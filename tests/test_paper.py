"""Live paper trading: a decision recorded at the instant, not replayed.

No network. The runner is driven with a synthetic near-pass frame.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import ClassVar

import pandas as pd
import pytest

from avo.core.types import Candidate
from experiments.kalshi_quant import paper_log
from experiments.kalshi_quant.paper_runner import run_pass

NOW = datetime(2026, 9, 17, 18, 0, tzinfo=timezone.utc)


def _frame(n=4, bid=0.70, spread=0.02, size=4000.0, at=None):
    at = at or NOW - timedelta(minutes=3)
    return pd.DataFrame([{
        "ticker": f"T{i}", "event_ticker": "E", "series_ticker": "S",
        "title": f"m{i}", "observed_at": at, "close_time": NOW + timedelta(hours=2),
        "yes_bid": bid, "yes_ask": bid + spread, "last_price": bid,
        "volume": 100.0, "open_interest": 50.0,
        "yes_bid_size": size, "yes_ask_size": size, "liquidity": 0.0,
        "status": "open", "price_level_structure": "linear_cent", "is_mve": False,
    } for i in range(n)])


class _Buyer:
    MANIFEST: ClassVar[dict] = {"candidate_id": "buyer"}
    def forecast(self, m, c):
        return min(m.implied_prob + 0.04, 0.99)


class _Abstainer:
    MANIFEST: ClassVar[dict] = {"candidate_id": "abstainer"}
    def forecast(self, m, c):
        return m.implied_prob


class _Exp:
    def __init__(self, mods): self.mods = mods
    def seed_candidates(self):
        return [Candidate(k, "kalshi_quant", 0, None, NOW, "x", "") for k in self.mods]
    def load_candidate(self, c): return self.mods[c.candidate_id]


def test_only_positions_are_logged(tmp_path):
    """A row is a claim that real money should move. An abstention is not one,
    and logging thousands of them would bury the handful that matter."""
    exp = _Exp({"buyer": _Buyer(), "abstainer": _Abstainer()})
    _, st = run_pass(_frame(4), _frame(0), now=NOW, root=tmp_path,
                     candidates=("buyer", "abstainer"), experiment=exp)
    df = paper_log.read(tmp_path)
    assert st["asked"] == 8 and st["acted"] == 4
    assert len(df) == 4 and set(df["candidate_id"]) == {"buyer"}
    assert df["acted"].all() and (df["side"] == "yes").all()


def test_the_quote_age_is_recorded_because_it_is_the_whole_point(tmp_path):
    """Replay scored entries at a median 46 minutes' staleness. Live, the age of
    the book you traded is the number that says whether the fill was real."""
    exp = _Exp({"buyer": _Buyer()})
    run_pass(_frame(1, at=NOW - timedelta(minutes=7)), _frame(0), now=NOW,
             root=tmp_path, candidates=("buyer",), experiment=exp)
    row = paper_log.read(tmp_path).iloc[0]
    assert row["quote_age_s"] == pytest.approx(420.0)
    assert row["fill_price_cents"] > row["yes_ask"] * 100, "fee must be charged"


def test_a_market_is_decided_once_per_candidate(tmp_path):
    """The first decision stands, exactly as it would if money had gone in."""
    exp = _Exp({"buyer": _Buyer()})
    run_pass(_frame(3), _frame(0), now=NOW, root=tmp_path,
             candidates=("buyer",), experiment=exp)
    _, st = run_pass(_frame(3), _frame(0), now=NOW + timedelta(minutes=15),
                     root=tmp_path, candidates=("buyer",), experiment=exp)
    assert st["acted"] == 0 and st["skipped_seen"] == 3
    assert len(paper_log.read(tmp_path)) == 3


def test_a_raising_candidate_is_counted_and_takes_no_position(tmp_path):
    class Bad:
        MANIFEST: ClassVar[dict] = {"candidate_id": "bad"}
        def forecast(self, m, c): raise ValueError("boom")

    exp = _Exp({"bad": Bad()})
    _, st = run_pass(_frame(2), _frame(0), now=NOW, root=tmp_path,
                     candidates=("bad",), experiment=exp)
    assert st["errors"] == 2 and st["acted"] == 0
    assert paper_log.read(tmp_path).empty


def test_settlement_only_counts_outcomes_after_the_decision(tmp_path):
    """The holdout here is per DECISION. A market that resolved before the
    decision was made cannot judge it."""
    exp = _Exp({"buyer": _Buyer()})
    run_pass(_frame(2), _frame(0), now=NOW, root=tmp_path,
             candidates=("buyer",), experiment=exp)
    d = tmp_path / "resolutions" / "date=2026-09-17"
    d.mkdir(parents=True)
    pd.DataFrame([
        {"ticker": "T0", "series_ticker": "S", "resolved_at": NOW + timedelta(hours=1),
         "outcome": 1, "settlement_value": 1.0},
        {"ticker": "T1", "series_ticker": "S", "resolved_at": NOW - timedelta(hours=1),
         "outcome": 0, "settlement_value": 0.0},
    ]).to_parquet(d / "r.parquet", index=False)

    s = paper_log.settled(tmp_path)
    assert list(s["ticker"]) == ["T0"]
    p = paper_log.pnl(s)
    assert len(p) == 1 and p.iloc[0]["pnl_per_contract"] > 0   # bought at .72, paid 1
    assert p.iloc[0]["pnl_dollars"] == pytest.approx(
        p.iloc[0]["pnl_per_contract"] * p.iloc[0]["contracts"])


def test_paper_candidates_is_a_deliberate_whitelist():
    """Running all 42 through paper trading would reproduce exactly the
    multiple-comparison problem the confirmation split exists to control."""
    from experiments.kalshi_quant.paper_runner import PAPER_CANDIDATES
    assert PAPER_CANDIDATES == ("unclimbed_favourite",)


def _ladder(n=6, event="KXNDX-26SEP18H1600", bid=0.70, at=None):
    """One event, n rungs -- the nested-ladder shape that broke the sizing."""
    at = at or NOW - timedelta(minutes=3)
    return pd.DataFrame([{
        "ticker": f"{event}-T{i}", "event_ticker": event, "series_ticker": "KXNDX",
        "title": f"above {29400 + i*10}", "observed_at": at,
        "close_time": NOW + timedelta(hours=2),
        "yes_bid": bid, "yes_ask": bid + 0.02, "last_price": bid,
        "volume": 100.0, "open_interest": 50.0,
        "yes_bid_size": 4000.0, "yes_ask_size": 4000.0, "liquidity": 0.0,
        "status": "open", "price_level_structure": "linear_cent", "is_mve": False,
    } for i in range(n)])


def test_exposure_is_capped_per_event_not_per_market(tmp_path):
    """Sixteen rungs of one Nasdaq ladder settle on ONE index close. Sizing per
    market made that 1,600 contracts on a single outcome while looking like
    sixteen diversified positions."""
    exp = _Exp({"buyer": _Buyer()})
    _, st = run_pass(_ladder(6), _frame(0), now=NOW, root=tmp_path,
                     candidates=("buyer",), experiment=exp,
                     desired_contracts=100, max_contracts_per_event=100)
    df = paper_log.read(tmp_path)
    assert st["acted"] == 6, "every rung is still a recorded decision"
    assert df["contracts"].sum() == 100, "one event, one position's worth of risk"
    assert st["capped"] == 5
    assert df[df.contracts == 0]["fill_reason"].str.contains("event exposure").all()


def test_the_cap_holds_across_passes_as_rungs_enter_the_window(tmp_path):
    """A ladder does not arrive all at once; later rungs must not top it up."""
    exp = _Exp({"buyer": _Buyer()})
    run_pass(_ladder(2), _frame(0), now=NOW, root=tmp_path,
             candidates=("buyer",), experiment=exp, max_contracts_per_event=100)
    run_pass(_ladder(6), _frame(0), now=NOW + timedelta(minutes=15), root=tmp_path,
             candidates=("buyer",), experiment=exp, max_contracts_per_event=100)
    assert paper_log.read(tmp_path)["contracts"].sum() == 100


def test_separate_events_are_sized_independently(tmp_path):
    exp = _Exp({"buyer": _Buyer()})
    two = pd.concat([_ladder(3, "EV-A"), _ladder(3, "EV-B")], ignore_index=True)
    run_pass(two, _frame(0), now=NOW, root=tmp_path, candidates=("buyer",),
             experiment=exp, max_contracts_per_event=100)
    df = paper_log.read(tmp_path)
    assert df["contracts"].sum() == 200
    assert df.groupby("event_ticker")["contracts"].sum().tolist() == [100, 100]


def test_pnl_summary_clusters_rather_than_reporting_a_bare_mean():
    """The error I made reading paper results: 296 trades were not 296 bets.
    Unclustered +0.0860 [+0.0488,+0.1233]; by series [-0.0020,+0.1741]."""
    import pandas as pd

    rows = []
    for ev in range(4):                      # 4 events, 10 correlated rungs each
        won = ev < 3
        for _ in range(10):
            rows.append({"series_ticker": "S", "event_ticker": f"E{ev}",
                         "acted": True, "contracts": 100,
                         "pnl_per_contract": 0.28 if won else -0.72,
                         "pnl_dollars": 28.0 if won else -72.0})
    df = pd.DataFrame(rows)
    wide = paper_log.pnl_summary(df, "event_ticker")
    assert wide["n_trades"] == 40 and wide["n_clusters"] == 4
    naive_se = df.pnl_per_contract.std() / len(df) ** 0.5
    clustered_half = (wide["ci_hi"] - wide["ci_lo"]) / 2
    assert clustered_half > 1.96 * naive_se, "clustering must widen a ladder"


def test_two_appends_in_the_same_second_do_not_overwrite(tmp_path):
    """append() named files HHMMSS.parquet, so two passes inside one second
    silently destroyed the earlier one. Found 2026-09-20 when a cross-pass
    exposure test reported zero contracts after logging a hundred."""
    a = paper_log.append([paper_log.row("c", _mk(), 0.8, NOW)], tmp_path)
    b = paper_log.append([paper_log.row("c", _mk("T2"), 0.8, NOW)], tmp_path)
    assert a != b, "same-second writes must not collide"
    assert len(paper_log.read(tmp_path)) == 2


def _mk(ticker="T1"):
    from experiments.kalshi_quant.types import MarketSnapshot
    return MarketSnapshot(ticker, "E", "S", "t", NOW - timedelta(minutes=2),
                          NOW + timedelta(hours=1), 0.70, 0.72, None, 100.0, 50.0,
                          yes_bid_size=4000.0, yes_ask_size=4000.0)
