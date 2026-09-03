"""price_momentum's hypothesis, with the shape the data actually shows.

Same measurement as drift_reversion. The edge is real in the moderate band --
+0.0307 at a mean drift of +0.14 -- and decays beyond it. So: lean WITH the
drift, but only in that band, and cap the shift rather than scaling it without
limit.

Paired with drift_reversion this partitions the drift axis: this one takes the
moderate moves, that one takes the violent ones, and neither touches what the
other does. If both fail, drift carries nothing net of costs and the whole axis
is closed. If exactly one works, the shape is settled rather than argued."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "drift_momentum_capped",
    "generation": 2,
    "parent_id": "price_momentum",
    "created_at": "2026-09-03T17:12:22+00:00",
    "rationale": (
        "Lean with drift only in the moderate band where its edge peaks, with "
        "a capped shift. Partitions the drift axis with drift_reversion."
    ),
}

EPS = 0.001
MIN_POINTS = 4
MIN_DRIFT = 0.05
MAX_DRIFT = 0.30
MAX_SHIFT = 0.03
FRACTION = 0.25


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    hist = context.price_history
    if len(hist) < MIN_POINTS:
        return p
    drift = p - hist[0].implied_prob
    if not (MIN_DRIFT <= abs(drift) <= MAX_DRIFT):
        return p
    shift = max(-MAX_SHIFT, min(MAX_SHIFT, FRACTION * drift))
    return min(max(p + shift, EPS), 1.0 - EPS)
