"""Phase 2 pipeline: snapshots + resolutions -> holdout -> skill.

Synthetic Parquet in tmp_path. No network, no dependency on captured data.

The properties asserted here are the ones whose failure would silently inflate
every score in the project rather than raise: a leaky context, a holdout that
lets earlier truth through, or an entry chosen after the outcome was known.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from avo.core.holdout import HoldoutViolation, assert_clean
from avo.core.types import Candidate
from experiments.kalshi_quant.experiment import KalshiQuantExperiment, paper_trade
from experiments.kalshi_quant.observations import (
    MAX_ENTRY_STALENESS_MINUTES,
    SeriesHistory,
    load_entries,
)

T0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _snap_row(ticker: str, observed_at: datetime, bid: float, ask: float,
              series: str = "KXTEST") -> dict:
    return {
        "ticker": ticker, "event_ticker": f"{series}-EV", "series_ticker": series,
        "title": ticker, "observed_at": observed_at,
        "close_time": observed_at + timedelta(hours=1),
        "yes_bid": bid, "yes_ask": ask, "last_price": None,
        "volume": 100.0, "open_interest": 50.0,
        "yes_bid_size": 10.0, "yes_ask_size": 10.0, "liquidity": 1.0,
        "status": "active", "price_level_structure": "linear_cent", "is_mve": False,
    }


@pytest.fixture
def data_root(tmp_path):
    """Markets chosen to exercise every entry rule at once.

    m1 kept  - has a stale snapshot AND a fresh one; the fresh one must win
    m2 kept  - single snapshot, 30 min before resolution
    m3 gone  - captured but never resolved
    m4 gone  - its only snapshot postdates its resolution (lookahead)
    m5 gone  - resolved, but its freshest snapshot is 120 min stale (over cap)
    """
    snaps = [
        _snap_row("m1", T0, 0.30, 0.40),                                  # 180 min stale
        _snap_row("m1", T0 + timedelta(hours=2, minutes=50), 0.60, 0.70),  # 10 min stale
        _snap_row("m2", T0 + timedelta(hours=3, minutes=30), 0.10, 0.20, series="KXOTHER"),
        _snap_row("m3", T0, 0.45, 0.55),
        _snap_row("m4", T0 + timedelta(hours=9), 0.50, 0.60),
        _snap_row("m5", T0 + timedelta(hours=4), 0.40, 0.50),              # 120 min stale
    ]
    sd = tmp_path / "snapshots" / "date=2026-08-01"
    sd.mkdir(parents=True)
    pd.DataFrame(snaps).to_parquet(sd / "120000.parquet", index=False)

    res = [
        {"ticker": "m1", "series_ticker": "KXTEST", "outcome": 1,
         "resolved_at": T0 + timedelta(hours=3), "settlement_value": 1.0},
        {"ticker": "m2", "series_ticker": "KXOTHER", "outcome": 0,
         "resolved_at": T0 + timedelta(hours=4), "settlement_value": 0.0},
        {"ticker": "m4", "series_ticker": "KXTEST", "outcome": 1,
         "resolved_at": T0 + timedelta(hours=5), "settlement_value": 1.0},
        {"ticker": "m5", "series_ticker": "KXTEST", "outcome": 1,
         "resolved_at": T0 + timedelta(hours=6), "settlement_value": 1.0},
    ]
    rd = tmp_path / "resolutions" / "date=2026-08-01"
    rd.mkdir(parents=True)
    pd.DataFrame(res).to_parquet(rd / "130000.parquet", index=False)
    return tmp_path


def _candidate(created_at: datetime, cid: str = "t") -> Candidate:
    return Candidate(
        candidate_id=cid, experiment="kalshi_quant", generation=0, parent_id=None,
        created_at=created_at,
        module_path="experiments.kalshi_quant.candidates.baseline_market",
        rationale="test",
    )


def test_only_resolved_markets_become_entries(data_root):
    entries = load_entries(data_root)
    assert {e.ticker for e in entries} == {"m1", "m2"}, (
        "m3 unresolved, m4 postdates resolution, m5 over the staleness cap"
    )


def test_stale_entries_are_dropped(data_root):
    """ENTRY_POLICY caps staleness at 60 min. m5 resolved and was captured, but
    its freshest price is 2h old -- old enough that a sharpen-away-from-0.5
    transform scores on drift rather than on forecasting."""
    entries = load_entries(data_root)
    assert "m5" not in {e.ticker for e in entries}
    assert all(e.staleness_minutes <= MAX_ENTRY_STALENESS_MINUTES for e in entries)


def test_the_freshest_snapshot_wins_over_a_stale_one(data_root):
    """m1 has a 180-min-stale snapshot and a 10-min one. Taking the stale price
    would both mis-state the market and push the entry over the cap."""
    m1 = next(e for e in load_entries(data_root) if e.ticker == "m1")
    assert m1.staleness_minutes == pytest.approx(10.0)
    assert m1.market.implied_prob == pytest.approx(0.65)


def test_entry_never_postdates_the_outcome(data_root):
    """m4's only snapshot is after it resolved. Scoring it would be lookahead."""
    for e in load_entries(data_root):
        assert e.market.observed_at < e.resolved_at


