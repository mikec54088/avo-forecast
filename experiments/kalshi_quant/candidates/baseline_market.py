"""Control #1: trust the market exactly. Skill must be 0.000 by construction.
If this scores anything else, the scorer is broken."""
from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "baseline_market",
    "generation": 0,
    "parent_id": None,
    "created_at": "2026-01-01T00:00:00+00:00",
    "rationale": "Control. Returns market implied probability unchanged.",
}


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    return market.implied_prob
