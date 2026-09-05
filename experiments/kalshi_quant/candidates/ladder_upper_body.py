"""Buy the upper body of a monotone threshold ladder: its implied distribution
is too diffuse.

THE EDGE. Many Kalshi events are threshold ladders -- "gas above $3.96", "above
$3.97", "above $3.98"; "over 1.5 goals", "over 2.5". Their legs are ordered by a
number in the ticker and their prices trace a discretised survival curve, which
must be monotone in that number. Read as a distribution over the underlying,
that curve is systematically TOO WIDE. The market spreads probability mass
further into the tails than the outcome warrants, so the rungs sitting on the
likely side of the curve -- priced 0.60 to 0.90 -- are underpriced, and the far
tail below 0.20 is overpriced. This candidate buys the underpriced side only.

The trigger is the leg's position in an ordered ladder, not its price level.
That distinction is the whole point, and the control below is what establishes
it.

THE EVIDENCE. Measured 2026-09-05 over 65,102 resolved observations with a
two-sided book. An event is called a ladder when every leg's ticker ends in a
numeric suffix with a common prefix, there are at least three legs, the legs'
prices are at least 90% concordant with that numeric order, and they sum above
1.2 (so they overlap and are not mutually exclusive). Intervals are bootstrapped
over SERIES, not observations, because legs of one event resolve together.

Across the ladder, by price band (spread <= 0.04):

    p 0.00-0.10   n=3060  edge -0.0152   P&L -0.0331
    p 0.10-0.20   n= 455  edge -0.0215   P&L -0.0400
    p 0.20-0.40   n= 491  edge -0.0213   P&L -0.0400
    p 0.40-0.60   n= 440  edge +0.0302   P&L +0.0113
    p 0.60-0.75   n= 379  edge +0.0753   P&L +0.0574
    p 0.75-0.90   n= 654  edge +0.0762   P&L +0.0579
    p 0.90-1.00   n=3527  edge +0.0195   P&L +0.0019

Mass wants to move from the low rungs to the upper body. That is one coherent
shape rather than a single lucky cell, which is the main reason to believe it.

The traded subset, 0.60 <= p < 0.90:

    n=1033  ser=97   edge +0.0759  CI [+0.0445, +0.1338]
                     P&L  +0.0577  CI [+0.0253, +0.1143]   (buy the ask, +1c fee)

THE CONTROL, which is what makes this a claim about ladders rather than about
favourites. Take every fillable market priced 0.60-0.80 and split by event
structure:

    not an ordered event          n=1889 ser=210  edge +0.0136  P&L -0.0099
    ordered but sums <= 1.2       n= 294 ser= 63  edge +0.0334  P&L +0.0055
    ladder, weakly monotone       n= 184 ser= 49  edge +0.0459  P&L +0.0169
    ladder, strongly monotone     n= 784 ser= 96  edge +0.0728  P&L +0.0489

The same price band earns nothing outside a ladder -- P&L is negative there, and
only the strongly monotone cell has an interval clear of zero. This is the
favourite-longshot SHAPE, but at roughly twice the size, in a population that
can be identified in advance, which is the difference between a 4-point bias
that cannot pay the ~2-point cost of crossing and a 7-point one that can.

It is not ladder_leader wearing different constants. Removing every observation
that ladder_leader would also take (leads the next rung by >= 0.25) leaves
n=934 across 71 series at edge +0.0716, P&L +0.0534, CI [+0.0207, +0.1165]. The
overlap is 99 observations and scores better on its own, so the two agree where
they meet, but this candidate does not depend on that slice.

Robustness. Every threshold was swept around a 0.60-0.80 / spread <= 0.08 base,
and every variation keeps P&L positive: band low edge 0.50/0.55/0.60/0.65 and
high edge 0.75/0.80/0.85/0.90 span +0.036 to +0.049; concordance cut
0.80/0.85/0.90/0.95/1.00 gives +0.046/+0.050/+0.049/+0.064/+0.064; sum cut 1.0
through 2.0 gives +0.043 to +0.051; spread cap 0.02/0.03/0.04/0.06/0.08 gives
+0.081/+0.074/+0.065/+0.046/+0.049. It is not perched on one lucky cell.

Nor on one series, though this is the weakest part of the case. On the shipped
configuration, dropping any of the eight largest series leaves P&L between
+0.0504 and +0.0772, every interval still clear of zero.

WHY SIBLINGS ARE USED ONLY TO CLASSIFY. An earlier version of this idea traded
monotonicity VIOLATIONS -- a higher-threshold rung quoted above a lower one is a
logical impossibility, so one of the two must be wrong. Measured on the same
data, that lost money in every band and both directions: buying the apparently
underpriced rung returned -0.026 to -0.049 per contract. Siblings are quoted up
to 20 minutes before the entry (SIBLING_TOLERANCE_MIN), and that asynchrony is
enough to manufacture violations that are not there. So this candidate never
prices a leg off a sibling's number; it only uses the sibling set to decide what
KIND of event this is, which is a classification stale quotes do not disturb.

WHERE IT IS WEAK, stated plainly:

  - The ladder test is a ticker-string heuristic. It reads the numeric suffix
    after the last "-", so a negative threshold loses its sign, and any event
    whose legs are not named this way is simply invisible. It abstains rather
    than guessing, but it is parsing conventions, not semantics.
  - The mechanism is inferred, not shown. "The implied distribution is too
    diffuse" fits the profile across all seven bands, but I have not established
    that the curve's width is what causes it. A market-maker quoting a ladder
    from one volatility number that is set too high would produce exactly this,
    and so would several other stories.
  - The subset leans hard on crypto. Daily Bitcoin, Ether and Solana threshold
    ladders are 58% of the 1,033 observations, and KXBTCD alone is 33%. The
    intervals quoted here are clustered over series precisely so that
    concentration is priced in, and dropping KXBTCD still leaves P&L +0.0640
    CI [+0.0209, +0.1239] -- but "the market misprices ladders" is really
    "the market misprices crypto ladders, and some sports totals agree". If
    crypto ladders are one market maker with one volatility setting, this is one
    observation dressed up as 97 series.
  - It was found by slicing. I tested volume-confirmed drift, sibling dominance,
    bin smoothness and monotonicity violations first, and all four failed. The
    intervals here are not corrected for that search, so treat +0.058 as the
    optimistic end of what a forward test should show.
  - Every number above is in-sample: these observations resolved before this
    file's created_at, so INVARIANT #1 excludes all of them from its score. This
    docstring is a hypothesis with arithmetic attached, not a result.

WHAT WOULD FALSIFY IT. A forward P&L interval that includes zero on this subset.
It is separately falsified if the low rungs stop being overpriced while the
upper body stays underpriced -- the diffuse-distribution story requires both
ends to move together, and if only one holds then the shape is a coincidence and
something narrower is doing the work. It is also falsified if the same 0.60-0.90
band starts paying on non-ladder events, which would mean this is the ordinary
favourite bias and the ladder test is decoration.

Shift is +0.04 against a measured +0.0759, taking well under the low end of the
interval, on the same reasoning as favourite_longshot and ladder_leader:
carrying a fitted value into a forward test is how in-sample fitting sneaks back
in. It still clears the ~2-point cost of crossing by a factor of two.

Abstains on 98.4% of observations by construction, so its whole-population Brier
skill will look like nothing next to candidates that nudge every market. That is
the intended shape.

Parent is control_middle_only, which is where the reasoning starts: that control
showed a shift applied on price level alone earns nothing even in the band that
measures calibrated, so price level is not the axis to condition on. This
candidate keeps the shift and changes the axis to event structure."""
from __future__ import annotations

