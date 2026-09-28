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


# ------------------------------------------------------------- politics capture

def test_say_events_search_the_quoted_words_in_batches():
    from experiments.kalshi_research import capture
    titles = [f'Will Trump say "w{i}" before Oct 1, 2026?' for i in range(10)]
    q = capture.topic_queries(titles, "KXTRUMPSAYNICKNAME")
    assert q[0] == "Trump" and len(q) == 3
    assert q[1].count(" OR ") == 7 and '"w9"' in q[2]


def test_a_narrow_topic_gets_a_broader_search_too():
    from experiments.kalshi_research import capture
    q = capture.topic_queries(["Will Trump's RCP approval average be exactly 39.5 on Sep 20?"],
                              "")
    assert len(q) == 2 and len(q[1].split()) == 3 and q[0].startswith(q[1])


def test_a_title_without_a_topic_falls_back_to_the_series():
    from experiments.kalshi_research import capture
    assert capture.topic_queries(["Above 8.3%"], "KXGENERICBALLOTVOTEHUB")[0] == (
        "generic congressional ballot poll")


def test_political_checkpoints_follow_the_close():
    from experiments.kalshi_research import capture
    now = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    close = datetime(2026, 9, 29, 17, 0, tzinfo=UTC)
    cps = dict(capture.close_checkpoints(close, now))
    assert cps["C-3h"] == close - timedelta(hours=3) and cps["C-1h"] == close - timedelta(hours=1)
    # no daily capture on the closing day: 15Z is inside the final 3h (14Z-17Z)
    assert {k for k in cps if k.startswith("day-")} == {
        "day-20260926-15Z", "day-20260927-15Z", "day-20260928-15Z"}


def test_only_short_dated_political_events_are_captured():
    from experiments.kalshi_research import capture
    now = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    base = _snap().iloc[0].to_dict()
    rows = [{**base, "ticker": "KXTRUMPAPPROVE-26SEP28-B39", "event_ticker": "KXTRUMPAPPROVE-26SEP28",
             "series_ticker": "KXTRUMPAPPROVE", "title": "RCP approval 39?",
             "close_time": now + timedelta(days=1)},
            {**base, "ticker": "KXTRUMPOUT27-X", "event_ticker": "KXTRUMPOUT27-29",
             "series_ticker": "KXTRUMPOUT27", "title": "Leave office before 2029?",
             "close_time": now + timedelta(days=800)},
            {**base, "ticker": "KXDIMAYORGAME-X", "event_ticker": "KXDIMAYORGAME-X",
             "series_ticker": "KXDIMAYORGAME", "title": "Millonarios wins",
             "close_time": now + timedelta(days=1)}]
    assert list(capture.politics(pd.DataFrame(rows), now)) == ["KXTRUMPAPPROVE-26SEP28"]


# ------------------------------------------------------------- kickoff gate

def test_game_start_comes_from_the_ticker_or_close_minus_48h():
    from experiments.kalshi_research import markets
    close = datetime(2026, 9, 29, 2, 0, tzinfo=UTC)
    assert markets.game_start("KXMLBGAME-26SEP261910CWSCHC", close) == datetime(
        2026, 9, 26, 23, 10, tzinfo=UTC)                        # the ticker wins
    assert markets.game_start("KXNCAAFGAME-26SEP26OKSTWVU", close) == close - timedelta(hours=48)


def test_research_stops_before_kickoff_not_at_it():
    from experiments.kalshi_research import markets
    start = datetime(2026, 9, 26, 23, 10, tzinfo=UTC)
    ev, close = "KXMLBGAME-26SEP261910CWSCHC", start + timedelta(days=3)
    assert not markets.game_started(ev, close, start - timedelta(minutes=16))
    assert markets.game_started(ev, close, start - timedelta(minutes=14))
    assert markets.game_started(ev, close, start + timedelta(hours=2))


@pytest.mark.parametrize("module", ["local_news_favourite", "bidirectional_news_shift"])
def test_sports_candidates_do_not_research_a_started_game(module):
    import importlib
    mod = importlib.import_module(f"experiments.kalshi_research.candidates.{module}")
    m = _market_snapshot()
    in_game = datetime(2026, 9, 26, 23, 30, tzinfo=UTC)        # 20 min after first pitch
    ctx = ResearchContext(now=in_game, researcher=StubResearcher(default="VERDICT: THIS_SIDE"))
    assert mod.forecast(m, ctx) == m.implied_prob and ctx.calls == 0


