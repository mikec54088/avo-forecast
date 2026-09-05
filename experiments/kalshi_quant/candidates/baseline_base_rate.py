"""Control #2: ignore price, use the series' historical yes rate.
Expected clearly negative skill. If it beats the market, something is wrong."""
from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "baseline_base_rate",
    "role": "control",
    "generation": 0,
    "parent_id": None,
    "created_at": "2026-01-01T00:00:00+00:00",
    "rationale": "Control. Series base rate, ignoring current price.",
}

PRIOR, PRIOR_WEIGHT = 0.5, 10.0


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    hist = context.series_history.get(market.series_ticker, [])
    if not hist:
        return PRIOR
    return (sum(r.outcome for r in hist) + PRIOR * PRIOR_WEIGHT) / (len(hist) + PRIOR_WEIGHT)
