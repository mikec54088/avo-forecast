"""Pull a confident political price toward 0.5 when dated news CONTRADICTS itself.

A variant of cheap_already_happened (2026-11-08) that changes WHICH VERDICT it
trusts. Every politics sibling (cheap_already_happened, coinflip_settled_by_news,
week_ahead_happened, longshot_already_happened, confirm_favourite_happened,
expensive_ruled_out, certainty_ruled_out, endgame_decisive, lowest_rung_happened,
politics_already_happened) acts on HAPPENED or RULED_OUT, i.e. on a judge
saying the question is SETTLED. All of them idle: settled-by-news is rare and
the book is usually there first. Nobody uses the fourth token, CONFLICT: the
judge found one dated report saying it happened AND another saying it was
ruled out.

THE EDGE, as a hypothesis. A price at 0.10 or 0.90 says "nearly settled".
When the news itself is split, that confidence is unearned: the event is
live, disputed or reported two ways (a statement denied then confirmed, a
meeting announced then called off), and the question's resolution rests on
a detail the book is not pricing. Traders anchor on the headline they saw
last; a contradictory pair is exactly the case where one side of the book
is stale. We do not need to know WHICH report is right, only that the
extreme price overstates certainty. Expected value comes from a confident
market being wrong more often than it says when the evidence is mixed, and
the move is large enough (>= 8 points) to clear the ~2 point cost.

WHICH MARKETS (`wants_research`). A politics series (markets.is_politics), a
yes/no EVENT question (ladder/count/polling-average titles skipped, as the
judge cannot count), 24h-120h to close (long enough for the dispute to
resolve either way, short enough to be live), spread <= 4c, and |mid - 0.5|
>= 0.30 (mid <= 0.20 or >= 0.80). Mid-priced markets are skipped: pulling
toward 0.5 there moves nothing.

WHAT IT ASKS. The same local politics judge, one call per market, shared
with any candidate asking about the same market.

HOW IT MAPS. CONFLICT -> p + 0.5 * (0.5 - p), halfway to 0.5 (a 0.90 market
goes to 0.70; a 0.10 market to 0.30). Every other token abstains: HAPPENED
and RULED_OUT belong to the siblings, NONE is not evidence.

WHAT WOULD FALSIFY IT. CONFLICT markets resolving on the market's own
favoured side about as often as the price says (the split reports were noise
and the book was right); CONFLICT appearing on under ~1 in 30 researched
markets, in which case the rate, not the edge, is the finding; or the
CONFLICT cases being mostly one wording trap (e.g. a "will X say Y" market
where one report quotes a different speaker). Read the first dozen by hand.
An edge above ~10 points is outside every measured bias: leak-check it
(scripts/leak_check.py) before building on it.
"""
from __future__ import annotations

import re
from datetime import datetime

from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research.markets import is_politics
from experiments.kalshi_research.types import ResearchContext, parse_verdict

MANIFEST = {
    "candidate_id": "contested_pulls_to_middle",
    "generation": 3,
    "parent_id": "cheap_already_happened",
    "created_at": "2026-11-08T16:06:53.373288+00:00",
    "rationale": (
        "Variant of cheap_already_happened: act on the unused CONFLICT verdict. "
        "On a confident political price (<=0.20 or >=0.80), contradictory dated "
        "reports mean the certainty is unearned, so pull halfway to 0.5."
    ),
}

MIN_HOURS, MAX_HOURS = 24.0, 120.0
MAX_SPREAD = 0.04
SPREAD_TOL = 1e-9
MAX_LOW, MIN_HIGH = 0.20, 0.80
PULL = 0.5
LADDER = re.compile(
    r"\b(?:at least|at most|between|above|below|exactly|more than|fewer than|less than"
    r"|or more|or fewer|number of|approval|average|posts?)\b|%", re.IGNORECASE)


def wants_research(market: MarketSnapshot, now: datetime) -> bool:
    if not is_politics(market.series_ticker) or LADDER.search(market.title):
        return False
    hours_left = (market.close_time - now).total_seconds() / 3600.0
    if not (MIN_HOURS < hours_left <= MAX_HOURS):
        return False
    if not market.has_two_sided_book or market.spread > MAX_SPREAD + SPREAD_TOL:
        return False
    p = market.implied_prob
    return p <= MAX_LOW + SPREAD_TOL or p >= MIN_HIGH - SPREAD_TOL


def forecast(market: MarketSnapshot, context: ResearchContext) -> float:
    p = market.implied_prob
    if not wants_research(market, context.now):
        return p
    verdict, _ = parse_verdict(context.research(
        f"Do reports conflict on whether this happened: {market.title} ({market.ticker})?"
    ))
    if verdict == "CONFLICT":
        return p + PULL * (0.5 - p)
    return p