def test_the_underdog_mirror_does_not_research_a_started_game():
    from experiments.kalshi_research.candidates import underdog_mirror_shift as ums
    snap = _snap()
    fav, dog = (MarketSnapshot(**{f: r[f] for f in MarketSnapshot.__dataclass_fields__ if f in r})
                for _, r in snap.iterrows())
    stub = StubResearcher(default="VERDICT: THIS_SIDE")
    before = ResearchContext(now=NOW, researcher=stub, siblings=[fav])
    assert ums.forecast(dog, before) > dog.implied_prob     # the gate is what stops it below
    after = ResearchContext(now=datetime(2026, 9, 26, 23, 30, tzinfo=UTC), researcher=stub,
                            siblings=[fav])
    assert ums.forecast(dog, after) == dog.implied_prob and after.calls == 0


# ------------------------------------------------------------- politics research

POL_EVENT = "KXTRUMPSAY-26SEP28"


def _pol_snap() -> pd.DataFrame:
    base = _snap().iloc[0].to_dict()
    base.update(event_ticker=POL_EVENT, series_ticker="KXTRUMPSAY",
                close_time=NOW + timedelta(hours=20), yes_bid=0.33, yes_ask=0.36)
    return pd.DataFrame([{**base, "ticker": f"{POL_EVENT}-{w.upper().replace(' ', '')}",
                          "title": f'Will Trump say "{w}" before Sep 28, 2026?'}
                         for w in ("Voter ID", "Moon", "Nobel")])


def _pol_bundle(items: int = 1) -> dict[str, Any]:
    return {"id": f"{POL_EVENT}@x", "asked_at": NOW.isoformat(), "state": "EVIDENCE_AVAILABLE",
            "errors": [], "items": [
                {"id": f"S{i + 1}", "published": "2026-09-26T15:00:00+00:00",
                 "publisher": "AP", "headline": f"Trump rally remarks {i}"} for i in range(items)]}


@pytest.fixture
def pol_researcher(monkeypatch, tmp_path):
    monkeypatch.setattr(lr, "model_digest", lambda: "abc123")
    r = lr.LocalResearcher(evidence_root=tmp_path)
    r.bind(_pol_snap())
    return r


def _q(word: str) -> str:
    return f"Has this already happened: say {word} ({POL_EVENT}-{word.upper().replace(' ', '')})?"


def test_a_political_event_is_searched_and_screened_once(pol_researcher, monkeypatch):
    from experiments.kalshi_research.researcher import BudgetedResearcher
    n = {"search": 0, "screen": 0}

    def search(*a, **k):
        n["search"] += 1
        return _pol_bundle()

    def screen(*a, **k):
        n["screen"] += 1
        return []
    monkeypatch.setattr(lr, "retrieve_topic", search)
    monkeypatch.setattr(lr, "politics_screen", screen)
    monkeypatch.setattr(lr, "retrieve", lambda m: pytest.fail("the game path was used"))
    b = BudgetedResearcher(pol_researcher, budget=1)
    answers = [b.research(_q(w)) for w in ("Voter ID", "Moon", "Nobel")]
    assert n == {"search": 1, "screen": 1}
    assert all(a.text.startswith("VERDICT: NONE") and not a.error for a in answers)
    assert b.used == 1 and b.refused == 0       # siblings of an empty screen are free


def test_a_screened_item_goes_to_the_judge_per_market(pol_researcher, monkeypatch):
    monkeypatch.setattr(lr, "retrieve_topic", lambda *a, **k: _pol_bundle())
    monkeypatch.setattr(lr, "politics_screen", lambda *a, **k: ["S1"])
    asked = []

    def judge(title, close, now, bundle, keep):
        asked.append(title)
        return ("HAPPENED" if "Voter ID" in title else "NONE"), ["S1"], "said it"
    monkeypatch.setattr(lr, "politics_judge", judge)
    yes, no = pol_researcher.research(_q("Voter ID")), pol_researcher.research(_q("Moon"))
    assert yes.text.startswith("VERDICT: HAPPENED") and "Trump rally remarks" in yes.text
    assert no.text.startswith("VERDICT: NONE") and len(asked) == 2
    assert not pol_researcher.is_cached(_q("Nobel"))   # a live screen costs a judge call


