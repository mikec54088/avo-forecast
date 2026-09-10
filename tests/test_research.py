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


def test_an_abstaining_candidate_is_deferred_until_the_final_window(tmp_path):
    """Game markets close 2-3 days after the game. Logging an early abstention
    as the one forecast would make every research candidate read as the
    control; it is asked again on later passes instead."""
    early = _snapshot(3, close_in_h=48.0)
    _, st = run_pass(NullResearcher(), early, now=NOW, root=tmp_path, experiment=EXP,
                     final_hours=1.0)
    n_cands = len(EXP.seed_candidates())
    assert st["asked"] == 3 * n_cands and st["deferred"] == 3 * n_cands
    assert st["forecasts"] == 0 and forecast_log.read(tmp_path).empty
    late = _snapshot(3, close_in_h=0.5)
    _, st = run_pass(NullResearcher(), late, now=NOW, root=tmp_path, experiment=EXP)
    assert st["forecasts"] == 3 * n_cands and st["deferred"] == 0


def test_a_candidate_that_acts_early_is_done_and_not_asked_again(tmp_path):
    from avo.core.types import Candidate

    class Acts:
        MANIFEST: ClassVar[dict] = {"candidate_id": "acts"}
        def forecast(self, m, c):
            return min(m.implied_prob + 0.05, 0.99)

    class FakeExp:
        def seed_candidates(self):
            return [Candidate("acts", "kalshi_research", 0, None, NOW, "x", "")]
        def load_candidate(self, c):
            return Acts()

    early = _snapshot(2, close_in_h=48.0)
    _, st = run_pass(NullResearcher(), early, now=NOW, root=tmp_path, experiment=FakeExp())
    assert st["forecasts"] == 2 and st["acted"] == 2 and st["deferred"] == 0
    _, st = run_pass(NullResearcher(), early, now=NOW + timedelta(hours=1),
                     root=tmp_path, experiment=FakeExp())
    assert st["skipped_seen"] == 2 and st["forecasts"] == 0


def test_spending_research_counts_as_acting_even_if_the_forecast_is_the_market(tmp_path):
    """Research spent is a decision made; the log must hold it exactly once."""
    from avo.core.types import Candidate

    class Spends:
        MANIFEST: ClassVar[dict] = {"candidate_id": "spends"}
        def forecast(self, m, c):
            c.research("anything")
            return m.implied_prob

    class FakeExp:
        def seed_candidates(self):
            return [Candidate("spends", "kalshi_research", 0, None, NOW, "x", "")]
        def load_candidate(self, c):
            return Spends()

    stub = StubResearcher()
    _, st = run_pass(stub, _snapshot(2, close_in_h=48.0), now=NOW, root=tmp_path,
                     experiment=FakeExp())
    assert st["forecasts"] == 2 and st["research_calls"] == 2 and stub.calls == 2
    df = forecast_log.read(tmp_path)
    assert (df["research_calls"] == 1).all()


def test_latest_snapshot_distinguishes_full_from_near(tmp_path):
    from experiments.kalshi_research.runner import latest_snapshot
    d = tmp_path / "date=2026-09-09"; d.mkdir()
    (d / "100000.parquet").write_bytes(b"")
    (d / "101500-near.parquet").write_bytes(b"")
    assert latest_snapshot("full", tmp_path).name == "100000.parquet"
    assert latest_snapshot("near", tmp_path).name == "101500-near.parquet"


def test_a_failed_research_call_defers_the_market_instead_of_consuming_it(tmp_path):
    """2026-09-10: a live call returned exit 1 whose BODY was "You're out of
    usage credits". A keyword candidate reads that as "no news" and abstains,
    so without this the market would be logged as a forecast identical to the
    control's and never asked again -- a broken channel wearing the face of a
    result."""
    from avo.core.types import Candidate
    from experiments.kalshi_research.types import ResearchResult

    class Broken:
        name = "broken"
        def research(self, query):
            return ResearchResult(query, "You're out of usage credits.", 0.1,
                                  error="exit 1: ")

    class Researches:
        MANIFEST: ClassVar[dict] = {"candidate_id": "r"}
        def forecast(self, m, c):
            c.research("anything")
            return m.implied_prob

    class FakeExp:
        def seed_candidates(self):
            return [Candidate("r", "kalshi_research", 0, None, NOW, "x", "")]
        def load_candidate(self, c):
            return Researches()

    _, st = run_pass(Broken(), _snapshot(3, close_in_h=48.0), now=NOW,
                     root=tmp_path, experiment=FakeExp())
    assert st["research_failed"] == 3 and st["deferred"] == 3
    assert st["forecasts"] == 0 and forecast_log.read(tmp_path).empty


