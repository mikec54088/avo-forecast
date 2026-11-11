"""Buy a likely political event up to near-certainty when dated news says it already happened.

A variant of cheap_already_happened (2026-11-01) that takes the same token on
the one price band the family never uses: the EXPENSIVE side.

THE EDGE, as a hypothesis. Every sibling takes HAPPENED only on markets priced
below 0.55 (cheap, coinflip, week_ahead, longshot, endgame), and takes the
expensive end only with RULED_OUT (expensive_ruled_out). So a market quoted
0.55-0.90 whose event has ALREADY occurred is unwatched. These are plentiful
(books price "Trump says X before Friday" at 0.7 when he says X often) and a
quote of 0.70 on something that happened yesterday is a book that has not
re-marked: the fair price is ~0.97. The move is ~25 points against a ~2 point
cost. It also should fire far more often than the cheap siblings (research/
market 0.00), because many politics books sit in this band, which is how it
gets judged inside the fortnight. Honest caveat: a book already at 0.90 has
probably seen the news, so the band stops at 0.90 and a falsifier checks it.

WHICH MARKETS (`wants_research`). A politics series (markets.is_politics), a
yes/no EVENT question (the family's ladder/count/polling-average title
filter), 24-168h to close (under 24h the pass cadence and a 2-4 minute call
leave no time to act; 168h is markets.POLITICS_HORIZON), spread <= 4c, mid
0.55-0.90. The band is disjoint from every HAPPENED sibling. It overlaps
expensive_ruled_out's markets, but the two read opposite tokens off one shared
call, so they can never both act on a market.

WHAT IT ASKS. The same local politics judge, one call per market, shared with
any candidate asking about the same market.

HOW IT MAPS. HAPPENED -> max(p, 0.97). Everything else abstains: RULED_OUT
belongs to expensive_ruled_out, and CONFLICT/NONE are not evidence. The target
is not 1.0 because resolution wording is fine print and the judge is a 9B model
reading headlines. Asymmetry: a false HAPPENED at 0.70 risks ~70 cents to win
~30, so the judge has to be right more than ~72% of the time to profit.

WHAT WOULD FALSIFY IT. HAPPENED calls resolving NO more than ~5% of the time
(break-even is near there at these prices); HAPPENED verdicts concentrated in
the 0.85-0.90 end of the band (the book was never slow, only the judge was
late); or almost no HAPPENED verdicts in two weeks, in which case the rate is
the finding. Read the first dozen by hand. An edge above ~10 points is outside
every measured bias: run scripts/leak_check.py before trusting it.
"""
from __future__ import annotations

import re
from datetime import datetime

from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research.markets import is_politics
from experiments.kalshi_research.types import ResearchContext, parse_verdict

MANIFEST = {
    "candidate_id": "confirm_favourite_happened",
    "generation": 3,
    "parent_id": "cheap_already_happened",
    "created_at": "2026-11-01T16:06:32.522482+00:00",
    "rationale": (
        "Variant of cheap_already_happened: the same HAPPENED token on the band "
        "it leaves out. 24-168h out, markets priced 0.55-0.90, and if dated news "
        "says the event already happened, jump to 0.97."
    ),
}

MIN_HOURS, MAX_HOURS = 24.0, 168.0
MAX_SPREAD = 0.04
SPREAD_TOL = 1e-9
MIN_PRICE, MAX_PRICE = 0.55, 0.90
TARGET = 0.97
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
        f"Has this already happened: {market.title} ({market.ticker})?"
    ))
    if verdict == "HAPPENED":
        return max(p, TARGET)
    return p
