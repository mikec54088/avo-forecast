"""Fade a game-winner favourite when researched news says THIS side got worse.

Supersedes injury_news_favourite, which is in the forecast log with eight
forecasts and a clear failure. Same hypothesis, different extraction.

THE EDGE, as a hypothesis. Game-winner books are quoted off the odds feed, not
the beat reporters. A late scratch, a starting-pitcher change or an esports
roster substitution reported on game day can leave the favourite priced where
the pre-news line put it.

WHY THE PARENT FAILED, and it was not the hypothesis. It asked for free-text
findings and matched substrings against them. Measured on its first four live
research calls (2026-09-10), three of its four fades were wrong, and two of
those fired on the researcher explicitly reporting the ABSENCE of news:

    "No sourced reports of injury, scratch, or postponement affecting the
     active roster today."
    "No report found of an injury, scratch, or postponement specifically
     dated 2026-09-10."

A substring gate reads a negation as a positive, because the negation restates
the query's own words. The third fade fired on a genuine but irrelevant line --
a pitcher on the IL since three days earlier, not in this game. Only the
"NOTHING FOUND" case behaved. No keyword list fixes this; prose can always
negate. So the researcher now returns a VERDICT token (research protocol v2)
and this candidate parses it instead of reading prose.

WHICH MARKETS. Series in RESEARCH_SERIES -- game-winner series that both appear
in the hourly full pass and actually resolve into scoreable entries. Favourite
side only, mid in [MIN_PRICE, MAX_PRICE], spread <= MAX_SPREAD so the fill is
real, and a deterministic 1-in-SAMPLE_1_IN ticker sample to bound cost.

WHEN. Kalshi sets close_time two to three days AFTER the game, so hours-to-close
says nothing about kickoff. The ticker carries the date
(KXMLBGAME-26SEP081940PITCWS-PIT); research is spent only on the game's own date
in UTC, from GAME_DAY_START_UTC. Before that it abstains and the runner asks
again next pass.

HOW THE ANSWER MAPS. VERDICT: NONE -> abstain, which now covers "no news", an
unconfirmed report, a routine preview and a malformed reply. A verdict naming
THIS market's side -> shrink toward 0.5 by SHIFT. A verdict naming anyone else
-> abstain, deliberately: a report hurting the opponent would argue for buying
the favourite, but identifying the opponent from a title like "Atlanta wins"
is guesswork, and a candidate that guesses has stopped being falsifiable.

WHY THE FUNNEL WIDENED (2026-09-17). Eight days live produced 134 research
calls and SIX actions. The verdicts were right -- 125 NONE, 9 naming a side, 6
of those ours -- and the evidence when it fired was exactly what was wanted
(Ohtani to the 15-day IL; an OU starting tackle doubtful; a Texas A&M QB's
hamstring during the game). The problem was arithmetic, not quality: six
actions in eight days is 267 days to the 200-fill gate, which is the
unfalsifiable-by-construction failure this candidate's own docstring names
below. So the gates that cost nothing in precision were opened: the 1-in-4
ticker sample dropped (it was pure rate limiting), the price band widened to
0.50-0.90, and the series allowlist extended to every game-winner series that
both appears in the full pass and resolves. Expect roughly 15-20x the research
calls. What did NOT change is the verdict logic -- a negation still parses as
NONE, and a verdict naming the opponent is still an abstention.

WHAT WOULD FALSIFY IT. A forward P&L interval including zero on the faded
subset. It is also falsified, and more cheaply, if VERDICT is almost always
NONE -- that would mean game-day roster news is not reachable inside this window
and the idea is untestable here regardless of its truth.

Cannot be backtested: replaying an old game against today's web returns the
score. Every claim above is a hypothesis until the forecast log says otherwise.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research.types import ResearchContext, parse_verdict

MANIFEST = {
    "candidate_id": "roster_news_favourite",
    "generation": 1,
    "parent_id": "injury_news_favourite",
    "created_at": "2026-09-11T02:30:00+00:00",
    "rationale": (
        "Fade a game-winner favourite when a parsed VERDICT names THIS side as "
        "the one a dated report has hurt. Replaces its parent's substring gate, "
        "which read the researcher's negations as positives and got three of "
        "four fades wrong."
    ),
}

RESEARCH_SERIES = frozenset({
    "KXNCAAFGAME", "KXMLBGAME", "KXMLSGAME", "KXEPLGAME", "KXLALIGAGAME",
    "KXSERIEAGAME", "KXCS2GAME", "KXLOLGAME",
    # Added 2026-09-17 with the rest of the widening: game-winner series that
    # both appear in the full pass and resolve into scoreable entries.
    "KXNPBGAME", "KXKBOGAME", "KXVALORANTGAME", "KXDOTA2GAME", "KXFIBAGAME",
    "KXBRASILEIROGAME", "KXLIGAMXGAME", "KXUSLGAME", "KXEFLCHAMPIONSHIPGAME",
    "KXSERIECGAME", "KXBRASILEIROBGAME", "KXR6GAME", "KXNCAAMSOCCERGAME",
    "KXARGNACBGAME", "KXETTANGAME", "KXECULPGAME", "KXURYPDGAME",
    "KXEREDIVISIEGAME", "KXLVAVIRGAME",
})
MIN_PRICE, MAX_PRICE = 0.50, 0.90
MAX_SPREAD = 0.04
SPREAD_TOL = 1e-9
SAMPLE_1_IN = 1          # widened from 4 on 2026-09-17; see WHY THE FUNNEL WIDENED
SHIFT = 0.04
GAME_DAY_START_UTC = 6
EPS = 0.001

_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")
_MON = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
_STOP = frozenset({"wins", "win", "the", "tie", "is", "result", "a", "of", "and", "fc", "cs",
                   "esports", "esport", "club", "team", "gaming", "vs"})


def game_date(ticker: str) -> datetime | None:
    m = _DATE.search(ticker)
    if not m or m.group(2) not in _MON:
        return None
    try:
        return datetime(2000 + int(m.group(1)), _MON[m.group(2)], int(m.group(3)),
                        tzinfo=timezone.utc)
    except ValueError:
        return None


def side_tokens(title: str) -> set[str]:
    """The distinctive words naming this market's side, from its title."""
    words = re.findall(r"[A-Za-z][A-Za-z'.-]+", title.lower())
    return {w for w in words if w not in _STOP and len(w) > 2}


