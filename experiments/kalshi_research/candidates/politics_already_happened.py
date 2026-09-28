"""Move a yes/no political market when dated news says it is already settled.

Created 2026-09-28 at the human's request: politics gets its own research
candidate. A founder, not a variant -- nothing before it researches politics,
and the sports judge cannot answer a market that has no sides.

THE EDGE, as a hypothesis. Short-dated political markets ask whether a
discrete thing happens before a close: "Will Trump say 'Voter ID' before Sep
28?", "meet Greg Abbott in September?", "name an AI czar before Oct 2?".
Once it HAS happened, the question is decided but the market stays open until
the close and settlement. If the book is slow to absorb a report of it -- a
quiet mention in a speech, a phone call in a readout -- the quote still shows
room. Same the other way: a report that it was called off.

WHICH MARKETS (`wants_research`). A politics series (markets.POLITICS), the
final 48h before close, a yes/no EVENT question, spread <= 4c, mid 0.03-0.92.
Counts, ladders and polling averages are excluded by title ("at least",
"between", "approval", "posts", "%", ...): a tracker settles those, not a
headline. The runner lets a candidate research a market ONCE, so the window is
late on purpose: most of the question's period has elapsed, so the thing has
had time to happen. Measured over 2026-09-21..27: ~23 markets a week pass
these gates at 48h (16 at 24h, 69 at 120h), about two thirds of them "Trump
say" markets. That is few; most answers will be NONE, which moves nothing.

WHAT IT ASKS. The local politics judge (local_research.politics_judge) over
headlines <= 24h old: HAPPENED (a specific report it already occurred,
satisfying the question's wording), RULED_OUT (reported cancelled or ruled
out), NONE, or CONFLICT. One search and one screen per event, so a 20-market
"say" event costs one call when the screen finds nothing.

HOW IT MAPS. HAPPENED moves halfway to 1; RULED_OUT halves the price. Not all
the way: the evidence is categorical when right, but resolution wording is
fine print and the judge is a 9B model reading headlines. CONFLICT and NONE
abstain.

WHAT WOULD FALSIFY IT. HAPPENED calls that resolve NO (the judge is wrong or
the wording trap is real), or HAPPENED calls on markets already above ~0.9
when researched (the book was never slow). Either shows up in its first dozen
resolved decisions; read them by hand before trusting any skill number.
"""
from __future__ import annotations

import re
from datetime import datetime

from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research.markets import is_politics
from experiments.kalshi_research.types import ResearchContext, parse_verdict

MANIFEST = {
    "candidate_id": "politics_already_happened",
    "generation": 1,
    "parent_id": None,
    "created_at": "2026-09-28T08:30:00+00:00",
    "rationale": (
        "Politics founder: in the final 48h of a yes/no political event market, "
        "ask the local judge whether dated news reports the thing already "
        "happened (move halfway to 1) or was ruled out (halve the price)."
    ),
}

WINDOW_HOURS = 48.0
MAX_SPREAD = 0.04
SPREAD_TOL = 1e-9
MIN_PRICE, MAX_PRICE = 0.03, 0.92
LADDER = re.compile(
    r"\b(?:at least|at most|between|above|below|exactly|more than|fewer than|less than"
    r"|or more|or fewer|number of|approval|average|posts?)\b|%", re.IGNORECASE)


def wants_research(market: MarketSnapshot, now: datetime) -> bool:
    if not is_politics(market.series_ticker) or LADDER.search(market.title):
        return False
    hours_left = (market.close_time - now).total_seconds() / 3600.0
    if not (0.0 < hours_left <= WINDOW_HOURS):
        return False
    if not market.has_two_sided_book or market.spread > MAX_SPREAD + SPREAD_TOL:
        return False
    return MIN_PRICE <= market.implied_prob <= MAX_PRICE


def forecast(market: MarketSnapshot, context: ResearchContext) -> float:
    p = market.implied_prob
    if not wants_research(market, context.now):
        return p
    verdict, _ = parse_verdict(context.research(
        f"Has this already happened, or been ruled out: {market.title} ({market.ticker})?"
    ))
    if verdict == "HAPPENED":
        return p + (1.0 - p) / 2.0
    if verdict == "RULED_OUT":
        return p / 2.0
    return p
