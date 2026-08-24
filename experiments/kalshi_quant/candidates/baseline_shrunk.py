"""Control #3: market price pulled 20% toward 0.5.
Expected slightly negative skill — confirms the sign is oriented correctly."""
from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "baseline_shrunk",
    "generation": 0,
    "parent_id": None,
    "created_at": "2026-01-01T00:00:00+00:00",
    "rationale": "Control. Market implied, shrunk toward 0.5.",
}

SHRINK = 0.2


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    return p + SHRINK * (0.5 - p)