def test_a_failed_research_call_inside_the_final_window_is_logged_with_its_error(tmp_path):
    """Deferring forever would silently drop the market, so in the final window
    it is logged -- and the log says why it is worthless."""
    from avo.core.types import Candidate
    from experiments.kalshi_research.types import ResearchResult

    class Broken:
        name = "broken"
        def research(self, query):
            return ResearchResult(query, "", 0.1, error="exit 1: out of credits")

    class Researches:
        MANIFEST: ClassVar[dict] = {"candidate_id": "r"}
        def forecast(self, m, c):
            c.research("anything")
            return m.implied_prob

    class FakeExp:
        def seed_candidates(self):
            return [Candidate("r", "kalshi_research", 0, None, NOW, "x", "")]
        def load_candidate(self, c):
            return Researches()

    _, st = run_pass(Broken(), _snapshot(2, close_in_h=0.5), now=NOW,
                     root=tmp_path, experiment=FakeExp())
    df = forecast_log.read(tmp_path)
    assert st["forecasts"] == 2 and len(df) == 2
    assert df["research_error"].str.contains("out of credits").all()


def test_a_successful_research_call_is_not_flagged_as_failed(tmp_path):
    from avo.core.types import Candidate

    class Researches:
        MANIFEST: ClassVar[dict] = {"candidate_id": "r"}
        def forecast(self, m, c):
            c.research("anything")
            return m.implied_prob

    class FakeExp:
        def seed_candidates(self):
            return [Candidate("r", "kalshi_research", 0, None, NOW, "x", "")]
        def load_candidate(self, c):
            return Researches()

    _, st = run_pass(StubResearcher(), _snapshot(2, close_in_h=48.0), now=NOW,
                     root=tmp_path, experiment=FakeExp())
    assert st["research_failed"] == 0 and st["forecasts"] == 2
    assert (forecast_log.read(tmp_path)["research_error"] == "").all()


def test_scoring_excludes_rows_whose_research_failed(tmp_path):
    """A broken-channel row holds the market's own number. Counting it would
    drag a research candidate toward the control and look like a result."""
    from avo.core.types import Candidate
    from experiments.kalshi_research.types import ResearchResult

    class Broken:
        name = "broken"
        def research(self, query):
            return ResearchResult(query, "", 0.1, error="exit 1: out of credits")

    class Researches:
        MANIFEST: ClassVar[dict] = {"candidate_id": "r"}
        def forecast(self, m, c):
            c.research("anything")
            return m.implied_prob

    class FakeExp:
        def seed_candidates(self):
            return [Candidate("r", "kalshi_research", 0, None, NOW, "x", "")]
        def load_candidate(self, c):
            return Researches()

    run_pass(Broken(), _snapshot(2, close_in_h=0.5), now=NOW, root=tmp_path,
             experiment=FakeExp())
    _resolutions(tmp_path, {f"KXTEST-T{i}": (NOW + timedelta(hours=1), 1) for i in range(2)})
    obs = load_forecast_observations(tmp_path, tmp_path)
    assert len(obs) == 2 and all(o.research_error for o in obs)
    c = Candidate("r", "kalshi_research", 0, None, NOW, "x", "")
    s = FakeExp().load_candidate(c)
    from avo.core import registry
    score = registry.load("kalshi_research").score(c, s, entries=obs)
    assert score.n_observations == 0, "broken-channel rows must not be scored"


def test_a_research_model_must_be_pinned():
    """The CLI default is a user setting that can change under the experiment.
    It was Fable on 2026-09-10, its credits were out, and every call returned
    the credit notice as its TEXT -- which a keyword candidate reads as "no
    news". INVARIANT #7 applies to the researcher as to the generating backend."""
    from experiments.kalshi_research.researcher import (
        DEFAULT_RESEARCH_MODEL,
        ClaudeResearcher,
        make,
    )
    with pytest.raises(ValueError, match="explicit model"):
        ClaudeResearcher()
    assert make("claude").name.startswith(f"claude:{DEFAULT_RESEARCH_MODEL}:")
    assert make("claude", "claude-opus-5").name.startswith("claude:claude-opus-5:")


def test_the_model_is_recorded_on_every_logged_forecast(tmp_path):
    """Forecasts researched by different models are not comparable, so the log
    must say which one produced each row."""
    from experiments.kalshi_research.researcher import StubResearcher
    stub = StubResearcher(name="stub:pinned")
    run_pass(stub, _snapshot(2), now=NOW, root=tmp_path, experiment=EXP)
    assert (forecast_log.read(tmp_path)["researcher"] == "stub:pinned").all()


