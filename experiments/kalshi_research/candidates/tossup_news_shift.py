"""Spend the research on toss-ups, where a roster fact moves the price most.

PARENT: bidirectional_news_shift. It acts on both verdict directions but only
on favourites priced 0.50-0.90, with a fixed 4-point move. Two things about
that are worth changing, and this candidate changes exactly those two:
WHICH band, and HOW BIG the move is. The question asked and the token
mapping's direction are the parent's.

THE EDGE, as a hypothesis. Crossing the spread plus fees costs ~2 points, so a
4-point move on a 0.80 favourite nets about 2 points even if the news is
right every time -- and a 0.80 favourite barely moves when its second-best
player is out, because the team is much better than the field. A 0.50-0.60
game is the opposite: price is most sensitive to a single dated fact (the
starting goalkeeper, the scratched starter, the star ruled out), so the same
true headline is worth more probability points there, and a bigger move
clears the cost. If the book is slow to absorb dated roster news at all, it
is slowest AND matters most in the near-even games.

WHICH MARKETS. Game-winner series in RESEARCH_SERIES, not a tie leg, spread
<= 0.04, mid in [0.50, 0.62], on the game's own UTC date, and not after
kickoff (markets.game_started). Only the leg priced AT OR ABOVE 0.50 is
researched: a game lists each side as its own market, and in a toss-up both
legs would otherwise each cost a call for the same two-sided answer. This
overlaps the parent only on 0.50-0.62 favourites; there the parent's call is
shared through the cache, so the overlap costs nothing, but the two records
differ in magnitude and so remain separately scored.

WHAT IT ASKS. Same question and same local judge as the parent, one call.

HOW THE ANSWER MAPS (SHIFT = 0.07, versus the parent's 0.04):
    NONE       -> abstain
    BOTH       -> abstain: news on both sides cannot be netted from a token
    THIS_SIDE  -> p - SHIFT: this side got worse news
    OTHER_SIDE -> p + SHIFT: the opponent got worse news
Unlike the parent, nothing is clamped to stay on the favourite side of 0.5:
in a toss-up, bad news legitimately makes this side the underdog.

WHAT WOULD FALSIFY IT. If per-contract P&L over ~200 fills across many
series has an interval including zero or negative, or if its fills do no
better than the parent's fills on the same markets (then the band and the
bigger move bought nothing). Also if actions are too rare to be judged within
weeks: toss-ups are plentiful among game markets, but the judge says NONE on
most of them, so the action count is the first thing to check. A 7-point move
that is wrong costs more than a 4-point one, so direction accuracy below
roughly 55% on resolved actions falsifies it outright.

Cannot be backtested: replaying an old game against today's web returns the
score. Every claim above is a hypothesis until the forecast log says
otherwise.
"""
from __future__ import annotations

from datetime import datetime

from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research.candidates.roster_news_favourite import (
    EPS,
    GAME_DAY_START_UTC,
    MAX_SPREAD,
    RESEARCH_SERIES,
    SPREAD_TOL,
    game_date,
)
from experiments.kalshi_research.markets import game_started
from experiments.kalshi_research.types import ResearchContext, parse_verdict

MANIFEST = {
    "candidate_id": "tossup_news_shift",
    "generation": 2,
    "parent_id": "bidirectional_news_shift",
    "created_at": "2026-10-04T15:06:00.247582+00:00",
    # Retired 2026-10-04 (human decision): one of four sports news-shift
    # strategies that are really one bet against sportsbook-anchored game
    # prices. Across ~94 overlapping scored decisions they were right 15-30%
    # of the time and lost 7-19c a trade before fees. History kept for scoring.
    "retired": True,
    "rationale": (
        "Research near-even game-winner markets (0.50-0.62) instead of lopsided "
        "favourites, where one dated roster fact moves the true probability most, "
        "and move 7 points in the news's direction on THIS_SIDE/OTHER_SIDE so the "
        "move clears the ~2-point cost."
    ),
}

MIN_PRICE, MAX_PRICE = 0.50, 0.62
SHIFT = 0.07


def wants_research(market: MarketSnapshot, now: datetime) -> bool:
    if market.series_ticker not in RESEARCH_SERIES:
        return False
    if market.title.lower().startswith("tie"):
        return False
    if not market.has_two_sided_book or market.spread > MAX_SPREAD + SPREAD_TOL:
        return False
    if not (MIN_PRICE <= market.implied_prob <= MAX_PRICE):
        return False
    gd = game_date(market.ticker)
    if gd is None:
        return False
    return gd.replace(hour=GAME_DAY_START_UTC) <= now <= gd.replace(hour=23, minute=59)


def forecast(market: MarketSnapshot, context: ResearchContext) -> float:
    p = market.implied_prob
    if not wants_research(market, context.now):
        return p
    if game_started(market.event_ticker, market.close_time, context.now):
        return p
    verdict, _ = parse_verdict(context.research(
        f"Has anything dated today changed the expected outcome of this event: "
        f"{market.title} ({market.ticker})? Roster, injury, scratch, "
        f"substitution, postponement or venue change."
    ))
    if verdict == "THIS_SIDE":
        return max(p - SHIFT, EPS)
    if verdict == "OTHER_SIDE":
        return min(p + SHIFT, 1.0 - EPS)
    return p
