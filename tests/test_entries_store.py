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

    # cold: falls back, and says so
    entries, _ = exp.scoring_inputs()
    assert entries, "fallback must still produce entries"
    assert "cold or stale" in capsys.readouterr().out

    # warm: uses the table, silently
    store.build(data_root)
    monkeypatch.setattr(store, "DATA_ROOT", data_root)
    exp2 = registry.load("kalshi_quant")
    cached, _ = exp2.scoring_inputs()
    assert {e.ticker for e in cached} == {e.ticker for e in entries}
    assert "cold or stale" not in capsys.readouterr().out
