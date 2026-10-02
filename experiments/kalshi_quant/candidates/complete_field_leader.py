"""Buy the leader of an event whose legs demonstrably form a complete, exclusive field.

THE EDGE. The favourite-longshot story is about choice among alternatives, so it
should live where the alternatives are REAL: one winner, every contender quoted.
The parent (fielded_favourite) only asks that a quotable sibling EXISTS. That
admits nested ladders ("over 1.5 / 2.5 / 3.5"), where legs overlap and a leg's
neighbours say nothing about its rivals, and partial captures, where the field is
missing legs. This candidate reads the AGGREGATE, context.sibling_sum, which no
earlier candidate has used, to check the structure before trusting it: if the
quoted legs (this one plus its siblings) sum to about 1.0 they are, by
arithmetic, an exhaustive set of exclusive outcomes with nothing missing. Within
such a field it buys only the LEADER -- the single leg with the highest midpoint.

Why the leader and not every favourite: in an exclusive field the leader's price
is the market's claim that one contender takes most of the mass. Being the top of
a field that sums to 1 is a relative fact the price level alone does not carry
(0.62 leads a five-way race and trails a two-way one), which is the piece a
price-only candidate cannot see. Reads: midpoint, spread, sibling midpoints and
their sum. Not volume, not history, not depth.

THE EVIDENCE. None measured. The DATASET summary gives the shape only: 63,109
multi-leg events, median leg-price sum 1.04, 17% below 0.95 and 49% above 1.05.
So roughly a third of events sit in 0.95-1.05 and are candidates for the
"complete field" label, and the parent's rationale gives the price of admission
(favourite with a sibling ~+4.7 points, isolated ~+1.0). I did NOT load the
entries store to split that by sum or by leader rank, so there is no
series-clustered interval here to quote. This is a hypothesis with a
mechanism, not a measured effect.

THE CONTROL, to be run on forward data: the same band and spread on legs that
are NOT the leader of a complete field (sum outside the window, or not top of
its event). If the leader does not beat that control, the sum test is decoration.
A second control: sum above 1.06 (overlapping legs) with the same leader rule --
that is the parent's population minus the structure, and should pay like it.

ACTION RATE. Not measured; my estimate is 2-5% of observations (about one leg
per exclusive event, in the ~third of events with a complete field, restricted
to a leader priced 0.55-0.92 on a book of at most 4 points). If it is under 1%
it will be unfalsifiable inside a week and that is a defect to report.

WHAT WOULD FALSIFY IT. A forward P&L interval, series-clustered, that includes
zero; or the leader-of-complete-field subset paying no more than the
non-leader control; or the edge concentrating in one series family whose maker
sets the sum (a two-way moneyline sums near 1 by construction, so two-leg fields
are excluded via MIN_LEGS).

WHERE IT IS WEAK.
  - A sum near 1.0 can also come from a nested ladder that happens to add up,
    or from an incomplete capture offset by an overround. The window is a
    filter, not a proof.
  - Two-leg events, where "leader" is just "favourite", are excluded by
    MIN_LEGS = 3, which also cuts its population.
  - Sibling quotes are as of about the same instant, not exactly; a fast market
    can move the sum a point or two inside the window.
  - Shift is +0.03, a round number below the parent's ~4.7 point figure, not a
    fitted one; it clears the ~2-point cost only if the hypothesis holds.
  - The mirror (fade the trailing legs of a complete field) is the classic
    longshot overpricing, right in direction and below the spread hurdle, so it
    is abstained on.
  - All in-sample reasoning; INVARIANT #1 excludes anything I could have seen.
"""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "complete_field_leader",
    "generation": 14,
    "parent_id": "fielded_favourite",
    "created_at": "2026-09-30T10:55:30.978272+00:00",
    "rationale": (
        "Favourite-longshot bias needs a real field. Use the sibling sum to "
        "verify the legs form a complete exclusive set (sum ~1.0, 3+ legs), "
        "then buy only the leader of that field. Structure, not price level, "
        "selects the market; the parent only checked a sibling existed."
    ),
}

EPS = 0.001

MIN_LEGS = 3          # this leg plus two quotable siblings; two-way is just "favourite"
SUM_LO = 0.97         # below this a leg is missing or the field is loose
SUM_HI = 1.06         # above this the legs overlap (nested ladder), not exclusive
MIN_PRICE = 0.55      # a leader below this leaves little bias to collect
MAX_PRICE = 0.92      # above this there is no room to be underpriced
MAX_SPREAD = 0.04     # cost gate
SHIFT = 0.03


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob

    if market.spread > MAX_SPREAD or not (MIN_PRICE <= p < MAX_PRICE):
        return p
    if not market.has_two_sided_book:
        return p

    quotable = [s for s in context.siblings if s.has_two_sided_book]
    if len(quotable) + 1 < MIN_LEGS:
        return p

    # The aggregate nobody else reads: the whole field, this leg included.
    total = p + context.sibling_sum
    if not (SUM_LO <= total <= SUM_HI):
        return p

    # Leader only. A tie is not a lead.
    if any(s.implied_prob >= p for s in quotable):
        return p

    return min(p + SHIFT, 1.0 - EPS)