import re

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "ladder_upper_body",
    "generation": 3,
    "parent_id": "control_middle_only",
    "created_at": "2026-09-05T05:27:06.894726+00:00",
    "rationale": (
        "Threshold ladders price a survival curve that is too diffuse, so rungs "
        "at 0.60-0.90 are underpriced by ~7.6 points while the tail below 0.20 "
        "is overpriced. The same band earns nothing outside a ladder. Siblings "
        "classify the event only; they never price the leg. Abstains on 98.4%."
    ),
}

EPS = 0.001

MIN_SIBLINGS = 2        # a 3+ leg event; two legs cannot show a monotone shape
MIN_EVENT_SUM = 1.2     # above this the legs overlap, so they are not exclusive
MIN_CONCORDANCE = 0.90  # how well price order tracks threshold order
LOW_PRICE = 0.60        # the upper body starts here
HIGH_PRICE = 0.90       # above this there is no room left to be underpriced by
MAX_SPREAD = 0.04       # the cost gate; the same one ladder_leader uses
SHIFT = 0.04

# The numeric suffix of a ladder leg: "T6.68", "B78750", "3.9600", "-5".
_SUFFIX = re.compile(r"^([A-Za-z]{0,3})(-?\d+(?:\.\d+)?)$")


def _rung(ticker: str) -> tuple[str, float] | None:
    """The leg's (prefix, threshold), or None if the ticker is not a ladder leg.

    Deliberately literal: split on the last "-" and parse what follows. That
    drops the sign of a negative threshold, which costs a few events and never
    manufactures an ordering that is not there.
    """
    m = _SUFFIX.match(ticker.rsplit("-", 1)[-1])
    return (m.group(1).upper(), float(m.group(2))) if m else None


def _concordance(legs: list[tuple[float, float]]) -> float:
    """Fraction of leg pairs whose price order agrees with the majority order.

    1.0 is a perfectly monotone ladder. Ties contribute to neither side, so a
    ladder pinned at 0.99 across several rungs is judged on the rungs that
    actually differ. Direction is not fixed: "above X" ladders fall with the
    threshold and "below X" ladders rise, and both are equally monotone.
    """
    up = down = 0
    for i in range(len(legs)):
        for j in range(i + 1, len(legs)):
            if legs[i][1] < legs[j][1]:
                up += 1
            elif legs[i][1] > legs[j][1]:
                down += 1
    total = up + down
    return 0.0 if total == 0 else max(up, down) / total


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob

    # Cost gate first. Nothing below matters on a book we cannot cross cheaply,
    # and simulate_fill rejects a spread above 0.08 outright.
    if market.spread > MAX_SPREAD or not (LOW_PRICE <= p < HIGH_PRICE):
        return p

    mine = _rung(market.ticker)
    if mine is None:
        return p
    prefix, threshold = mine

    # Every quotable sibling must be a ladder leg of the same family. A mixed
    # or unparseable set means the event is not a ladder we can read, and
    # reading it anyway would order legs that have no order.
    legs: list[tuple[float, float]] = [(threshold, p)]
    for s in context.siblings:
        if not s.has_two_sided_book:
            continue
        other = _rung(s.ticker)
        if other is None or other[0] != prefix:
            return p
        legs.append((other[1], s.implied_prob))

    if len(legs) < MIN_SIBLINGS + 1:
        return p

    # Overlapping legs, so the event is a ladder rather than a set of exclusive
    # outcomes. The test is one-sided: a sum near 1.0 may be an exclusive event
    # or a ladder with legs missing, and those are not distinguishable here.
    if sum(price for _, price in legs) <= MIN_EVENT_SUM:
        return p

    legs.sort()
    if _concordance(legs) < MIN_CONCORDANCE:
        return p

    return min(p + SHIFT, 1.0 - EPS)
