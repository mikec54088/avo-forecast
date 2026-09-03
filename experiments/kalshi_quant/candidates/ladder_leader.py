"""Buy the loosest rung of a nested threshold ladder when it clearly leads.

THE EDGE. Not every Kalshi event is mutually exclusive. Many are nested
ladders -- "over 1.5 goals", "over 2.5 goals", "over 3.5 goals" on one match --
where the legs are ordered by inclusion and several can settle yes together.
Their implied probabilities correctly sum well above 1.0. In those events the
loosest rung, when it leads the next rung by a wide margin, is systematically
underpriced. That is a different population from the favourite-longshot
shoulder: the trigger is the leg's position in the ladder, not its price level.

THE EVIDENCE. Measured 2026-09-03 over 56,838 resolved observations with a
two-sided book, 23,451 of which carry at least two quotable siblings. Confidence
intervals are bootstrapped over SERIES, not observations, because legs of one
match resolve together and independent intervals would be far too tight.

  dominant leader, non-exclusive event    n=  176  ser=87
      actual - mid  +0.1030  CI [+0.0598, +0.1436]
      P&L/contract  +0.0839  CI [+0.0407, +0.1246]   (buy the ask, +1c fee)

The comparison that matters is against the same event type without dominance,
and against the same rule on genuinely exclusive events:

  ladder, leads by >= 0.25    +0.1030 edge   +0.0839 P&L   <- this candidate
  ladder, leads by  < 0.25    +0.0459 edge   +0.0251 P&L   CI spans zero
  ladder, not the leader      -0.0040 edge   -0.0231 P&L
  exclusive event, leads >= 0.25  +0.0344   +0.0155 P&L   CI spans zero

So both conditions carry weight. Dominance without the ladder is not enough,
and the ladder without dominance is not enough.

Unusually for this project the P&L interval EXCLUDES zero. The governing
constraint is that crossing the spread plus fees costs about 2 points and every
bias measured so far was 1-3 points; this subset is the first at ~10 points,
which is what makes it worth trading rather than merely worth noticing.

Robustness: every neighbouring threshold keeps a P&L interval clear of zero --
gap 0.15/0.20/0.25/0.30 gives +0.074/+0.075/+0.084/+0.089, sum cut
1.05/1.1/1.2/1.3 gives +0.078/+0.084/+0.106/+0.112, spread cap 0.02 through
0.06 gives +0.085/+0.080/+0.084/+0.068. It is not perched on one lucky cell.
No single series carries it either: the largest contributes 5% of the sample,
and dropping any one series leaves P&L between +0.030 and +0.035 on the wider
gap>=0.25 set.

WHERE IT IS WEAK, stated plainly:

  - n=176. That is small, and it was found by slicing a larger table -- I looked
    at leader-vs-trailer first, then price bands, then the exclusive/ladder
    split. The interval is not corrected for that search. Treat +0.10 as the
    optimistic end of what a forward test should be expected to show.
  - All of it is in-sample. These observations resolved before this file's
    created_at, so INVARIANT #1 excludes every one of them from its score. The
    forward test is genuinely out of sample; this docstring is a hypothesis with
    arithmetic attached, not a result.
  - The mechanism is a guess. "Ladder legs price coherently in aggregate while
    the near-certain rung lags" is consistent with the numbers, but I have not
    shown the lag is what causes them. An equally good story is that these
    events are simply less traded on the loose end.

WHAT WOULD FALSIFY IT. A forward P&L interval that includes zero on this subset
kills it -- there is no thinner slice to retreat to, since the subset is already
0.31% of observations. It is also falsified, differently, if `ladder, not the
leader` or `exclusive event, leads >= 0.25` scores as well as this rule does
forward: that would mean the conditioning is decoration and the edge is
something broader that dominance merely correlates with here.

Note this trades DIRECTLY AGAINST sibling_coherence, deliberately. That
candidate reads a sum above 1.0 as the event being collectively overpriced and
shifts each leg down; on a ladder priced to 1.2 it sells exactly the leg this
one buys. Both cannot be right, and scoring them together settles whether an
above-1.0 sum means mispricing or just means the legs overlap.

Shift is +0.06 against a measured +0.1030 -- the low end of the interval rather
than the point estimate, on the same reasoning as favourite_longshot: carrying a
fitted value into a forward test is how in-sample fitting sneaks back in. It is
still comfortably above the ~2-point cost of crossing.

Abstains on 99.7% of observations by construction, so its whole-population Brier
skill is +0.0002 and it will look like nothing next to candidates that nudge
every market. That is the intended shape: acting on 0.31% of markets at ten
points beats nudging all of them at one and paying the spread each time."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "ladder_leader",
    "generation": 2,
    "parent_id": "sibling_coherence",
    "created_at": "2026-09-03T18:45:00+00:00",
    "rationale": (
        "In non-exclusive events (nested threshold ladders, legs summing well "
        "above 1.0), the loosest rung is underpriced when it leads the next by "
        "a wide margin. Abstains on 99.7% of markets; trades against "
        "sibling_coherence on purpose."
    ),
}

EPS = 0.001

MIN_SIBLINGS = 2        # a 3+ leg event; a two-leg event has no ladder structure
MIN_EVENT_SUM = 1.1     # above this the legs overlap, so they are not exclusive
MIN_LEAD = 0.25         # margin over the next rung down
MAX_PRICE = 0.90        # above this there is no room left to be underpriced by
MAX_SPREAD = 0.04       # the cost gate; a wider book eats the edge
SHIFT = 0.06


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob

    # Cost gate first. Everything below is irrelevant on a book we cannot cross
    # cheaply, and simulate_fill rejects a spread above 0.08 outright.
    if market.spread > MAX_SPREAD or p >= MAX_PRICE:
        return p

    quotable = [s.implied_prob for s in context.siblings if s.has_two_sided_book]
    if len(quotable) < MIN_SIBLINGS:
        return p

    # A partial set of legs sums low for a boring reason, so the test is
    # one-sided: only a sum clearly ABOVE 1.0 proves the legs overlap. A sum
    # near 1.0 may be an exclusive event or may be a ladder with legs missing,
    # and those are not distinguishable from here.
    if p + sum(quotable) <= MIN_EVENT_SUM:
        return p

    # Strict leader, by a wide margin over the next rung.
    runner_up = max(quotable)
    if p - runner_up < MIN_LEAD:
        return p

    return min(max(p + SHIFT, EPS), 1.0 - EPS)
