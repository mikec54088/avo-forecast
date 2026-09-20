"""The materialised entries table must be indistinguishable from the raw rebuild.

A cache that returns ALMOST the same answer is worse than no cache: every score
in the project would shift by an amount nobody could attribute. These tests
compare the two paths field by field rather than by count.
"""
from __future__ import annotations

import json

from experiments.kalshi_quant import entries_store
from experiments.kalshi_quant.observations import load_entries


def _key(e):
    """Everything about an entry that scoring can see."""
    m = e.market
    return (
        m.ticker, m.event_ticker, m.series_ticker, m.title,
        m.observed_at, m.close_time, m.yes_bid, m.yes_ask, m.last_price,
        m.volume, m.open_interest, m.yes_bid_size, m.yes_ask_size,
        m.status, m.price_level_structure, m.is_mve,
        e.resolved_at, e.outcome,
        tuple((h.observed_at, h.yes_bid, h.yes_ask, h.volume, h.open_interest)
              for h in e.price_history),
        tuple(sorted(s.ticker for s in e.siblings)),
    )


def test_the_table_reproduces_the_raw_rebuild_exactly(data_root):
    """The whole contract. If these differ, every score silently moves."""
    raw = load_entries(data_root)
    assert raw, "fixture must produce entries"

    stats = entries_store.build(data_root)
    assert stats.added == len(raw)

    cached = entries_store.load(data_root)
    assert cached is not None
    assert {_key(e) for e in cached} == {_key(e) for e in raw}


def test_load_returns_None_on_a_cold_cache_not_an_empty_list(data_root):
    """"Not built yet" and "there are no entries" are different answers, and a
    caller that confuses them scores against nothing and reports success."""
    assert entries_store.load(data_root) is None


def test_a_changed_policy_forces_a_rebuild_rather_than_serving_stale_rows(data_root):
    """Every row depends on ENTRY_POLICY, MAX_ENTRY_STALENESS_MINUTES,
    MAX_PRICE_HISTORY and SIBLING_TOLERANCE_MIN. This project has twice been
    bitten by derived data going quietly wrong against changed logic."""
    entries_store.build(data_root)
    assert entries_store.load(data_root) is not None

    meta = data_root / "entries" / "meta.json"
    stale = json.loads(meta.read_text())
    stale["policy"]["max_price_history"] = 999
    meta.write_text(json.dumps(stale))

    assert entries_store.load(data_root) is None, "stale rows must not be served"
    stats = entries_store.build(data_root)
    assert stats.rebuilt and entries_store.load(data_root) is not None


def test_building_twice_adds_nothing_and_is_not_an_error(data_root):
    first = entries_store.build(data_root)
    second = entries_store.build(data_root)
    assert first.added > 0
    assert second.added == 0 and "up to date" in second.note, second.note
    assert len(entries_store.load(data_root)) == first.added


def test_an_incremental_build_only_adds_the_new_resolutions(data_root):
    """The daily case: a few thousand new rows against 51 million scanned."""
    import pandas as pd

    res_file = next((data_root / "resolutions").glob("date=*/*.parquet"))
    full = pd.read_parquet(res_file)
    m1 = full[full["ticker"] == "m1"]
    rest = full[full["ticker"] != "m1"]
    assert len(m1) == 1 and len(rest) >= 1

    rest.to_parquet(res_file, index=False)
    entries_store.build(data_root)
    before = len(entries_store.load(data_root))

    pd.concat([rest, m1]).to_parquet(res_file, index=False)
    stats = entries_store.build(data_root)
    assert stats.added == 1 and not stats.rebuilt, stats
    assert len(entries_store.load(data_root)) == before + 1


def test_resolutions_with_no_usable_entry_are_not_reconsidered_forever(data_root):
    """365,648 resolutions yield 155,348 entries: most resolved markets never
    produce one. Keying off entries alone would re-scan that growing majority
    on every single build."""
    entries_store.build(data_root)
    again = entries_store.build(data_root)
    assert again.added == 0 and "up to date" in again.note, again.note


