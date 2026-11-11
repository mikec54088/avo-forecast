"""Fade a near-certain political market when dated news says it was called off.

A variant of cheap_already_happened (2026-11-08) that keeps the "decisive jump"
idea but moves it to the one corner of the price range no sibling covers: the
EXPENSIVE end, 0.90-0.98, and acts on RULED_OUT instead of HAPPENED.

THE EDGE, as a hypothesis. Every politics candidate so far sits at 0.05-0.92.
expensive_ruled_out stops at 0.92; confirm_favourite_happened pushes a favourite
UP; longshot_already_happened sits at 0.01-0.05. Nobody looks at markets the
book has priced as all but settled -- "Will the meeting happen this week?" at
0.96 -- and asks whether a dated report says it was cancelled, postponed or
denied. A book at 0.96 is a book that stopped checking: quotes there are thin
and are anchored by "this is scheduled", so a quiet cancellation notice is the
sort of fact that arrives late. The payoff is lopsided in the right direction
for cost: buying NO at ~0.04-0.06 that settles at 1 pays far more than the ~2
point crossing cost, and a wrong call loses a small, known stake.

WHICH MARKETS (`wants_research`). A politics series (markets.is_politics), a
yes/no EVENT question (the usual ladder/count/polling-average title filter),
between 12h and 120h to close -- early enough that a cancellation has had time
to be reported and the book has not yet run out the clock -- spread <= 3c,
mid 0.90-0.98. That is a thin slice, deliberately: the question is asked only
where the prior is strongest and the cost of a false alarm is smallest.

WHAT IT ASKS. The local politics judge, one call per market (the answer depends
only on the market, so the call is shared with any candidate asking the same).

HOW IT MAPS. RULED_OUT -> min(p, 0.35): decisive enough to clear costs, but not
to 0.05, because a 9B model reading headlines can mistake a rescheduled item or
a denial of a rumour for a cancellation. HAPPENED, CONFLICT and NONE abstain:
HAPPENED confirms what the price already says.

WHAT WOULD FALSIFY IT. RULED_OUT calls that still resolve YES more than ~30% of
the time (the judge confuses postponement or rumour-denial with cancellation);
RULED_OUT calls on markets whose price had already fallen below 0.90 by the
time of the decision (the book was never slow); or almost no RULED_OUT verdicts
in two weeks -- then the rate, not the edge, is the finding. Read the first
dozen by hand. A single loss here costs ~0.3, so a handful of wrong calls
outweighs many right ones: judge it on fills, not hit rate. An edge above ~10
points is outside every measured bias: run scripts/leak_check.py first.
"""
from __future__ import annotations

import re
from datetime import datetime

from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research.markets import is_politics
from experiments.kalshi_research.types import ResearchContext, parse_verdict

MANIFEST = {
    "candidate_id": "certainty_ruled_out",
    "generation": 3,
    "parent_id": "cheap_already_happened",
    "created_at": "2026-11-08T16:06:06.306878+00:00",
    "rationale": (
        "Variant of cheap_already_happened: look at the expensive corner "
        "(0.90-0.98), 12-120h out, and if dated news says the event was called "
        "off, drop decisively to 0.35 instead of ignoring it."
    ),
}

MIN_HOURS, MAX_HOURS = 12.0, 120.0
MAX_SPREAD = 0.03
SPREAD_TOL = 1e-9
MIN_PRICE, MAX_PRICE = 0.90, 0.98
CEILING = 0.35
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
    return MIN_PRICE - SPREAD_TOL <= market.implied_prob <= MAX_PRICE + SPREAD_TOL


def forecast(market: MarketSnapshot, context: ResearchContext) -> float:
    p = market.implied_prob
    if not wants_research(market, context.now):
        return p
    verdict, _ = parse_verdict(context.research(
        f"Has this been called off or ruled out: {market.title} ({market.ticker})?"
    ))
    if verdict == "RULED_OUT":
        return min(p, CEILING)
    return p
