"""Blend the market price toward how this series has actually resolved.

The only candidate here that uses `context`. ForecastContext.series_history
carries the resolutions of that series that were already known at the entry
instant -- the slice is enforced in observations.SeriesHistory, so this cannot
see its own outcome or any later one.

baseline_base_rate ignores price entirely and scores about -0.68, which says the
base rate alone is far worse than the market. That does not mean it is
worthless: a small blend can still help if the market systematically drifts from
a series' long-run rate. The weight is deliberately small and shrinks further
when history is thin.

Expected near zero. It is here to test whether cross-market memory adds anything
at all before Phase 5 builds a memory store on the assumption that it does."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "series_base_rate_blend",
    "generation": 1,
    "parent_id": None,
    "created_at": "2026-09-01T01:00:00+00:00",
    "rationale": (
        "Blend the price toward the series base rate, weighted by how much "
        "history existed at entry. Tests whether memory is worth building."
    ),
}

EPS = 0.001
MAX_WEIGHT = 0.15
PRIOR_STRENGTH = 50.0


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    hist = context.series_history.get(market.series_ticker, [])
    if not hist:
        return p
    rate = sum(r.outcome for r in hist) / len(hist)
    # Weight grows with history but is capped: 50 resolutions gets half of
    # MAX_WEIGHT, and no amount of history lets the base rate dominate price.
    weight = MAX_WEIGHT * len(hist) / (len(hist) + PRIOR_STRENGTH)
    return min(max((1.0 - weight) * p + weight * rate, EPS), 1.0 - EPS)
