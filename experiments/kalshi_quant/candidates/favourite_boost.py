"""The other half of favourite_longshot: back favourites only.

The high shoulder measured larger than the low one (+4.3 pts against -3.2), so
if only one half survives out of sample, this is the likelier. Paired with
longshot_fade it partitions favourite_longshot exactly -- the two together
touch every market that one does, and no market twice."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "favourite_boost",
    "generation": 1,
    "parent_id": None,
    "created_at": "2026-09-01T01:00:00+00:00",
    "rationale": (
        "Back underpriced favourites only. Pairs with longshot_fade to partition favourite_longshot."
    ),
}

EPS = 0.001


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    if p <= 0.65:
        return p
    return min(p + (0.04 if p <= 0.95 else 0.01), 1.0 - EPS)
