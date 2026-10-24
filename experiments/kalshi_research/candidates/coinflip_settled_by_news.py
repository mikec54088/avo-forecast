"""Take either side of a toss-up political event when dated news says it is settled.

A variant of cheap_already_happened (2026-10-18) that changes WHICH PRICE BAND it
looks at and uses BOTH decisive tokens, not one.

THE EDGE, as a hypothesis. The two siblings split the price range by token:
cheap_already_happened buys HAPPENED on markets priced 0.05-0.35, and
expensive_ruled_out fades RULED_OUT on markets priced 0.55-0.92. Both have
research/market 0.00: they almost never fire. The middle is untouched. A yes/no
political event priced 0.35-0.55 several days out is a book that genuinely does
not know, and that is where a single dated report is worth the most: a thing
that already happened, or was called off, turns a coin flip into a near
certainty (a ~40 point move, far above the ~2 point cost). A toss-up is also
where a quiet report is least likely to have been absorbed, because nobody has
a strong prior pulling the quote. Both directions are scored in one candidate
so it gets twice the chance to fire in the fortnight.

WHICH MARKETS (`wants_research`). A politics series (markets.is_politics), a
yes/no EVENT question (the parent's ladder/count/polling-average title
filter), 48-120h to close, spread <= 4c, mid strictly between 0.35 and 0.55.
The price band is disjoint from both siblings (they stop at 0.35 and start at
0.55) and the window starts where politics_already_happened's ends, so no
market is acted on by two of these family members. The call itself is shared
with any candidate asking about the same market.

WHAT IT ASKS. The same local politics judge, one call per market.

HOW IT MAPS. HAPPENED -> max(p, 0.85); RULED_OUT -> min(p, 0.10). Decisive,
because a fractional shift never clears costs and a correct verdict means the
question is nearly decided. CONFLICT and NONE abstain. Asymmetry is
deliberate: wording fine print (does a passing mention count?) makes HAPPENED
slightly less certain than a cancellation, hence 0.85 against 0.10.

WHAT WOULD FALSIFY IT. HAPPENED calls resolving NO more than ~25% of the
time, or RULED_OUT calls resolving YES more than ~20% (judge misreads a
rescheduling, a denial, or a loose mention); markets whose price had already
moved to the verdict before research (the book was never slow); or almost no
non-NONE verdicts in two weeks, in which case the rate, not the edge, is the
finding. Read the first dozen by hand. An edge above ~10 points is outside
every measured bias: leak-check before trusting it.
"""
from __future__ import annotations

import re
from datetime import datetime

from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research.markets import is_politics
from experiments.kalshi_research.types import ResearchContext, parse_verdict

MANIFEST = {
    "candidate_id": "coinflip_settled_by_news",
    "generation": 3,
    "parent_id": "cheap_already_happened",
    "created_at": "2026-10-18T15:05:39.470549+00:00",
    "rationale": (
        "Variant of cheap_already_happened: look 48-120h out at political "
        "event markets priced 0.35-0.55, where a book is least sure, and jump "
        "to 0.85 if dated news says it happened or to 0.10 if ruled out."
    ),
}

MIN_HOURS, MAX_HOURS = 48.0, 120.0
MAX_SPREAD = 0.04
SPREAD_TOL = 1e-9
MIN_PRICE, MAX_PRICE = 0.35, 0.55
HAPPENED_TARGET = 0.85
RULED_OUT_TARGET = 0.10
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
    return MIN_PRICE < market.implied_prob < MAX_PRICE


def forecast(market: MarketSnapshot, context: ResearchContext) -> float:
    p = market.implied_prob
    if not wants_research(market, context.now):
        return p
    verdict, _ = parse_verdict(context.research(
        f"Has this already happened, or been ruled out: {market.title} ({market.ticker})?"
    ))
    if verdict == "HAPPENED":
        return max(p, HAPPENED_TARGET)
    if verdict == "RULED_OUT":
        return min(p, RULED_OUT_TARGET)
    return p
