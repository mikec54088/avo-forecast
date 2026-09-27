"""The local research engine and its candidate. No network: retrieval and the
model are replaced with fakes, so these test the contract, not the web."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
import pytest

from avo.core import registry
from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research import forecast_log
from experiments.kalshi_research import local_research as lr
from experiments.kalshi_research.candidates import local_news_favourite as lnf
from experiments.kalshi_research.researcher import StubResearcher
from experiments.kalshi_research.runner import run_pass
from experiments.kalshi_research.types import ResearchContext

EXP = registry.load("kalshi_research")
NOW = datetime(2026, 9, 26, 18, 0, tzinfo=UTC)
TICKER = "KXMLBGAME-26SEP261910CWSCHC-CHC"
QUERY = f"Has anything dated today changed the expected outcome: Chicago C wins ({TICKER})?"


def _snap() -> pd.DataFrame:
    base = {"event_ticker": "KXMLBGAME-26SEP261910CWSCHC", "series_ticker": "KXMLBGAME",
            "observed_at": NOW - timedelta(minutes=3), "close_time": NOW + timedelta(days=2),
            "yes_bid": 0.60, "yes_ask": 0.62, "last_price": 0.61, "volume": 100.0,
            "open_interest": 50.0, "yes_bid_size": 40.0, "yes_ask_size": 60.0,
            "liquidity": 0.0, "status": "open", "price_level_structure": "linear_cent",
            "is_mve": False}
    return pd.DataFrame([{**base, "ticker": TICKER, "title": "Chicago C wins"},
                         {**base, "ticker": TICKER[:-3] + "CWS", "title": "Chicago WS wins",
                          "yes_bid": 0.38, "yes_ask": 0.40}])


@pytest.fixture
def researcher(monkeypatch, tmp_path):
    monkeypatch.setattr(lr, "model_digest", lambda: "abc123")
    r = lr.LocalResearcher(evidence_root=tmp_path)
    r.bind(_snap())
    return r


def _bundle(state: str = "EVIDENCE_AVAILABLE") -> dict[str, Any]:
    items = [] if state != "EVIDENCE_AVAILABLE" else [
        {"id": "S1", "published": "2026-09-26T15:00:00+00:00", "publisher": "RotoWire",
         "headline": "Seiya Suzuki Injury: Out of lineup", "side": "Chicago C"}]
    return {"id": f"{TICKER}@x", "asked_at": NOW.isoformat(), "state": state, "items": items,
            "errors": ["SEARCH_FAILED q: URLError"] if state == "SEARCH_FAILED" else []}


# ------------------------------------------------------------- the researcher

def test_the_name_pins_model_digest_and_pipeline(researcher):
    assert researcher.name == f"local:{lr.MODEL}@abc123:{lr.PIPELINE_VERSION}"


def test_the_market_and_its_opponent_come_from_the_bound_snapshot(researcher):
    m = researcher._market(QUERY, NOW)
    assert (m.this_side, m.opponent, m.series) == ("Chicago C", "Chicago WS", "KXMLBGAME")


@pytest.mark.parametrize("token", ["THIS_SIDE", "OTHER_SIDE", "BOTH", "NONE"])
def test_a_judgement_becomes_a_verdict_token(researcher, monkeypatch, token):
    monkeypatch.setattr(lr, "retrieve", lambda m: _bundle())
    monkeypatch.setattr(lr, "judge", lambda m, b: (token, ["S1"], "why"))
    r = researcher.research(QUERY)
    assert not r.error and r.text.startswith(f"VERDICT: {token}\n")
    assert f"[bundle {TICKER}@x]" in r.text


def test_a_failed_search_is_an_error_never_none(researcher, monkeypatch):
    monkeypatch.setattr(lr, "retrieve", lambda m: _bundle("SEARCH_FAILED"))
    r = researcher.research(QUERY)
    assert r.error.startswith("local: SEARCH_FAILED") and r.text == ""


def test_no_results_is_a_real_none(researcher, monkeypatch):
    monkeypatch.setattr(lr, "retrieve", lambda m: _bundle("NO_RELEVANT_RESULTS"))
    r = researcher.research(QUERY)
    assert not r.error and r.text.startswith("VERDICT: NONE")


def test_a_model_failure_is_an_error_never_none(researcher, monkeypatch):
    monkeypatch.setattr(lr, "retrieve", lambda m: _bundle())

    def boom(m, b):
        raise lr.ModelFailure("THINK_CAP: model hit its token cap")
    monkeypatch.setattr(lr, "judge", boom)
    r = researcher.research(QUERY)
    assert "THINK_CAP" in r.error and r.text == ""


def test_anything_unexpected_is_an_error(researcher, monkeypatch):
    def boom(m):
        raise ConnectionError("ollama down")
    monkeypatch.setattr(lr, "retrieve", boom)
    assert "unexpected" in researcher.research(QUERY).error


def test_a_query_without_a_ticker_is_an_error(researcher):
    assert "no (TICKER)" in researcher.research("anything new today?").error


def test_every_retrieval_is_stored_before_judging(researcher, monkeypatch, tmp_path):
    monkeypatch.setattr(lr, "retrieve", lambda m: _bundle())
    monkeypatch.setattr(lr, "judge", lambda m, b: ("NONE", [], ""))
    researcher.research(QUERY)
    files = list(tmp_path.glob("date=*/*.jsonl"))
    assert len(files) == 1 and TICKER in files[0].read_text()


# ------------------------------------------------------------- the judge

def _items() -> dict[str, Any]:
    return _bundle()


def _market() -> lr.Market:
    return lr.Market(TICKER, "Chicago C wins", "KXMLBGAME", "Chicago C", "Chicago WS", NOW)


def _fake_chat(screen_ids: list[str], judged: dict[str, Any]):
    def chat(system, user, schema, think, num_predict, timeout):
        return {"ids": screen_ids} if not think else judged
    return chat


def test_an_empty_screen_is_none_without_asking_the_judge(monkeypatch):
    monkeypatch.setattr(lr, "_chat", _fake_chat([], {}))
    assert lr.judge(_market(), _items())[0] == "NONE"


def test_a_citation_to_an_item_not_shown_is_rejected(monkeypatch):
    judged = {"bad_for_named_side": {"value": True, "source_ids": ["S9"]},
              "bad_for_opponent": {"value": False, "source_ids": []}, "reason": ""}
    monkeypatch.setattr(lr, "_chat", _fake_chat(["S1"], judged))
    with pytest.raises(lr.ModelFailure, match="INVALID_CITATION"):
        lr.judge(_market(), _items())


def test_a_true_side_without_a_citation_is_rejected(monkeypatch):
    judged = {"bad_for_named_side": {"value": True, "source_ids": []},
              "bad_for_opponent": {"value": False, "source_ids": []}, "reason": ""}
    monkeypatch.setattr(lr, "_chat", _fake_chat(["S1"], judged))
    with pytest.raises(lr.ModelFailure, match="INVALID_CITATION"):
        lr.judge(_market(), _items())


def test_both_sides_true_is_both(monkeypatch):
    judged = {"bad_for_named_side": {"value": True, "source_ids": ["S1"]},
              "bad_for_opponent": {"value": True, "source_ids": ["S1"]}, "reason": ""}
    monkeypatch.setattr(lr, "_chat", _fake_chat(["S1"], judged))
    assert lr.judge(_market(), _items())[0] == "BOTH"


# ------------------------------------------------------------- names

def test_club_names_are_localised_for_search():
    assert lr.search_name("Chicago WS", "KXMLBGAME") == "White Sox"
    assert lr.search_name("Fukuoka Hawks", "KXNPBGAME") == "ソフトバンク"
    assert lr.search_name("Hanwha Eagles", "KXKBOGAME") == "한화"


def test_a_reserve_side_does_not_match_its_senior_club(monkeypatch):
    rows = [{"date": "2026-09-26", "name": "deeN", "team2": "mouz nxt"},
            {"date": "2026-09-26", "name": "xertioN", "team1": "MOUZ"}]
    monkeypatch.setattr(lr, "liquipedia_rows", lambda wiki, page: rows)
    got = lr.liquipedia_items("KXCS2GAME", ["MOUZ"], pd.Timestamp(NOW))
    assert [g["headline"] for g in got] == ["Transfer: xertioN from MOUZ to (none)"]


# ------------------------------------------------------------- the candidate

def _market_snapshot() -> MarketSnapshot:
    r = _snap().iloc[0]
    return MarketSnapshot(**{f: r[f] for f in MarketSnapshot.__dataclass_fields__ if f in r})


@pytest.mark.parametrize(("token", "fades"), [("THIS_SIDE", True), ("OTHER_SIDE", False),
                                              ("BOTH", False), ("NONE", False)])
def test_the_candidate_fades_only_when_its_own_side_is_hurt(token, fades):
    m = _market_snapshot()
    ctx = ResearchContext(now=NOW, researcher=StubResearcher(default=f"VERDICT: {token}"))
    p = lnf.forecast(m, ctx)
    assert bool(p < m.implied_prob) is fades and ctx.calls == 1


def test_a_crosstown_opponent_verdict_does_not_fade_under_the_new_candidate():
    """The old name gate read 'Chicago WS' as naming 'Chicago C wins'."""
    m = _market_snapshot()
    ctx = ResearchContext(now=NOW, researcher=StubResearcher(default="VERDICT: Chicago WS"))
    assert lnf.forecast(m, ctx) == m.implied_prob


def test_the_retired_candidate_is_never_asked(tmp_path):
    run_pass(StubResearcher(), _snap(), now=NOW, root=tmp_path, experiment=EXP)
    df = forecast_log.read(tmp_path)
    assert "roster_news_favourite" not in set(df["candidate_id"])
    assert "local_news_favourite" in {c.candidate_id for c in EXP.seed_candidates()}


# ------------------------------------------------------------- sharing and capture

def test_candidates_asking_about_one_market_share_one_call(researcher, monkeypatch):
    from experiments.kalshi_research.researcher import BudgetedResearcher
    n = {"calls": 0}

    def fake(m):
        n["calls"] += 1
        return _bundle("NO_RELEVANT_RESULTS")
    monkeypatch.setattr(lr, "retrieve", fake)
    b = BudgetedResearcher(researcher, budget=1)
    first, second = b.research(QUERY), b.research("other wording (" + TICKER + ")")
    assert n["calls"] == 1 and first.text == second.text and second.calls == 1
    assert b.used == 1 and not second.error


def test_capture_checkpoints_follow_the_ticker_start_time():
    from experiments.kalshi_research import capture
    cps = dict(capture.checkpoints("KXMLBGAME-26SEP261910CWSCHC"))
    start = datetime(2026, 9, 26, 23, 10, tzinfo=UTC)          # 19:10 ET
    assert cps == {"T-12h": start - timedelta(hours=12), "T-3h": start - timedelta(hours=3),
                   "T-1h": start - timedelta(hours=1)}
    assert set(dict(capture.checkpoints("KXNCAAFGAME-26SEP26OKSTWVU"))) == {
        "day-06Z", "day-12Z", "day-15Z"}


def test_capture_takes_each_checkpoint_once(monkeypatch):
    from experiments.kalshi_research import capture
    snap = _snap()
    now = datetime(2026, 9, 26, 22, 0, tzinfo=UTC)            # T-1h is 22:10
    todo = capture.due(snap, now, set())
    assert [lab for _, lab, _ in todo] == ["T-1h"]
    assert capture.due(snap, now, {"KXMLBGAME-26SEP261910CWSCHC|T-1h"}) == []
