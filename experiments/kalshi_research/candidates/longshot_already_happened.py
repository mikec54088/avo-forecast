"""Buy a deep-longshot political event when dated news says it already happened.

A variant of cheap_already_happened (2026-10-25) that changes WHICH PRICE BAND
it looks at: the one the whole family leaves out.

THE EDGE, as a hypothesis. Every sibling starts at 0.03-0.05 and works upward
(cheap_already_happened and week_ahead_happened floor at 0.05, the parent at
0.03). Below that is the deepest tail of the politics books: "Will X say Y
before Friday?" quoted at 1-4c because it looked unlikely on Monday. These are
the quotes nobody maintains -- a resting 2c bid on a question that is a
one-line search from being settled. If a dated report says it already
happened, the fair price is ~0.9, a ~85 point move against a ~2 point cost,
and the gap is largest exactly where the book has the least reason to have
looked. The price of being wrong is small too: the stake is the 2-4c ask, so
a false HAPPENED loses a few cents, while a true one is worth the rest of the
dollar. That asymmetry is why the target is a jump, not a halfway move.

WHICH MARKETS (`wants_research`). A politics series (markets.is_politics), a
yes/no EVENT question (the family's ladder/count/polling-average title
filter), 24h-168h to close (a full week, the width of markets.POLITICS_HORIZON,
because a longshot that already happened is most valuable while many days
remain; under 24h the pass cadence and a 2-4 minute call leave no time to
act), a two-sided book with spread <= 3c, and mid 0.01-0.05 (inclusive of 0.05
so it hands over to the siblings without a gap; the overlap is a single price
point). Research is one call per market and the answer is shared with every
sibling asking about the same ticker. It acts in the same direction as the
siblings, only lower in price: size the family as one position.

WHAT IT ASKS. The same local politics judge ("has this already happened?").
The query text is ignored apart from the "(TICKER)".

HOW IT MAPS. HAPPENED -> max(p, 0.85). Every other token abstains: RULED_OUT
on a 2c market was expected, and CONFLICT/NONE are not evidence. 0.85 and not
1.0 allows for wording fine print and a 9B judge.

WHAT WOULD FALSIFY IT. HAPPENED calls resolving NO more than ~25% of the time
(the judge misreads passing mentions, forecasts or denials as events);
HAPPENED calls on markets whose price had already risen before the research
(the book was never slow); or almost no HAPPENED verdicts in two weeks, in
which case the rate, not the edge, is the finding -- the likely outcome, since
siblings that look higher up have fired almost never. Read the first dozen by
hand. An edge above ~10 points is outside every measured bias: run
scripts/leak_check.py before trusting it.
"""
from __future__ import annotations

import re
from datetime import datetime

from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research.markets import is_politics
from experiments.kalshi_research.types import ResearchContext, parse_verdict

MANIFEST = {
    "candidate_id": "longshot_already_happened",
    "generation": 3,
    "parent_id": "cheap_already_happened",
    "created_at": "2026-10-25T15:07:23.216505+00:00",
    "rationale": (
        "Variant of cheap_already_happened: look at the 0.01-0.05 deep-longshot "
        "politics markets the family skips, 24-168h out, and if dated news says "
        "the event already happened, jump to 0.85."
    ),
}

MIN_HOURS, MAX_HOURS = 24.0, 168.0
MAX_SPREAD = 0.03
SPREAD_TOL = 1e-9
MIN_PRICE, MAX_PRICE = 0.01, 0.05
TARGET = 0.85
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