def test_scoring_prefers_the_table_and_falls_back_when_it_is_cold(data_root, monkeypatch, capsys):
    """The fallback must work AND be visible. 10.9 GB / 36 min against 2.8 GB /
    10 s is not a difference to discover from a slow run."""
    from avo.core import registry
    from experiments.kalshi_quant import digest, observations
    from experiments.kalshi_quant import entries_store as store

    monkeypatch.setattr(observations, "DATA_ROOT", data_root)
    monkeypatch.setattr(store, "DATA_ROOT", data_root)
    monkeypatch.setattr(digest, "CACHE", data_root / "digest.json")
    exp = registry.load("kalshi_quant")

    # cold: falls back to the raw archive, and says so
    entries, _ = exp.scoring_inputs()
    assert not callable(entries), "cold path yields a materialised list"
    assert entries, "fallback must still produce entries"
    assert "cold or stale" in capsys.readouterr().out
    raw_tickers = {e.ticker for e in entries}

    # warm: yields a chunk FACTORY, not a list, and says nothing
    store.build(data_root)
    exp2 = registry.load("kalshi_quant")
    provider, hist = exp2.scoring_inputs()
    assert callable(provider), "warm path must stream, not materialise"
    assert hist is not None
    streamed = {e.ticker for chunk in provider() for e in chunk}
    assert streamed == raw_tickers
    # the factory is reusable: 42 candidates each need their own pass
    assert {e.ticker for chunk in provider() for e in chunk} == raw_tickers
    assert "cold or stale" not in capsys.readouterr().out


def test_streaming_scores_identically_to_holding_the_whole_list(data_root, monkeypatch):
    """The contract for the streaming path. Verified on the real archive too --
    six candidates, both subsets, every field to 12 dp -- but pinned here so a
    future change to chunking cannot quietly move a score."""
    from avo.core import registry
    from experiments.kalshi_quant import entries_store as store
    from experiments.kalshi_quant import observations

    monkeypatch.setattr(observations, "DATA_ROOT", data_root)
    monkeypatch.setattr(store, "DATA_ROOT", data_root)
    store.build(data_root)

    exp = registry.load("kalshi_quant")
    listed = store.load(data_root)
    hist = store.series_history(data_root)
    c = next(x for x in exp.seed_candidates() if x.candidate_id == "baseline_sharpened")
    mod = exp.load_candidate(c)

    def norm(v):
        # NaN != NaN, and a tiny fixture legitimately yields NaN intervals
        # (a bootstrap needs more than one cluster). Compare them as equal.
        return "nan" if isinstance(v, float) and v != v else v

    def fields(s):
        return (norm(s.primary), tuple(norm(x) for x in s.primary_ci),
                s.n_observations,
                tuple(sorted((k, norm(v)) for k, v in s.secondary.items())))

    for subset in ("all", "selection", "confirmation"):
        a = exp.score(c, mod, entries=listed, history=hist, subset=subset)
        b = exp.score(c, mod, chunks=store.iter_entries(data_root), history=hist,
                      subset=subset)
        assert fields(a) == fields(b), subset


def test_iter_entries_yields_nothing_on_a_cold_table(data_root):
    """Same rule as load() returning None: a caller must not read "not built"
    as "empty", or it scores against nothing and reports success."""
    from experiments.kalshi_quant import entries_store as store
    assert list(store.iter_entries(data_root)) == []


def test_the_fill_rule_has_exactly_one_implementation():
    """paper_trade and the streaming scorer both go through accumulate_fills.
    A second copy would be free to drift from INVARIANT #4, and the whole P&L
    gate rests on it."""
    import inspect

    from experiments.kalshi_quant import experiment as ex

    assert "accumulate_fills" in inspect.getsource(ex.paper_trade)
    assert "accumulate_fills" in inspect.getsource(ex.KalshiQuantExperiment.score)
    assert inspect.getsource(ex.accumulate_fills).count("simulate_fill") == 1


def test_the_cli_handles_a_chunk_factory_without_calling_len(data_root, monkeypatch, capsys):
    """scoring_inputs() returns a factory when the table is warm, and `len()`
    on a callable raises. Both `avo rank` and `avo evolve` printed an
    observation count; the live rank died on exactly this."""
    import inspect

    from avo import cli

    src = inspect.getsource(cli)
    guarded = "sum(len(c) for c in entries()) if callable(entries) else len(entries)"
    assert src.count(guarded) == 2, "rank and evolve must both guard the count"
    # every len(entries) must sit inside that guard, never bare
    assert src.count("len(entries)") == src.count(guarded), "a bare len() on a factory raises"
