"""Buy the favourite that GROUND into its price instead of gapping into it.

THE EDGE. Every history-aware candidate in this project asks how FAR the price
has travelled -- distance above the path low, move over the last hour, drawdown
from the high -- and they all conclude the same thing: buy the one that did not
travel. This asks a different question. Among the favourites that HAVE travelled,
the ones every one of those candidates hands back, the shape of the journey
separates money from nothing. A market that reached 0.80 without ever moving
more than 25 points between two consecutive quotes we hold is underpriced by
~8.3 points. A market at the same price, on an equally tight book, that has
travelled just as far but did it in one large step, is underpriced by ~1.9,
which the ~2-point cost of crossing eats entirely.

The trigger is the LARGEST SINGLE STEP in the path. Not the total displacement,
not the volatility of the path, not its length.

WHY THIS IS NOT volume_weighted. The parent reads volume as a scalar for how
much a market knows -- deviate more from the quiet ones, less from the busy ones
-- and scored a near-null, concluding in its own docstring that the mispriced
markets are the untradeable ones because quiet markets are wide. This makes no
claim about how much a market knows. It claims that two markets which know the
same amount, quoted equally tight, are priced differently depending on whether
their price arrived continuously or discontinuously. Volume does not appear in
the rule at all.

THE EVIDENCE. Measured 2026-09-05 over all 65,225 scoreable entries. The step
set is the consecutive differences of the midpoint along `price_history` plus
the step into the entry quote. Intervals are bootstrapped over SERIES, not
observations, because markets in one series share an underlying and a day. P&L
crosses the spread at the ask and pays the 1c fee, per INVARIANT #4.

  biggest step < 0.25, run-up >= 0.10, 0.55 <= mid < 0.97, spread <= 0.04
      n=588  ser=158
      actual - mid  +0.0832  CI [+0.0580, +0.1065]
      P&L/contract  +0.0628  CI [+0.0374, +0.0864]

The control is the same rule with the step gate inverted, and it is the whole
argument:

  biggest step >= 0.25, same band, same spread, same run-up floor
      n=939  ser=232
      actual - mid  +0.0186  CI [-0.0081, +0.0434]   includes zero
      P&L/contract  -0.0007  CI [-0.0274, +0.0226]   a clean null

  the two pooled, i.e. no step gate at all
      n=1527 ser=285   edge +0.0435   P&L +0.0237  CI [+0.0022, +0.0431]

The gapped group is 61% of the population and halves the pooled number. Both
legs are the same markets by every other measure this project has used.

IT IS NOT A DISPLACEMENT PROXY, which is the obvious objection, since the
candidates that own this territory all gate on displacement. The step gate
separates INSIDE both halves of the run-up distribution, including the markets
that travelled furthest:

    run-up 0.10-0.30, step < 0.25   n= 377  P&L +0.0566
    run-up 0.10-0.30, step >= 0.25  n=  98  P&L -0.0000
    run-up >= 0.30,   step < 0.25   n= 211  P&L +0.0740
    run-up >= 0.30,   step >= 0.25  n= 841  P&L -0.0008

Nor is it path volatility, which is the other obvious objection. Split by both
at once, the step gate separates within both halves of realised volatility and
volatility separates nothing within either step group:

    step lo, rvol lo  n=631  P&L +0.0525     step hi, rvol lo  n=132  P&L -0.0241
    step lo, rvol hi  n=130  P&L +0.0473     step hi, rvol hi  n=634  P&L +0.0003

Nor is it history length: the split holds at n_history < 24 (+0.1151 against
+0.0432) and at the 24-point cap (+0.0525 against -0.0088).

Robustness. The step threshold is not perched on a cell: 0.20/0.25/0.30 give
P&L +0.056/+0.063/+0.047. The spread cap barely matters, which is what an
8-point edge against a 2-point cost should look like -- 0.02/0.03/0.04/0.06/0.08
give +0.065/+0.066/+0.063/+0.059/+0.053. The effect holds in every price band
(0.55-0.65 +0.063, 0.65-0.75 +0.130, 0.75-0.85 +0.092, 0.85-0.97 +0.031),
though the lowest band's interval includes zero on n=101. 123 of 158 series are
positive and dropping the three largest leaves +0.0550. No MVE rows.

WHAT IS WEAK ABOUT IT, stated plainly.

1. The mechanism story I would like to tell is that a large step is a discrete
   piece of news arriving -- a goal, a run, a print -- and that after news the
   price is fair, while a price assembled continuously still carries the retail
   imbalance that the favourite-longshot literature describes. That story
   predicts the effect should sharpen when the step is normalised by elapsed
   time. It does the opposite: dividing each step by the gap between snapshots
   destroys the result (+0.0265 against +0.0628 for the raw version, across a
   larger sample). The raw step size is doing the work and I cannot say why.
   Treat the news reading as a guess, not a finding.

2. Snapshot spacing is not uniform. The gated population's largest inter-quote
   gap is a median of 62 minutes, not the 15-minute near-pass cadence, so "a
   step" is not a fixed unit of time. Given (1), that imprecision may be part of
   why the rule works rather than a defect in it, which is not a comfortable
   position to be in.

3. It may be partly a market-type proxy. Continuous underlyings -- index levels,
   run totals -- move smoothly, and discrete-event markets -- moneylines,
   spreads -- gap. Within the 105 series that contain both kinds, holding the
   series fixed, ground still beats gapped by +0.0718 on average (+0.0525
   median, positive in 61 of 105 series, and in all six of the largest series
   individually), so it is not purely compositional. But that paired interval is
   [-0.0063, +0.1463] and includes zero. Some of the effect may be category
   selection wearing a microstructure costume.

4. It will look like a null on the primary fitness. Skill is Brier skill over
   every scoreable observation, and this abstains on 99.1% of them, so it scores
   +0.0004 on the full set (clustered CI [+0.0002, +0.0006]) while returning
   +0.0628 per contract on the 588 it acts on -- where its skill is +0.0614.
   That is baseline_sharpened's problem in mirror image, and it is a property of
   the fitness denominator rather than of this rule. Read the two numbers
   together or this looks like nothing.

5. This is one gate chosen after scanning many. Five other hypotheses were
   measured and discarded on the same data first -- event-sum deviation,
   shorting overpriced tails, resting offer size, wide-book pricing, and
   quote-revision counts -- so the interval above is not corrected for that
   search. INVARIANT #1 is the real test.

WHAT WOULD FALSIFY IT. If the step gate stops separating once the run-up floor
is removed, the effect was displacement all along. If the control leg scores
materially above zero on held-out data, the two populations were never
different. If the effect survives only in ladder-shaped TOTAL series, item 3 was
the whole story and this is neglected_leg's cross-section rediscovered.

The shifts are round numbers well inside the measured gaps, and smaller in the
top band where the measured gap is genuinely smaller (+0.052 there against
+0.083 overall). The measurements come from data this candidate is NOT scored on
(INVARIANT #1), and carrying a fitted point estimate forward is how in-sample
fitting sneaks back in.
"""
from __future__ import annotations

