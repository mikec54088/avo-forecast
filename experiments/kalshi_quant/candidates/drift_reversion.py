"""Fade the biggest moves. price_momentum bet the wrong shape.

Measured 2026-09-03 over 13,292 fillable observations with at least four
history points, mean (outcome - mid) by drift quintile:

    fell hard  -0.42 drift   ->  -0.0179
    2          -0.17         ->  -0.0004
    3          -0.03         ->  +0.0057
    4          +0.14         ->  +0.0307   <- peak
    rose hard  +0.45         ->  +0.0193   <- falls back

Drift is informative, but the relationship is NOT monotone: the edge peaks at
moderate drift and decays at the extreme. price_momentum extrapolated linearly
-- bigger drift, bigger shift -- so it bet hardest exactly where the edge
collapses, and returned -0.0554 per contract, the second-worst in the set.

That is a failed functional form, not a failed hypothesis. This candidate takes
the other half: where the move has been violent, lean AGAINST it. The decay
from +0.0307 to +0.0193 as drift doubles is consistent with large moves
overshooting.

Measured in-sample and therefore only a hypothesis here -- these observations
predate this file, so nothing it is scored on informed it (INVARIANT #1)."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "drift_reversion",
    "generation": 2,
    "parent_id": "price_momentum",
    "created_at": "2026-09-03T17:12:22+00:00",
    "rationale": (
        "Fade violent moves. Drift's edge peaks at moderate size and decays at "
        "the extreme; price_momentum extrapolated linearly and lost."
    ),
}

EPS = 0.001
MIN_POINTS = 4
BIG_DRIFT = 0.30
FRACTION = 0.20


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    hist = context.price_history
    if len(hist) < MIN_POINTS:
        return p
    drift = p - hist[0].implied_prob
    if abs(drift) < BIG_DRIFT:
        return p                      # the momentum zone; not this candidate's half
    return min(max(p - FRACTION * drift, EPS), 1.0 - EPS)