def test_the_claude_binary_is_resolved_not_assumed(monkeypatch):
    """A launchd job gets PATH=/usr/bin:/bin:/usr/sbin:/sbin, so which() finds
    nothing and a bare "claude" fails to exec. Measured 2026-09-10: every
    research call from the launchd runner failed this way while the identical
    call from a shell worked. Raise loudly -- a researcher that cannot run must
    not be mistaken for one that found nothing."""
    from experiments.kalshi_research import researcher as R

    monkeypatch.setattr(R.shutil, "which", lambda _: None)
    monkeypatch.setattr(R, "_CLAUDE_FALLBACKS", (Path("/nonexistent/claude"),))
    with pytest.raises(FileNotFoundError, match="minimal PATH"):
        R.claude_exe()

    monkeypatch.setattr(R, "_CLAUDE_FALLBACKS", (Path(__file__),))
    assert R.claude_exe() == __file__       # found via fallback, not PATH


def test_the_research_text_is_recorded_as_evidence(tmp_path):
    """Web search is not reproducible: a forecast whose input was not recorded
    can never be checked again. On 2026-09-10 the first four live forecasts all
    fired the same keyword gate and there was no way to tell a real injury
    report from the word "questionable" in a routine preview."""
    from avo.core.types import Candidate

    class Researches:
        MANIFEST: ClassVar[dict] = {"candidate_id": "r"}
        def forecast(self, m, c):
            c.research("is anyone hurt")
            return m.implied_prob * 0.9

    class FakeExp:
        def seed_candidates(self):
            return [Candidate("r", "kalshi_research", 0, None, NOW, "x", "")]
        def load_candidate(self, c):
            return Researches()

    run_pass(StubResearcher(default="Star player ruled out"), _snapshot(1),
             now=NOW, root=tmp_path, experiment=FakeExp())
    row = forecast_log.read(tmp_path).iloc[0]
    assert "is anyone hurt" in row["research_text"]
    assert "Star player ruled out" in row["research_text"]


# ---------------------------------------------------- the research candidate

def test_research_candidate_gates_research_on_the_game_date(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "inf", REPO / "experiments/kalshi_research/candidates/injury_news_favourite.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert mod.game_date("KXMLBGAME-26SEP081940PITCWS-PIT") == datetime(2026, 9, 8, tzinfo=timezone.utc)
    assert mod.game_date("KXNCAAFGAME-26SEP19FIUFAU-FIU") == datetime(2026, 9, 19, tzinfo=timezone.utc)
    assert mod.game_date("KXBTCD-26SEP0912") is not None    # date parses; series gate rejects it
    from experiments.kalshi_quant.types import MarketSnapshot
    def mk(ticker, series, mid=0.70, spread=0.02, title="Pittsburgh wins"):
        return MarketSnapshot(ticker, "E", series, title, NOW, NOW + timedelta(hours=60),
                              mid - spread / 2, mid + spread / 2, None, 100.0, 50.0)
    m = mk("KXMLBGAME-26SEP091940PITCWS-PIT", "KXMLBGAME")
    on_day = datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc)
    day_before = datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc)
    # the hash sample decides the final bit; test the gates that precede it
    assert not mod.wants_research(m, day_before)
    assert not mod.wants_research(mk("KXBTCD-26SEP0912-T1", "KXBTCD"), on_day)
    assert not mod.wants_research(mk("KXMLBGAME-26SEP091940PITCWS-TIE", "KXMLBGAME", title="Tie is the result"), on_day)
    assert not mod.wants_research(mk("KXMLBGAME-26SEP091940PITCWS-PIT", "KXMLBGAME", mid=0.30), on_day)
    assert not mod.wants_research(mk("KXMLBGAME-26SEP091940PITCWS-PIT", "KXMLBGAME", spread=0.10), on_day)
    # and a market that passes every gate researches exactly once, within budget
    sampled = next(t for t in (f"KXMLBGAME-26SEP09{i:04d}AAABBB-AAA" for i in range(1000))
                   if mod.wants_research(mk(t, "KXMLBGAME"), on_day))
    stub = StubResearcher(default="Starting pitcher scratched with injury")
    ctx = ResearchContext(now=on_day, researcher=stub)
    p = mod.forecast(mk(sampled, "KXMLBGAME"), ctx)
    assert stub.calls == 1 and p == pytest.approx(0.70 - 0.04)
    ctx2 = ResearchContext(now=on_day, researcher=StubResearcher())   # NOTHING FOUND
    assert mod.forecast(mk(sampled, "KXMLBGAME"), ctx2) == pytest.approx(0.70)


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