from itertools import pairwise

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "ground_favourite",
    "generation": 3,
    "parent_id": "volume_weighted",
    # After every resolution used above, so INVARIANT #1 scores this only on
    # markets that had not resolved when the split was measured.
    "created_at": "2026-09-05T07:48:39.871408+00:00",
    "rationale": (
        "Among favourites that HAVE moved -- the ones every displacement-gated "
        "candidate hands back -- the shape of the move separates money from "
        "nothing. A price that never jumped more than 25 points between "
        "consecutive quotes is underpriced by ~8.3 points against ~1.9 for one "
        "that gapped the same distance. Not displacement: the split holds "
        "inside both halves of the run-up distribution. Not volatility: the "
        "split holds inside both halves of realised vol, and vol separates "
        "nothing inside either step group. Abstains on 99.1%."
    ),
}

EPS = 0.001

# INVARIANT #4 will not fill wider than 8c, and simulate_fill caps the order at
# 25% of visible depth, so an offer under 4 contracts cannot fill at all. The
# edge is flat in the spread here (+0.065 at <=0.02, +0.053 at <=0.08), so this
# cap is not load-bearing -- it is inherited from the family for comparability.
MAX_SPREAD = 0.04
MIN_ASK_SIZE = 4.0

# The path has to be long enough for "how did it get here" to mean anything.
MIN_HISTORY = 8

# The hypothesis has no content on a market that never went anywhere: with no
# journey there is no shape to it. Those markets belong to the run-up family,
# which already trades them, and inside them this gate reverses on 27 rows.
MIN_RUNUP = 0.10

# The whole rule. Measured on midpoint steps between consecutive held quotes.
MAX_STEP = 0.25

BAND_LO, BAND_HI = 0.55, 0.97
TOP_BAND = 0.85


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob

    if not market.has_two_sided_book:
        return p
    if market.spread > MAX_SPREAD:
        return p
    if market.yes_ask_size is None or market.yes_ask_size < MIN_ASK_SIZE:
        return p
    if not (BAND_LO <= p < BAND_HI):
        return p

    history = context.price_history
    if len(history) < MIN_HISTORY:
        return p

    # The path, oldest first, with the entry quote as its final point.
    path = [h.implied_prob for h in history] + [p]

    # Did it travel at all? Below this floor the run-up family owns the market.
    if p - min(path) < MIN_RUNUP:
        return p

    # Did it ever gap? One large step disqualifies the whole path.
    for earlier, later in pairwise(path):
        if abs(later - earlier) >= MAX_STEP:
            return p

    shift = 0.03 if p >= TOP_BAND else 0.05
    return min(max(p + shift, EPS), 1.0 - EPS)
