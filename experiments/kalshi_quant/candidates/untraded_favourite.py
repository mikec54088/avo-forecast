"""Buy the favourite in a market where no contract has EVER changed hands.

THE EDGE. A market with zero volume and zero open interest has a quoted book
that nobody has ever hit. INVARIANT #2 scores every candidate against "the
market's implied probability", but in these markets there is no market belief to
score against -- the midpoint is one or two makers' posted opinion, and no trade
has ever tested it. Those unhit quotes are shaded, and shaded in the direction
the favourite-longshot literature predicts: on the high side the offer sits
about 12 points below where the outcome lands, against about 3 points for a
market that has traded at all.

The trigger is that lifetime volume is exactly zero. Not low volume -- zero.
That distinction is the whole hypothesis and the control below is what tests it.

WHY THIS IS NOT ITS PARENT. volume_weighted treats volume as a continuous proxy
for how much a market knows: deviate more from the quiet ones, less from the
busy ones. It scored a near-null and concluded, in its own docstring, that the
mispriced markets are the untradeable ones, because quiet markets are wide
(median spread 0.11 against 0.01). Both halves of that turn out to be wrong at
the endpoint. The error is not a gradient in volume with zero at one end of it;
it is a discontinuity AT zero, and a slice of the never-traded markets are
quoted inside three cents.

THE EVIDENCE. Measured 2026-09-05 over all 65,225 scoreable entries. Intervals
are bootstrapped over SERIES, not observations, because markets in one series
share an underlying and a day. P&L crosses the spread at the ask and pays the 1c
fee, per INVARIANT #4.

  never traded, 0.65 <= mid < 0.95, spread <= 0.03, offer >= 4 contracts
      n=61  ser=29
      actual - mid  +0.1197  CI [+0.0460, +0.1784]
      P&L/contract  +0.0997  CI [+0.0268, +0.1588]

  same band, same spread, same depth, but the market HAS traded
      n=3127 ser=281
      actual - mid  +0.0342  CI [+0.0196, +0.0475]
      P&L/contract  +0.0163  CI [+0.0015, +0.0293]

The control is not a null. It is the ordinary favourite bias, which is positive
and which nine candidates have already failed to turn into money because ~2
points of it goes to the spread. The claim is that an unhit quote carries about
three times as much of it, which is the difference between an edge the crossing
cost eats and one it does not.

THE CONTROL THAT MATTERS, since the parent is a volume candidate. Hold the price
band, the spread cap and the depth requirement fixed and walk the volume axis:

    volume == 0          n=  61  edge +0.1197   P&L +0.0997
    volume 0 - 10        n=  30  edge +0.0088   P&L -0.0130
    volume 10 - 100      n= 109  edge +0.0028   P&L -0.0175
    volume 100 - 1k      n= 369  edge +0.0375   P&L +0.0173
    volume 1k - 10k      n=1018  edge +0.0416   P&L +0.0229
    volume > 10k         n=1601  edge +0.0313   P&L +0.0146

A market that has traded thirty contracts behaves like a market that has traded
thirty thousand, and slightly worse than either. The parent's premise -- error
falling monotonically with volume -- predicts the 0-10 bucket to be the most
mispriced of the traded ones, and it is the least. Whatever is happening at zero
stops happening after the first fill.

The shading is visible in both directions, which is the main reason to believe
it is a property of unhit quotes rather than a lucky cell. Never-traded against
traded, same spread and depth gates, by price band:

                    never traded        traded
    mid 0.02-0.15   -0.0264 edge      -0.0177
    mid 0.15-0.35   +0.0213           -0.0205
    mid 0.35-0.50   -0.0041           -0.0111
    mid 0.50-0.65   +0.0244           +0.0094
    mid 0.65-0.95   +0.1197           +0.0342
    mid 0.95-1.00   +0.0260           +0.0136

The untouched longshot is MORE overpriced than the traded one and the untouched
favourite is MORE underpriced. That is the favourite-longshot shape amplified,
not a new shape, and it is what an uncorrected maker quote should look like.

ROBUSTNESS. Leave-one-series-out over the five largest contributors leaves P&L
between +0.0846 and +0.1031, every interval clear of zero; the largest series is
10 of 61 observations. 9 of the 11 days with any trade are P&L-positive. Band
floor 0.60/0.65/0.70 gives +0.059/+0.100/+0.102 and ceiling 0.90/0.95/0.98 gives
+0.125/+0.100/+0.052, which is the usual story that there is no room left to be
underpriced by near 1.0.

WHERE IT IS WEAK, stated plainly. There is more of this than usual:

  - n=61. That is the smallest population anything has shipped here, it is
    0.09% of entries, and a forward test will see tens of observations, not
    hundreds. The 29-series spread is the one thing keeping it from being a
    handful of markets, and the series are thin crypto (KXHYPE, KXSOLD, several
    15-minute coin markets) plus football and basketball team-total legs.
  - THE SPREAD GATE IS FRAGILE, and this is the worst part of the case. The next
    bucket up reverses violently: at 3-4 cents, n=40 across 11 series scores
    edge -0.1135 and P&L -0.1435, which drags the cumulative spread <= 0.04
    number down to +0.0034. Wider still it recovers (4-6c: +0.0395). I chose
    0.03 on cost arithmetic -- 1.5c of half-spread plus the 1c fee is 2.5 points,
    and the governing constraint says the edge has to clear about 2 -- and not
    from the sweep, because the sweep is not monotone and offers no support. Two
    readings are available: 40 observations of noise, or a real cliff where the
    cost eats a bias that was never 12 points wide. I cannot tell which, and if
    it is the second the shipped cell is riding the favourable side of it.
  - The sample is unstable in time. First half +0.0407 with an interval spanning
    zero, second half +0.1465. Same warning neglected_leg carries, same size.
  - The depth requirement is doing real population selection, not just
    housekeeping. Dropping it adds 56 observations that are ALL one series
    (KXBNB, quoting 1-3 contract offers), and they score +0.0821 -- so the gate
    does not flatter the number, but it is the reason the shipped cell has 29
    series rather than 30 with half its mass in one. It is set at 4 because
    simulate_fill caps a fill at 25% of visible depth and rejects anything under
    one contract; below 4 the harness would not fill this trade at all.
  - IT IS NOT INDEPENDENT OF neglected_leg. A market with zero volume has a zero
    share of its event's volume, so 36 of these 61 fall inside that candidate's
    gate mechanically. The remainder scores +0.0660 with an interval spanning
    zero (n=25, 11 series), which settles nothing on its own. Read the other way,
    the never-traded legs are the best-paying part of neglected_leg's population
    (+0.0770 against +0.0476 for its traded legs), so one honest reading of both
    files is that "no flow at all" is the sharp version of "little flow", and
    that neglected_leg's continuous share gate is partly a blurred proxy for it.
    If both score forward they must be checked for redundancy before either is
    bred from. 23 of the 61 also fall inside persistent_quote_favourite and 19
    inside unmarked_favourite.
  - The mechanism is inferred, not shown. "No flow has ever corrected this quote"
    fits the profile in both directions, but so does "a market nobody will trade
    is one whose outcome is already obvious, and the residual is the maker's fee
    cushion", and so does "these are all quoted by one algorithm with one
    shading parameter". Those predict different things about whether it survives
    a change in who is making these markets, and this data cannot separate them.
  - Found by scanning. Before this I tested flow-weighted VWAP against the
    current mid, stranded probability mass on dead event legs, open-interest
    share within the event, event price concentration, sibling book quality,
    tail momentum, top-of-book size imbalance, and path volatility rescaled by
    time to close. All eight separated nothing once the ordinary favourite bias
    was accounted for. The intervals here are not corrected for that search, so
    treat +0.0997 as the optimistic end of what a forward test should show.
  - All in-sample. Every observation above resolved before this file's
    created_at, so INVARIANT #1 excludes all of them from its score. This
    docstring is a hypothesis with arithmetic attached, not a result.

WHAT WOULD FALSIFY IT. A forward P&L interval including zero on this subset.
More sharply, it is falsified as a claim about the discontinuity if the volume
0-100 buckets pay as well forward as the zero bucket: that would mean this is
the parent's quiet-market gradient after all, measured at a threshold the parent
happened not to try, and the parent already showed that gradient is not worth
trading. It is falsified as a claim about unhit quotes, specifically, if the
never-traded tail stops being more overpriced than the traded tail -- the
symmetric shading is what makes this a statement about the quote rather than one
61-observation cell, and without it there is very little left.

DELIBERATELY ONE-SIDED. The mirror exists and does not pay. Never-traded
longshots at 0.02-0.20 are overpriced by 2.9 points, more than traded ones, and
buying NO there returns +0.0034 per contract: right about direction, eaten by
the spread, which is exactly the failure the brief warns about. The middle bands
and the 0.95+ band are abstained on for the same reason -- +0.0058 and +0.0047
are not worth crossing for.

A NOTE ON MVE. Auto-generated multivariate parlay combos are never-traded by
construction, and if they ever enter capture they would flood this gate. There
are none in the current 65,225 entries (is_mve is False on every one), so the
measurement above is unaffected, but this is the one population change that
would silently turn this candidate into something else.

Shift is +0.04 against a measured +0.1197, below the low end of the interval
(+0.0460) and a third of the point estimate, on the same reasoning as
favourite_longshot and ladder_leader: carrying a fitted value into a forward test
is how in-sample fitting sneaks back in, and a 61-observation estimate deserves
even less trust than usual. It still clears the ~2.5-point cost of crossing a
three-cent book.

Abstains on 99.9% of observations, so its whole-population Brier skill will be
indistinguishable from zero next to candidates that nudge every market. That is
the intended shape: acting on 0.09% of markets at ten points beats nudging all of
them at one and paying the spread each time."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "untraded_favourite",
    "generation": 3,
    "parent_id": "volume_weighted",
    "created_at": "2026-09-05T06:43:54.742887+00:00",
    "rationale": (
        "A market with zero lifetime volume has a quote nobody has ever hit, so "
        "its midpoint is a maker's opinion rather than a market belief. On the "
        "high side those quotes sit ~12 points below the outcome against ~3 for "
        "markets that have traded. The parent read volume as a gradient; the "
        "error is a discontinuity at zero -- thirty contracts of volume behaves "
        "like thirty thousand. Abstains on 99.9%."
    ),
}

EPS = 0.001

MIN_PRICE = 0.65      # below this the bias does not cover the spread
MAX_PRICE = 0.95      # above this there is no room left to be underpriced by
MAX_SPREAD = 0.03     # cost gate: 1.5c of half-spread plus the 1c fee is 2.5 pts
MIN_ASK_DEPTH = 4.0   # simulate_fill caps at 25% of depth and rejects under 1
SHIFT = 0.04

# Prices are floats parsed from decimal strings, so a nominal three-cent book
# comes out as 0.880 - 0.850 = 0.030000000000000027 and a bare `> MAX_SPREAD`
# silently drops a fifth of the population -- 14 of the 61 observations measured
# above. The gate is meant to read "no wider than three cents"; make it do that.
SPREAD_TOL = 1e-9


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob

    # Cost gate first. Nothing below matters on a book we cannot cross cheaply,
    # and simulate_fill rejects a spread above 0.08 outright.
    if market.spread > MAX_SPREAD + SPREAD_TOL or not (MIN_PRICE <= p < MAX_PRICE):
        return p

    # The band and the spread cap already imply a real two-sided book, but say
    # it: a 0.0/1.0 book has no midpoint to correct.
    if not market.has_two_sided_book:
        return p

    # The whole hypothesis. Both fields are checked because either one alone
    # would be a single point of failure for the claim: volume is the quantity
    # the parent reasoned about, and last_price is None exactly when no trade has
    # ever printed. They agree on all 19,220 never-traded entries in the current
    # data, and if they ever stop agreeing this should stand aside rather than
    # guess which one is right.
    if market.volume > 0.0 or market.last_price is not None:
        return p

    # Depth known to be too small for the harness to fill. Unknown depth is
    # allowed through, because simulate_fill applies no cap when size is absent
    # and abstaining here would forecast differently from how we are scored.
    depth = market.yes_ask_size
    if depth is not None and depth < MIN_ASK_DEPTH:
        return p

    return min(p + SHIFT, 1.0 - EPS)
