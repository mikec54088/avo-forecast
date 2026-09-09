"""Research the pre-game news for a major-league game-winner market; fade a
favourite when a fresh injury, scratch or postponement is reported.

THE EDGE, as a hypothesis. Game-winner books are quoted off the odds feed, not
the beat reporters. A late scratch, a starting-pitcher change or a key injury
reported on game day can leave the favourite where the pre-news line put it.
Research the title, look for such a report, and if one exists shrink the
favourite toward 0.5 by SHIFT. Otherwise return the market.

This is the first research candidate; its job is to prove the path end to end
at a bounded, measured cost. It is deliberately simple.

WHICH MARKETS. Series in RESEARCH_SERIES -- the game-winner series that both
appear in the hourly full pass in volume AND actually resolve into scoreable
entries (measured 2026-09-09: KXNCAAFGAME 566 markets / 136 resolved entries,
KXMLBGAME 150 / 95, KXMLSGAME 174, KXEPLGAME 66, KXLALIGAGAME 105, KXSERIEAGAME
78; plus the two esports series that resolve most of all, KXCS2GAME 803 and
KXLOLGAME 195). Favourite side only, mid in [MIN_PRICE, MAX_PRICE], spread <=
MAX_SPREAD so the fill is real, and a deterministic hash sample of SAMPLE_1_IN
tickers so the daily cost is bounded whatever the fixture list does.

WHEN. Kalshi sets close_time two to three days AFTER the game, so hours-to-close
says nothing about kickoff. The ticker does: KXMLBGAME-26SEP081940PITCWS-PIT
carries the date (and for some series the time). Research is spent only on the
game's date in UTC, from GAME_DAY_START_UTC onward. Before that the candidate
abstains and the runner asks again next pass.

WHAT IT ASKS. One query: the title plus "injury OR scratched OR postponed OR
lineup, today". Budget 1 of 3.

HOW THE ANSWER MAPS. If the text contains one of KEYWORDS and does not say
NOTHING FOUND, shift the favourite toward 0.5 by SHIFT, never crossing it. No
side attribution in v1: a report about EITHER side fades the favourite, which
is wrong about half the time and is the first thing to fix once the path is
proven and the log shows what the researcher actually returns.

EXPECTED COST. Over Sun-Tue 2026-09-06..08 the full pass showed 97 qualifying
favourites in these series (~32/day); at 1-in-4 that is ~8 research calls a
day, each one `claude -p` with web search.

WHAT WOULD FALSIFY IT. A forward P&L interval including zero on the researched
subset; or research overwhelmingly returning NOTHING FOUND, meaning the query
or the timing is wrong before the idea is.

Cannot be backtested: replaying an old game against today's web returns the
score. Every number above is a guess until the forecast log says otherwise.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research.types import ResearchContext

MANIFEST = {
    "candidate_id": "injury_news_favourite",
    "generation": 1,
    "parent_id": "research_market",
    "created_at": "2026-09-09T16:00:00+00:00",
    "rationale": (
        "On major-league game-winner favourites, research game-day news; if "
        "an injury/scratch/postponement is reported, fade the favourite toward "
        "0.5. Bounded by a series allowlist, a ticker-date gate and a hash sample."
    ),
}

RESEARCH_SERIES = frozenset({
    "KXNCAAFGAME", "KXMLBGAME", "KXMLSGAME", "KXEPLGAME", "KXLALIGAGAME",
    "KXSERIEAGAME", "KXCS2GAME", "KXLOLGAME",
})
MIN_PRICE, MAX_PRICE = 0.55, 0.85
MAX_SPREAD = 0.04
SPREAD_TOL = 1e-9
SAMPLE_1_IN = 4
SHIFT = 0.04
GAME_DAY_START_UTC = 6        # research from 06:00 UTC on the ticker's date
KEYWORDS = ("injur", "scratch", "postpon", "ruled out", "questionable",
            "will not play", "won't play", "lineup change", "suspended", "out for")
EPS = 0.001

_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")
_MON = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def game_date(ticker: str) -> datetime | None:
    m = _DATE.search(ticker)
    if not m or m.group(2) not in _MON:
        return None
    try:
        return datetime(2000 + int(m.group(1)), _MON[m.group(2)], int(m.group(3)),
                        tzinfo=timezone.utc)
    except ValueError:
        return None


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
    start = gd.replace(hour=GAME_DAY_START_UTC)
    if not (start <= now < start.replace(hour=23, minute=59)):
        return False
    return hashlib.sha256(market.ticker.encode()).digest()[0] % SAMPLE_1_IN == 0


def forecast(market: MarketSnapshot, context: ResearchContext) -> float:
    p = market.implied_prob
    if not wants_research(market, context.now):
        return p
    text = context.research(
        f"{market.title} injury OR scratched OR postponed OR lineup, today"
    ).lower()
    if "nothing found" in text or not any(k in text for k in KEYWORDS):
        return p
    return max(p - SHIFT, 0.5 + EPS)
