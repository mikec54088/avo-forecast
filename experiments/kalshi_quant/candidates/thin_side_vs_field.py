"""Lean the way the maker has pulled size, measured against the rest of the event.

THE EDGE. A market maker quoting a whole event posts roughly the same size on
both sides of every leg. When it pulls size from ONE side of ONE leg while its
siblings keep their usual two-sided depth, the likeliest reason is that it
expects that side to be hit -- it is protecting itself from flow it thinks is
informed. A leg whose ASK is thin next to its own bid, in an event where the
siblings' asks are not thin next to their bids, is a leg the maker expects to be
bought; the mirror, a thin bid, is a leg it expects to be sold.

The trigger is the leg's bid/ask depth ratio compared with the MEDIAN of the same
ratio across its quotable siblings. Not the absolute imbalance (that is
book_imbalance_tilt, which reads one book alone and cannot tell a maker's
deliberate lean from a market whose natural shape is lopsided: a 95c favourite
always has a fat bid and a thin ask), not volume share, not a price sum, not
price history. The sibling median is the scale-free baseline -- every leg of the
event shares a maker and a quoting rule, so what is left after dividing it out is
this leg's own deviation.

WHY THIS IS NOT book_imbalance_tilt. That candidate asks whether this book leans.
This one asks whether this book leans MORE THAN ITS SIBLINGS DO, which removes
the structural lean that comes with price level and with one maker's habits.
Depth-against-siblings is listed as unexplored in the brief; depth alone has
three candidates, none of which has a within-event baseline.

THE EVIDENCE. None computed this session, deliberately: the brief forbids a full
scan and the dataset summary carries no depth-ratio breakdown (it gives only
min-side depth p10/median/p90 = 1 / 30 / 808). So this docstring has NO measured
edge, NO series-clustered interval and NO control P&L. It is a hypothesis
written from a mechanism, and the forward score is its first test. What the
summary does say: 82% of observations have >= 2 quoted siblings, so the
baseline exists on most of the population, and the naive P&L of buying any
price band is -0.018 to -0.030, so the null this must beat is a loss of two to
three cents, not zero.

THE CONTROL, to be run on the first forward sample: the same gate with the ratio
condition inverted (leg's ratio within a factor of 2 of the sibling median)
should pay the ordinary band loss of about -0.025. If the ratio gate pays the
same as that control, the signal is decorative. Also split by sign of the lean:
if only the buy side pays and the sell side loses, this is the favourite bias
wearing a depth label, since buying favourites is where the known bias points.

WHERE IT IS WEAK, stated plainly.

  - THE DIRECTION IS A STORY. "Maker pulls the side it expects to be hit" is one
    reading; "the maker simply has less inventory capacity on that side", or "a
    resting order on the thick side is a retail limit order that happens to sit
    there", predict the same thin side with no information in it, or the
    opposite sign. Sign is untested, which is why both signs are traded and
    reported separately rather than only the one that would flatter the story.
  - SIZE FIELDS ARE OFTEN MISSING OR TINY. yes_bid_size / yes_ask_size are None
    on some quotes and the median top-of-book depth is only 30 contracts, so a
    ratio of 5 can be 5 contracts against 1. The depth floor below exists for
    that, and it shrinks the population by an amount I have not measured.
  - ACTION RATE IS UNKNOWN. Needs >= 3 quotable siblings with sizes, a tight
    book, a mid-range price and a ratio outlier. I expect low single-digit
    percent given 82% sibling coverage, but this is a guess, and if it comes in
    under 1% it is unfalsifiable inside a week -- the failure the brief warns of.
  - SIBLING SIZES ARE UP TO SIBLING_TOLERANCE_MIN OLD, so the baseline can lag
    the leg's own book; a leg whose ratio just moved will read as an outlier
    for a boring reason.
  - LADDERS. Nested strike ladders share a maker but not an outcome, and one
    close settles many rungs, so fills here may cluster heavily in few series.
    The interval must be read series-clustered.
  - All numbers absent; nothing in-sample was measured, so INVARIANT #1 has
    nothing to exclude and nothing here is evidence.

WHAT WOULD FALSIFY IT. A forward P&L interval including zero. Distinctly: the
inverted-gate control paying as well as the gate; the sell side losing while the
buy side wins (favourite bias, not depth); or the P&L being indistinguishable
across ratio buckets (2-5, 5-10, 10+), which a real maker-lean signal should
make monotone.

Shift is +/-0.03: it clears the ~2-point cost of crossing, and with no measured
edge to anchor on it is chosen as the smallest move that could pay, not fitted."""
from __future__ import annotations

from statistics import median

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "thin_side_vs_field",
    "generation": 19,
    "parent_id": "fielded_favourite",
    "created_at": "2026-10-04T10:56:41.137297+00:00",
    "rationale": (
        "A maker pulls size from the side of a leg it expects to be hit. Read "
        "the leg's bid/ask depth ratio against the median ratio of its "
        "siblings, so a lopsided book that is merely structural (favourite, "
        "one maker's habit) is divided out. Thin ask versus the field: buy; "
        "thin bid versus the field: sell. Untested hypothesis, no measured "
        "edge; both signs traded so the sign can be checked."
    ),
}

EPS = 0.001
SPREAD_TOL = 1e-9

MIN_PRICE = 0.10        # in the tails the book shape is mostly price level
MAX_PRICE = 0.90
MAX_SPREAD = 0.04       # cost gate
MIN_SIBLINGS = 3        # a median of fewer is just one other leg's habit
MIN_DEPTH = 5.0         # contracts on each side; below this a ratio is noise
LEAN_FACTOR = 4.0       # own ratio must differ from the field's by this factor
SHIFT = 0.03


def _ratio(bid_size: float | None, ask_size: float | None) -> float | None:
    """Bid depth over ask depth, or None when either side is unreadable."""
    if bid_size is None or ask_size is None:
        return None
    if bid_size < MIN_DEPTH or ask_size < MIN_DEPTH:
        return None
    return bid_size / ask_size


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob

    if market.spread > MAX_SPREAD + SPREAD_TOL or not (MIN_PRICE <= p <= MAX_PRICE):
        return p

    own = _ratio(market.yes_bid_size, market.yes_ask_size)
    if own is None:
        return p

    field_ratios = [
        r
        for s in context.siblings
        if s.has_two_sided_book
        for r in [_ratio(s.yes_bid_size, s.yes_ask_size)]
        if r is not None
    ]
    if len(field_ratios) < MIN_SIBLINGS:
        return p

    # Scale-free: how far this leg's lean sits from the event's typical lean.
    relative = own / median(field_ratios)

    if relative >= LEAN_FACTOR:
        # Bid is fat against the field's norm, ask thin: maker expects buying.
        return min(p + SHIFT, 1.0 - EPS)
    if relative <= 1.0 / LEAN_FACTOR:
        # Bid is thin against the field's norm: maker expects selling.
        return max(p - SHIFT, EPS)
    return p