def names_this_side(verdict: str, title: str) -> bool:
    v = verdict.lower()
    return any(t in v for t in side_tokens(title))


def wants_research(market: MarketSnapshot, now: datetime) -> bool:
    if market.series_ticker not in RESEARCH_SERIES:
        return False
    if market.title.lower().startswith("tie"):
        return False
    if market.spread > MAX_SPREAD + SPREAD_TOL:
        return False
    if not (MIN_PRICE <= market.implied_prob <= MAX_PRICE):
        return False
    gd = game_date(market.ticker)
    if gd is None:
        return False
    if not (gd.replace(hour=GAME_DAY_START_UTC) <= now <= gd.replace(hour=23, minute=59)):
        return False
    return hashlib.sha256(market.ticker.encode()).digest()[0] % SAMPLE_1_IN == 0


def forecast(market: MarketSnapshot, context: ResearchContext) -> float:
    p = market.implied_prob
    if not wants_research(market, context.now):
        return p
    verdict, _ = parse_verdict(context.research(
        f"Has anything dated today changed the expected outcome of this event: "
        f"{market.title} ({market.ticker})? Roster, injury, scratch, "
        f"substitution, postponement or venue change."
    ))
    if verdict == "NONE" or not names_this_side(verdict, market.title):
        return p
    return max(p - SHIFT, 0.5 + EPS)
