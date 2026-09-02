"""Read the book in logit space instead of price space.

The edge: `implied_prob` is the ARITHMETIC midpoint of bid and ask, which
assumes the true probability sits halfway between the quotes in price units.
Probabilities are not linear near their boundaries. A 0.01/0.09 book is a
factor-of-nine disagreement about the odds; a 0.46/0.54 book is a rounding
error. Averaging both the same way overstates the low one badly and leaves the
middle one alone -- exactly the shape the repo has already measured twice.

This candidate averages the two quotes as LOG-ODDS and converts back. It is the
same estimator the midpoint is, under the parameterisation the quantity
actually lives in.

Why it should work, from measurements already in this repo rather than a hunch:

  baseline_sharpened's docstring attributes the midpoint's centre-ward bias to
  a mechanical cause -- prices are bounded to [0, 1], books are wide at the
  extremes, so a 0.00/0.09 quote has a mid of 0.045 against a true rate nearer
  0.012. If that diagnosis is right, the correction should scale with the
  SPREAD, not with the price level. The logit midpoint does exactly that: it is
  identical to the arithmetic mid on a tight book at any price, and departs from
  it only as the book widens near a boundary.

  favourite_longshot measured the residual shape on fillable books: negative in
  0.00-0.35, zero in 0.35-0.65, positive in 0.65-1.00. The logit midpoint
  reproduces that sign pattern with no fitted constants at all -- it is exactly
  0.5 on any book symmetric about 0.5, and moves toward the nearer boundary on
  both shoulders. That docstring worries about carrying three decimals of a past
  sample into a forward test; this candidate carries none.

What separates it from what is already here. baseline_sharpened pushes away
from 0.5 by a fixed logit factor regardless of the book, and paid the spread
across the calibrated middle for it. favourite_longshot and its gated variants
key on the price level with hand-drawn bins. This one keys on the WIDTH of the
book, which none of them use, and it is the only candidate in the set with zero
tunable parameters.

It is deliberately falsifiable against baseline_sharpened. If most of that
control's +0.019 survives here, the bias is quote truncation and is a property
of the denominator, not a discovery. If favourite_longshot's tight-book
shoulder gap is still there after this correction, that gap is a real
inefficiency -- because on a tight book this candidate barely moves.

Expected weak on P&L for the usual reason: the correction is largest exactly
where the book is too wide for INVARIANT #4 to fill.

By construction the output always lies strictly inside (yes_bid, yes_ask) --
sigmoid and logit are monotone, so a mean in logit space stays between the two
quotes. It cannot invent a price the book does not already bracket.
"""
from __future__ import annotations

import math

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "logit_midpoint",
    "generation": 1,
    # Not derived from baseline_sharpened -- it tests that control's stated
    # mechanism rather than extending its code.
    "parent_id": None,
    # The real creation instant, not the 01:00 stamp the rest of generation 1
    # shares. Backdating to match the cohort would score this on observations
    # that resolved before it existed, which is INVARIANT #1.
    "created_at": "2026-09-01T16:20:41.376271+00:00",
    "rationale": (
        "Average the bid and ask as log-odds instead of as prices. Zero fitted "
        "parameters; identical to the midpoint on tight books, corrects wide "
        "ones near the boundaries. Tests whether the measured midpoint bias is "
        "quote truncation rather than a market inefficiency."
    ),
}

# Deci-cent books quote as low as 0.0010, so the guard has to sit well below
# that or it would clip real prices. Only present to keep the logit finite if a
# caller ever passes a quote at the boundary; the two-sided check above already
# excludes 0.0 and 1.0.
_FLOOR = 1e-4


def _logit(p: float) -> float:
    q = min(max(p, _FLOOR), 1.0 - _FLOOR)
    return math.log(q / (1.0 - q))


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    bid, ask = market.yes_bid, market.yes_ask
    # No two-sided book means there is no band to re-average, and log-odds are
    # undefined at 0.0 and 1.0. Agree with the market rather than guess.
    if not (0.0 < bid < ask < 1.0):
        return market.implied_prob
    mean_logit = (_logit(bid) + _logit(ask)) / 2.0
    # No output clamp: the result is strictly between bid and ask, hence
    # strictly inside (0, 1). Clamping at the usual 0.001 would erase the
    # correction on precisely the deep-longshot books it is largest on.
    return 1.0 / (1.0 + math.exp(-mean_logit))
