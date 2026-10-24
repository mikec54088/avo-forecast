"""Fade a political event priced as likely when dated news says it was ruled out.

A variant of politics_already_happened (2026-10-11) that takes the OTHER token
on the OTHER end of the price range. cheap_already_happened takes HAPPENED on
cheap markets; this takes RULED_OUT on expensive ones, so the two never act on
the same market.

THE EDGE, as a hypothesis. A yes/no political event priced 0.55-0.92 several
days out is a book that expects the thing to happen: a scheduled meeting, a
planned nomination, a vote. Those are the questions a headline can kill -- the
meeting is cancelled, the nominee withdraws, the vote is pulled. Cancellation
news is something few holders rush to act on, and a quote set before it can
sit stale on the high side. The parent halves a RULED_OUT price, but only
inside the final 48h, where a cancelled event has usually already been
repriced or has already closed. Looking earlier (48-144h) leaves the book
time to be wrong. Cost still applies: only a large move clears ~2 points.

WHICH MARKETS (`wants_research`). A politics series (markets.is_politics), a
yes/no EVENT question (the parent's ladder/count/polling-average title
filter), 48-144h to close, spread <= 4c, mid 0.55-0.92. Below 0.55 the book
does not expect the event, so "ruled out" is no surprise. Markets under 48h
belong to the parent; this one waits for the window before it. Some mid-priced
"Trump say X" markets will pass too; that is accepted, and falsifiable below.

WHAT IT ASKS. The same local politics judge, one call per market, shared with
any candidate asking about the same market.

HOW IT MAPS. RULED_OUT -> p * 0.4, a decisive fade: a correct "cancelled"
report puts the fair price near zero, and a fractional shift never clears the
cost. HAPPENED, CONFLICT and NONE abstain. HAPPENED on a market already priced
high is expected, not news.

WHAT WOULD FALSIFY IT. RULED_OUT calls resolving YES more than ~30% of the
time (the judge misreads a rescheduling or a denial, or the event gets
rebooked inside the window); almost no RULED_OUT verdicts in two weeks, in
which case the rate, not the edge, is the finding; or RULED_OUT markets whose
price had already fallen before research (the book was never slow). Read the
first dozen by hand. A claimed edge above ~10 points is outside every measured
bias: leak-check before trusting it.
"""
from __future__ import annotations

import re
from datetime import datetime

from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research.markets import is_politics
from experiments.kalshi_research.types import ResearchContext, parse_verdict

MANIFEST = {
    "candidate_id": "expensive_ruled_out",
    "generation": 2,
    "parent_id": "politics_already_happened",
    "created_at": "2026-10-11T15:06:50.518040+00:00",
    "rationale": (
        "Variant of politics_already_happened: look 48-144h out at event "
        "markets priced 0.55-0.92, and if dated news says the event was ruled "
        "out, fade decisively to 40% of the price instead of halving it."
    ),
}

MIN_HOURS, MAX_HOURS = 48.0, 144.0
MAX_SPREAD = 0.04
SPREAD_TOL = 1e-9
MIN_PRICE, MAX_PRICE = 0.55, 0.92
FADE = 0.4
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
        f"Has this been ruled out or called off: {market.title} ({market.ticker})?"
    ))
    if verdict == "RULED_OUT":
        return p * FADE
    return p
