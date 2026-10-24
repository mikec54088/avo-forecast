"""Buy a cheap political event a week out when dated news says it already happened.

A variant of cheap_already_happened (2026-10-18) that changes WHEN it looks, not
what it asks or how hard it moves.

THE EDGE, as a hypothesis. Every family member looks inside 120h of the close,
and cheap_already_happened's own falsifier admits the rate may be the problem
(research/market 0.00: it almost never fires). A longer question period helps.
"Will Trump say X before the 25th?" is a week-long window; the sooner in that
window he says it, the longer the market stays open priced as if he might not.
A report that it already happened on day 1 or 2 turns a cheap lottery-ticket
quote into a near-certainty, a ~60 point move against a ~2 point cost, and a
thin low-priced book is where a quiet mention is least likely to have been
absorbed. The window is 48h wide (120-168h; markets.POLITICS_HORIZON is 7
days), so it also reaches markets none of the siblings can.

WHICH MARKETS (`wants_research`). A politics series (markets.is_politics), a
yes/no EVENT question (the family's ladder/count/polling-average title
filter), MORE than 120h and at most 168h to close, spread <= 4c, mid
0.05-0.40. politics_already_happened (<=48h) and cheap_already_happened,
coinflip_settled_by_news (48-120h) never overlap in time; expensive_ruled_out
reaches 144h but only in the 0.55+ band, which this never enters. So no market
is acted on twice. Correlation is still real: the same "Trump say" series feeds
all of them, so size the family as one position.

WHAT IT ASKS. The same local politics judge, one call per market, shared with
any candidate asking about the same market.

HOW IT MAPS. HAPPENED -> max(p, 0.85), decisive. Everything else abstains:
RULED_OUT on a market this cheap was expected, CONFLICT and NONE are not
evidence. The target is a little above cheap_already_happened's 0.80 because a
report of a thing that happened with days still on the clock is the clearest
case there is; it is still below 1 for wording fine print and a 9B judge.

WHAT WOULD FALSIFY IT. HAPPENED calls resolving NO more than ~25% of the time
(the judge misreads a passing mention, or the wording trap is real, e.g. the
question wants a second occurrence); HAPPENED on markets whose price had
already risen before research (the book was never slow); or almost no HAPPENED
verdicts in two weeks, in which case the rate is the finding. Read the first
dozen by hand. An edge above ~10 points is outside every measured bias:
leak-check before trusting it.
"""
from __future__ import annotations

import re
from datetime import datetime

from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research.markets import is_politics
from experiments.kalshi_research.types import ResearchContext, parse_verdict

MANIFEST = {
    "candidate_id": "week_ahead_happened",
    "generation": 3,
    "parent_id": "cheap_already_happened",
    "created_at": "2026-10-18T15:06:32.313233+00:00",
    "rationale": (
        "Variant of cheap_already_happened: look 120-168h out at political "
        "event markets priced 0.05-0.40, where the question period is longest, "
        "and if dated news says the event already happened, jump to 0.85."
    ),
}

MIN_HOURS, MAX_HOURS = 120.0, 168.0
MAX_SPREAD = 0.04
SPREAD_TOL = 1e-9
MIN_PRICE, MAX_PRICE = 0.05, 0.40
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
