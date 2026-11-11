"""Jump decisively, not halfway, when dated news settles a political event in its last 48h.

A variant of cheap_already_happened (2026-10-25) that changes WHEN it looks and
takes both tokens at the edges of the price range.

THE EDGE, as a hypothesis. The family splits by time and price, but the final
48h is only covered by politics_already_happened, which moves HAPPENED halfway
to 1 and RULED_OUT halfway to 0. On a cheap market (0.15 -> 0.575) that is a
real move; on an expensive one (0.85 -> 0.925) it is a rounding error inside
the ~2 point cost, and in both cases it is a hedge on a question whose answer,
if the judge is right, is already known. cheap_already_happened's 48h floor and
the siblings' windows leave this stretch with no decisive bet. The endgame is
where a settled-but-unpriced event is most valuable: nothing is left to happen,
the book has one last chance to be lazy, and a thin lottery-ticket quote or a
stale "scheduled" quote sits until the close. A verdict that is right moves
the fair price 50+ points against a ~2 point cost. Only edge-of-range prices
are used, where the judge's answer is most surprising to the book.

WHICH MARKETS (`wants_research`). A politics series (markets.is_politics), a
yes/no EVENT question (the family's ladder/count/polling-average title filter),
between 3h and 48h to close (under 3h the pass cadence and the 2-4 minute call
leave no time to act), spread <= 4c, and mid in 0.05-0.35 (looking for
HAPPENED) or 0.65-0.92 (looking for RULED_OUT). The middle band belongs to the
parent's halfway move and the book there is not obviously wrong either way.
This overlaps politics_already_happened's window and acts in the same
direction on the same markets; it is a harder version of that bet, not a
diversifier. Size the two as one position. Research is one call per market,
shared with the parent.

WHAT IT ASKS. The same local politics judge. The query asks only the question
that matters for the band: "already happened" for cheap markets, "ruled out or
called off" for expensive ones (the judge's reply is the same two lines either
way, and the answer depends only on the ticker).

HOW IT MAPS. Cheap band: HAPPENED -> max(p, 0.85). Expensive band: RULED_OUT ->
min(p, 0.15). The opposite token in a band (RULED_OUT on a cheap market,
HAPPENED on an expensive one) was expected, so it abstains, as do CONFLICT and
NONE. 0.85 and 0.15 are not 1 and 0: wording fine print and a 9B judge.

WHAT WOULD FALSIFY IT. HAPPENED calls on cheap markets resolving NO more than
~25% of the time, or RULED_OUT calls on expensive ones resolving YES more than
~25% (the judge misreads a passing mention, a rescheduling or a denial);
calls where the price had already moved to the verdict before research (the
book was never slow); or almost no non-NONE verdicts in two weeks, in which
case the rate, not the edge, is the finding. Read the first dozen by hand. An
edge above ~10 points is outside every measured bias: leak-check before
trusting it.
"""
from __future__ import annotations

import re
from datetime import datetime

from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research.markets import is_politics
from experiments.kalshi_research.types import ResearchContext, parse_verdict

MANIFEST = {
    "candidate_id": "endgame_decisive",
    "generation": 3,
    "parent_id": "cheap_already_happened",
    "created_at": "2026-10-25T15:06:34.792036+00:00",
    "rationale": (
        "Variant of cheap_already_happened: in the final 3-48h, on political "
        "event markets priced at the edges (0.05-0.35 or 0.65-0.92), jump to "
        "0.85 if dated news says it happened (cheap) or to 0.15 if ruled out "
        "(expensive), instead of moving halfway."
    ),
}

MIN_HOURS, MAX_HOURS = 3.0, 48.0
MAX_SPREAD = 0.04
SPREAD_TOL = 1e-9
CHEAP = (0.05, 0.35)
EXPENSIVE = (0.65, 0.92)
UP, DOWN = 0.85, 0.15
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
    return CHEAP[0] <= p <= CHEAP[1] or EXPENSIVE[0] <= p <= EXPENSIVE[1]


def forecast(market: MarketSnapshot, context: ResearchContext) -> float:
    p = market.implied_prob
    if not wants_research(market, context.now):
        return p
    cheap = p <= CHEAP[1]
    ask = "already happened" if cheap else "been ruled out or called off"
    verdict, _ = parse_verdict(context.research(
        f"Has this {ask}: {market.title} ({market.ticker})?"
    ))
    if cheap and verdict == "HAPPENED":
        return max(p, UP)
    if not cheap and verdict == "RULED_OUT":
        return min(p, DOWN)
    return p
