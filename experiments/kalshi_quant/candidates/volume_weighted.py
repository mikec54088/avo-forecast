"""Trust a heavily traded market more than a quiet one.

Measured 2026-08-31, absolute pricing error falls monotonically with volume --
0.326 in the lowest quartile against 0.239 in the highest -- and mean error
shrinks toward zero as well (-0.018 to -0.003). Heavily traded markets are
better priced.

The catch, and the reason this is expected to be WEAK: low-volume markets are
also the wide ones (median spread 0.11 against 0.01). Exactly where the market
is worst is exactly where trading it costs most. So the correction has to be
applied to the quiet markets, and those are the ones INVARIANT #4 will refuse.

Kept deliberately as a near-null internal control. If it posts strong skill,
suspect the scorer before believing the edge."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "volume_weighted",
    "generation": 1,
    "parent_id": None,
    "created_at": "2026-09-01T01:00:00+00:00",
    "rationale": (
        "Deviate more from quiet markets, less from busy ones. Near-null by "
        "construction: the mispriced markets are the untradeable ones."
    ),
}

EPS = 0.001
BUSY = 10_000.0


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    if market.volume >= BUSY:
        return p                 # well traded: the market knows more than we do
    if p < 0.35:
        shift = -0.02
    elif p <= 0.65:
        shift = 0.0
    else:
        shift = 0.02
    return min(max(p + shift, EPS), 1.0 - EPS)
