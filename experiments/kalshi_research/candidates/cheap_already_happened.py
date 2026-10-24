"""Buy the cheap side of a political event when dated news says it already happened.

A variant of politics_already_happened (2026-10-11) that changes WHERE it looks
and HOW HARD it moves, not what it asks.

THE EDGE, as a hypothesis. The parent researches the final 48h, mid 0.03-0.92,
and moves HAPPENED only halfway to 1. It has never disagreed with the market
(research/market 0.00): by two days out the interesting questions are already
priced near 0 or 1, and a halfway move on a market near 0.9 is a rounding error
inside the ~2 point cost. A "this already happened" report is worth most in a
market still priced LOW. "Will Trump say X this week?" at 0.15 is a book
assuming nobody has checked; if a dated report says he said it on Tuesday, the
fair price is near 0.9, not 0.15, and a thin lottery-ticket book is where a
quiet mention is least likely to have been absorbed. Cost still applies: the
move has to beat ~2 points, so only a large move is worth taking.

WHICH MARKETS (`wants_research`). A politics series (markets.is_politics), a
yes/no EVENT question (the parent's ladder/count/polling-average title filter),
between 48h and 120h to close -- earlier than the parent, so the question is
still open and still cheap, and ~3x as many markets qualify (69 vs 23 a week
at the parent's measurement), which is how this gets judged inside the
fortnight -- spread <= 4c, mid 0.05-0.35. Above 0.35 the book already believes
it, so a report is less likely to be news.

WHAT IT ASKS. The same local politics judge, one call per market (the answer
depends only on the market, so it is shared with any candidate asking the same).

HOW IT MAPS. HAPPENED -> max(p, 0.80): a decisive jump, because a 9B model's
HAPPENED on a cheap market is either right (worth ~+0.6) or wrong, and a
fractional shift never clears costs. Everything else abstains: RULED_OUT on a
market already priced low carries almost no information (it was expected), and
CONFLICT/NONE are not evidence.

WHAT WOULD FALSIFY IT. HAPPENED calls resolving NO more than ~25% of the time
(the judge or the wording trap is real); HAPPENED on markets whose price had
already risen between the first quote and the research (the book was never
slow); or almost no HAPPENED verdicts in two weeks, in which case the rate,
not the edge, is the finding. Read the first dozen by hand. An edge above ~10
points is outside every measured bias: leak-check it before trusting it.
"""
from __future__ import annotations

import re
from datetime import datetime

from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research.markets import is_politics
from experiments.kalshi_research.types import ResearchContext, parse_verdict

MANIFEST = {
    "candidate_id": "cheap_already_happened",
    "generation": 2,
    "parent_id": "politics_already_happened",
    "created_at": "2026-10-11T15:06:00.017013+00:00",
    "rationale": (
        "Variant of politics_already_happened: look 48-120h out at markets still "
        "priced 0.05-0.35, and if dated news says the event already happened, "
        "jump decisively to 0.80 instead of moving halfway."
    ),
}

MIN_HOURS, MAX_HOURS = 48.0, 120.0
MAX_SPREAD = 0.04
SPREAD_TOL = 1e-9
MIN_PRICE, MAX_PRICE = 0.05, 0.35
TARGET = 0.80
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
    return MIN_PRICE <= market.implied_prob <= MAX_PRICE


def forecast(market: MarketSnapshot, context: ResearchContext) -> float:
    p = market.implied_prob
    if not wants_research(market, context.now):
        return p
    verdict, _ = parse_verdict(context.research(
        f"Has this already happened: {market.title} ({market.ticker})?"
    ))
    if verdict == "HAPPENED":
        return max(p, TARGET)
    return p
