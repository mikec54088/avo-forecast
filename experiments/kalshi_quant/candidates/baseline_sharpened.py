"""Control #4: market price pushed AWAY from 0.5, the mirror of baseline_shrunk.

This control is inverted relative to the other three. They are expected to score
at or below zero, and positive skill from any of them means the scorer is
broken. This one is expected to score POSITIVE while the fitness denominator is
biased, and ~0 once it is not. It is a tripwire, not a candidate.

Why it exists: controls #1-#3 either sit on the price or pull toward 0.5, so
none of them moves away from 0.5 -- and that is the direction the bid-ask
midpoint is actually biased in. The ROADMAP Phase 2 gate ("three baselines score
~0 skill and are statistically indistinguishable") can pass cleanly on a fitness
that a generated candidate will exploit within a generation or two.

Measured 2026-08-24 on 2,921 observations across 78 series, with all three
existing controls behaving exactly as documented:

    baseline_market          +0.0000
    baseline_shrunk (->0.5)  -0.0753
    baseline_sharpened       +0.0195   CI [+0.0121, +0.0273]

The bias appears mechanical rather than a market inefficiency: median spread is
0.20 and prices are bounded to [0, 1], so the midpoint is pushed centre-ward at
the extremes -- a book quoting 0.00/0.09 has a mid of 0.045 against a true rate
nearer 0.012. yes_bid is biased low in every bin and yes_ask high in every bin,
bracketing the outcome.

The skill is also untradeable: it sits where the book is wide (+0.0209, CI
excludes zero) and is indistinguishable from zero on markets simulate_fill will
trade (+0.0152, CI [-0.0005, +0.0320]). INVARIANT #2 is spread-blind while
INVARIANT #4 is spread-aware, and they disagree about what counts as a market.

If this control scores clearly positive, do NOT read it as a discovery, and do
not accept Phase 2. It measures the denominator, not forecasting skill. See
docs/ROADMAP.md Phase 2 and scripts/check_calibration.py.
"""
from __future__ import annotations

import math

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "baseline_sharpened",
    "role": "control",
    "generation": 0,
    "parent_id": None,
    # Same instant as the other three controls. Under INVARIANT #1 a different
    # created_at would score this on a different observation set and make the
    # four incomparable, which is the whole point of a control.
    "created_at": "2026-01-01T00:00:00+00:00",
    "rationale": (
        "Control. Market implied, sharpened away from 0.5 in logit space. "
        "Tripwire for a biased fitness denominator; expected POSITIVE while the "
        "midpoint is shaded toward 0.5."
    ),
}

# 1.25 rather than a larger value: measured skill was flat between 1.25 and 1.5
# (+0.0195 vs +0.0205) and gone by 2.0 (-0.0019), so a mild sharpen is the
# honest probe. A large one would overshoot and mask the bias it exists to find.
SHARPEN = 1.25

# Prices come from a two-sided book, so implied_prob is strictly inside (0, 1);
# this only guards the logit against a future caller that relaxes that.
_EPS = 1e-6


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = min(max(market.implied_prob, _EPS), 1.0 - _EPS)
    logit = math.log(p / (1.0 - p))
    return 1.0 / (1.0 + math.exp(-logit * SHARPEN))