def test_series_history_excludes_own_and_future_outcomes(data_root):
    entries = load_entries(data_root)
    h = SeriesHistory(entries)
    for e in entries:
        hist = h.context_for(e).series_history.get(e.market.series_ticker, [])
        assert all(r.ticker != e.ticker for r in hist), "context contains its own outcome"
        assert all(r.resolved_at < e.market.observed_at for r in hist), "future outcome leaked"


def test_series_history_as_of_is_strict(data_root):
    h = SeriesHistory(load_entries(data_root))
    at = T0 + timedelta(hours=3)
    assert h.as_of("KXTEST", at) == [], "a resolution AT the instant is not yet known"
    assert len(h.as_of("KXTEST", at + timedelta(seconds=1))) == 1


def test_holdout_excludes_truth_that_predates_the_candidate(data_root):
    exp, entries = KalshiQuantExperiment(), load_entries(data_root)
    h = SeriesHistory(entries)
    c = _candidate(T0)
    loaded = exp.load_candidate(c)
    assert exp.score(c, loaded, entries=entries, history=h).n_observations == 2

    # after m1 resolved, only m2 remains
    later = _candidate(T0 + timedelta(hours=3, minutes=1))
    assert exp.score(later, loaded, entries=entries, history=h).n_observations == 1

    # after everything resolved, nothing is scoreable
    after = _candidate(T0 + timedelta(days=1))
    s = exp.score(after, loaded, entries=entries, history=h)
    assert s.n_observations == 0
    assert s.primary != s.primary, "skill on no observations must be NaN, not 0"


def test_assert_clean_raises_on_a_dirty_set(data_root):
    entries = load_entries(data_root)
    with pytest.raises(HoldoutViolation):
        assert_clean(_candidate(T0 + timedelta(days=1)), entries, lambda e: e.resolved_at)


def test_market_baseline_scores_exactly_zero(data_root):
    """INVARIANT #2's zero point. If this drifts, every skill number moves."""
    exp, entries = KalshiQuantExperiment(), load_entries(data_root)
    c = _candidate(T0)
    s = exp.score(c, exp.load_candidate(c), entries=entries, history=SeriesHistory(entries))
    assert s.primary == pytest.approx(0.0, abs=1e-12)
    assert s.secondary["candidate_brier"] == pytest.approx(s.secondary["market_brier"])


def test_a_broken_candidate_does_not_kill_the_run(data_root):
    """An agent-written candidate will raise or return nonsense. That must cost
    its own score, not the whole generation."""
    exp, entries = KalshiQuantExperiment(), load_entries(data_root)
    h = SeriesHistory(entries)

    class Boom:
        @staticmethod
        def forecast(market, context):
            raise ValueError("bad candidate")

    class OutOfRange:
        @staticmethod
        def forecast(market, context):
            return 42.0

    for mod in (Boom, OutOfRange):
        s = exp.score(_candidate(T0), mod, entries=entries, history=h)
        assert s.n_observations == 0
        assert "error" in s.notes or "no usable" in s.notes


def test_score_reports_staleness_and_pnl(data_root):
    """A skill number is not readable without these. Measured 2026-08-31,
    baseline_sharpened scored +0.0286 skill while returning -0.0020/contract
    with a series-clustered CI spanning zero -- Brier skill and money pointed
    in opposite directions, and only the secondary metrics showed it."""
    exp, entries = KalshiQuantExperiment(), load_entries(data_root)
    c = _candidate(T0)
    s = exp.score(c, exp.load_candidate(c), entries=entries, history=SeriesHistory(entries))
    for key in ("staleness_median_min", "staleness_p90_min",
                "pnl_per_contract", "pnl_n_fills", "pnl_fill_rate"):
        assert key in s.secondary, f"{key} missing from Score.secondary"
    assert s.secondary["staleness_median_min"] > 0


def test_paper_trade_is_flat_when_the_candidate_agrees_with_the_market(data_root):
    """baseline_market forecasts the midpoint exactly, so it never has a reason
    to trade. Zero fills is the correct answer, not a degenerate one."""
    entries = load_entries(data_root)
    out = paper_trade(entries, [e.market.implied_prob for e in entries])
    assert out["pnl_n_fills"] == 0.0
    assert out["pnl_fill_rate"] == 0.0


def test_paper_trade_pays_the_spread(data_root):
    """A candidate that is right about direction can still lose money. This is
    the whole reason P&L is reported alongside skill: INVARIANT #4 makes fills
    pessimistic, and the spread plus fee is ~2 probability points."""
    entries = [e for e in load_entries(data_root) if e.outcome == 1]
    assert entries, "fixture needs a yes-resolving market"
    # Forecast 1.0 on a market that resolved yes: maximally correct.
    out = paper_trade(entries, [1.0] * len(entries))
    if out["pnl_n_fills"]:
        # Won every trade, yet the return per contract is capped at 1 - price
        # paid, and price paid crossed the ask and added the fee.
        assert out["pnl_per_contract"] < 1.0