def test_a_failed_political_search_is_an_error_never_none(pol_researcher, monkeypatch):
    monkeypatch.setattr(lr, "retrieve_topic", lambda *a, **k: {
        **_pol_bundle(0), "state": "SEARCH_FAILED", "errors": ["SEARCH_FAILED q: URLError"]})
    r = pol_researcher.research(_q("Moon"))
    assert r.error.startswith("local: SEARCH_FAILED") and r.text == ""


@pytest.mark.parametrize(("happened", "ruled_out", "token"), [
    (False, False, "NONE"), (True, False, "HAPPENED"),
    (False, True, "RULED_OUT"), (True, True, "CONFLICT")])
def test_the_politics_judge_maps_to_tokens(monkeypatch, happened, ruled_out, token):
    def chat(*a, **k):
        return {"happened": {"value": happened, "source_ids": ["S1"] if happened else []},
                "ruled_out": {"value": ruled_out, "source_ids": ["S1"] if ruled_out else []},
                "reason": ""}
    monkeypatch.setattr(lr, "_chat", chat)
    got, _, _ = lr.politics_judge("q", NOW, NOW, _pol_bundle(), ["S1"])
    assert got == token


def test_a_politics_citation_to_an_item_not_shown_is_rejected(monkeypatch):
    monkeypatch.setattr(lr, "_chat", lambda *a, **k: {
        "happened": {"value": True, "source_ids": ["S2"]},
        "ruled_out": {"value": False, "source_ids": []}, "reason": ""})
    with pytest.raises(lr.ModelFailure, match="INVALID_CITATION"):
        lr.politics_judge("q", NOW, NOW, _pol_bundle(2), ["S1"])


# ------------------------------------------------------------- politics candidate

def _pol_market(title: str = 'Will Trump say "Voter ID" before Sep 28, 2026?',
                hours: float = 20.0, bid: float = 0.33, ask: float = 0.36) -> MarketSnapshot:
    r = {**_pol_snap().iloc[0].to_dict(), "title": title, "yes_bid": bid, "yes_ask": ask,
         "close_time": NOW + timedelta(hours=hours)}
    return MarketSnapshot(**{f: r[f] for f in MarketSnapshot.__dataclass_fields__ if f in r})


@pytest.mark.parametrize(("token", "expect"), [
    ("HAPPENED", lambda p: p + (1 - p) / 2), ("RULED_OUT", lambda p: p / 2),
    ("NONE", lambda p: p), ("CONFLICT", lambda p: p)])
def test_the_politics_candidate_maps_the_verdict(token, expect):
    from experiments.kalshi_research.candidates import politics_already_happened as pah
    m = _pol_market()
    ctx = ResearchContext(now=NOW, researcher=StubResearcher(default=f"VERDICT: {token}"))
    assert pah.forecast(m, ctx) == pytest.approx(expect(m.implied_prob)) and ctx.calls == 1


@pytest.mark.parametrize("m", [
    _pol_market(hours=60),                                           # too early: one shot only
    _pol_market(title="Will there be at least 7 presidential actions in the week?"),
    _pol_market(title="Will Trump's RCP approval average be below 38.4 on Sep 28?"),
    _pol_market(bid=0.93, ask=0.95),                                 # no room left
    _pol_market(bid=0.30, ask=0.40),                                 # spread too wide
])
def test_the_politics_candidate_skips_what_news_cannot_settle(m):
    from experiments.kalshi_research.candidates import politics_already_happened as pah
    ctx = ResearchContext(now=NOW, researcher=StubResearcher(default="VERDICT: HAPPENED"))
    assert pah.forecast(m, ctx) == m.implied_prob and ctx.calls == 0


def test_sports_candidates_never_research_politics():
    m = _pol_market()
    ctx = ResearchContext(now=NOW, researcher=StubResearcher(default="VERDICT: THIS_SIDE"))
    assert lnf.forecast(m, ctx) == m.implied_prob and ctx.calls == 0
