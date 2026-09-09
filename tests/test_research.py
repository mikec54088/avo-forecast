"""kalshi_research: forward-only evaluation from a forecast log.

No network anywhere here. The runner is driven with a synthetic near-pass
snapshot and a stub researcher; scoring is driven with a synthetic log and
synthetic resolutions.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import ClassVar

import pandas as pd
import pytest

from avo.core import registry
from avo.core.validate import validate_candidate_file
from experiments.kalshi_research import forecast_log
from experiments.kalshi_research.experiment import load_forecast_observations
from experiments.kalshi_research.researcher import NullResearcher, StubResearcher
from experiments.kalshi_research.runner import run_pass, select_markets
from experiments.kalshi_research.types import ResearchBudgetExceeded, ResearchContext

REPO = Path(__file__).resolve().parents[1]
EXP = registry.load("kalshi_research")
NOW = datetime(2026, 9, 9, 20, 0, tzinfo=timezone.utc)


def _snapshot(n=6, close_in_h=0.5, spread=0.02, mve=False):
    rows = []
    for i in range(n):
        bid = 0.30 + 0.1 * i
        rows.append({
            "ticker": f"KXTEST-T{i}", "event_ticker": f"KXTEST-E{i // 2}",
            "series_ticker": "KXTEST", "title": f"test market {i}",
            "observed_at": NOW - timedelta(minutes=3),
            "close_time": NOW + timedelta(hours=close_in_h),
            "yes_bid": bid, "yes_ask": bid + spread, "last_price": bid,
            "volume": 100.0, "open_interest": 50.0,
            "yes_bid_size": 40.0, "yes_ask_size": 60.0, "liquidity": 0.0,
            "status": "open", "price_level_structure": "linear_cent", "is_mve": mve,
        })
    return pd.DataFrame(rows)


# ------------------------------------------------------------- the context

def test_research_is_metered_and_refuses_past_the_budget():
    stub = StubResearcher(answers={"q1": "rain expected"})
    ctx = ResearchContext(now=NOW, researcher=stub, budget=2)
    assert ctx.research("q1") == "rain expected"
    ctx.research("q2")
    with pytest.raises(ResearchBudgetExceeded):
        ctx.research("q3")
    assert ctx.calls == 2 and stub.calls == 2


# --------------------------------------------------------------- selection

def test_select_markets_filters_to_fillable_near_close_non_mve():
    df = pd.concat([
        _snapshot(3),                                  # keep
        _snapshot(2, close_in_h=5.0),                  # outside the window
        _snapshot(2, spread=0.20),                     # too wide to fill
        _snapshot(2, mve=True),                        # parlay combos
    ], ignore_index=True)
    out = select_markets(df, NOW, max_close_hours=1.0, max_markets=100)
    assert len(out) == 3
    assert select_markets(df, NOW, 1.0, max_markets=2).shape[0] == 2


# ------------------------------------------------------------------ runner

def test_a_pass_logs_every_candidate_on_every_selected_market(tmp_path):
    path, st = run_pass(NullResearcher(), _snapshot(4), now=NOW, root=tmp_path,
                        experiment=EXP)
    df = forecast_log.read(tmp_path)
    n_cands = len(EXP.seed_candidates())
    assert st["markets"] == 4 and st["forecasts"] == 4 * n_cands
    assert len(df) == 4 * n_cands and path is not None
    assert set(df["candidate_id"]) == {c.candidate_id for c in EXP.seed_candidates()}
    assert (df["forecast_at"] == pd.Timestamp(NOW)).all()


def test_a_market_is_forecast_at_most_once_per_candidate(tmp_path):
    """Research is spent once; the first forecast in the window is the scored one."""
    run_pass(NullResearcher(), _snapshot(4), now=NOW, root=tmp_path, experiment=EXP)
    _, st = run_pass(NullResearcher(), _snapshot(4), now=NOW + timedelta(minutes=15),
                     root=tmp_path, experiment=EXP)
    assert st["forecasts"] == 0 and st["skipped_seen"] == 4 * len(EXP.seed_candidates())


def test_a_raising_candidate_is_logged_as_the_market_not_dropped(tmp_path):
    class Bad:
        MANIFEST: ClassVar[dict] = {"candidate_id": "bad"}
        def forecast(self, m, c):
            raise ValueError("boom")

    class FakeExp:
        def seed_candidates(self):
            from avo.core.types import Candidate
            return [Candidate("bad", "kalshi_research", 0, None, NOW, "x", "")]
        def load_candidate(self, c):
            return Bad()

    _, st = run_pass(NullResearcher(), _snapshot(2), now=NOW, root=tmp_path,
                     experiment=FakeExp())
    df = forecast_log.read(tmp_path)
    assert st["errors"] == 2
    assert (df["forecast"] == (df["yes_bid"] + df["yes_ask"]) / 2).all()
    assert df["error"].str.contains("boom").all()


# ----------------------------------------------------------------- scoring

def _resolutions(tmp_path, outcomes: dict[str, tuple[datetime, int]]):
    d = tmp_path / "resolutions" / "date=2026-09-09"
    d.mkdir(parents=True)
    pd.DataFrame([{"ticker": t, "series_ticker": "KXTEST", "resolved_at": ra,
                   "outcome": o, "settlement_value": float(o)}
                  for t, (ra, o) in outcomes.items()]).to_parquet(d / "r.parquet", index=False)


def test_only_outcomes_resolved_after_the_forecast_count(tmp_path):
    """The holdout for this experiment is per forecast, not per candidate."""
    run_pass(NullResearcher(), _snapshot(3), now=NOW, root=tmp_path, experiment=EXP)
    _resolutions(tmp_path, {
        "KXTEST-T0": (NOW + timedelta(hours=1), 1),      # after: counts
        "KXTEST-T1": (NOW - timedelta(minutes=1), 0),    # BEFORE the forecast: excluded
        # T2 unresolved: excluded
    })
    obs = load_forecast_observations(tmp_path, tmp_path)
    n_cands = len(EXP.seed_candidates())
    assert len(obs) == 1 * n_cands
    assert {o.entry.ticker for o in obs} == {"KXTEST-T0"}


def test_the_control_scores_exactly_zero_and_takes_no_position(tmp_path):
    run_pass(NullResearcher(), _snapshot(6), now=NOW, root=tmp_path, experiment=EXP)
    _resolutions(tmp_path, {f"KXTEST-T{i}": (NOW + timedelta(hours=1), i % 2)
                            for i in range(6)})
    obs = load_forecast_observations(tmp_path, tmp_path)
    c = next(x for x in EXP.seed_candidates() if x.candidate_id == "research_market")
    s = EXP.score(c, EXP.load_candidate(c), entries=obs)
    assert s.n_observations == 6
    assert s.primary == 0.0
    assert s.secondary["pnl_n_fills"] == 0.0
    assert s.secondary["research_calls"] == 0.0


def test_subsets_split_by_series_with_the_shared_salt(tmp_path):
    from avo.core.selection import is_confirmation_group
    run_pass(NullResearcher(), _snapshot(2), now=NOW, root=tmp_path, experiment=EXP)
    _resolutions(tmp_path, {"KXTEST-T0": (NOW + timedelta(hours=1), 1),
                            "KXTEST-T1": (NOW + timedelta(hours=1), 0)})
    obs = load_forecast_observations(tmp_path, tmp_path)
    c = EXP.seed_candidates()[0]
    side = "confirmation" if is_confirmation_group("KXTEST") else "selection"
    other = "selection" if side == "confirmation" else "confirmation"
    assert EXP.score(c, EXP.load_candidate(c), entries=obs, subset=side).n_observations == 2
    assert EXP.score(c, EXP.load_candidate(c), entries=obs, subset=other).n_observations == 0


# ------------------------------------------------------------- validation

def test_probe_accepts_the_control():
    v = validate_candidate_file(
        str(REPO / "experiments/kalshi_research/candidates/research_market.py"),
        EXP.validation_probe(), REPO)
    assert v.accepted, v.problems


def test_probe_rejects_a_candidate_that_researches_every_market(tmp_path):
    src = '''
MANIFEST = {"candidate_id": "spender", "created_at": "2026-09-09T00:00:00+00:00"}
def forecast(market, context):
    context.research(market.title)
    return market.implied_prob
'''
    p = tmp_path / "spender.py"
    p.write_text(src)
    v = validate_candidate_file(str(p), EXP.validation_probe(), REPO)
    assert not v.accepted and any("every market" in x for x in v.problems)


def test_probe_rejects_a_candidate_that_blows_its_budget(tmp_path):
    src = '''
MANIFEST = {"candidate_id": "greedy", "created_at": "2026-09-09T00:00:00+00:00"}
def forecast(market, context):
    if market.implied_prob > 0.9:
        for _ in range(10):
            context.research("x")
    return market.implied_prob
'''
    p = tmp_path / "greedy.py"
    p.write_text(src)
    v = validate_candidate_file(str(p), EXP.validation_probe(), REPO)
    assert not v.accepted and any("raised" in x for x in v.problems)


def test_research_prompt_says_it_cannot_be_backtested():
    text = EXP.variation_prompt(None, [])
    assert "cannot be backtested" in text and "COST CONSTRAINT" in text
