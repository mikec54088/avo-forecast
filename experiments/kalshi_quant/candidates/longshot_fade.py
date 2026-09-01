"""Half of favourite_longshot: fade longshots only.

Splitting the two shoulders separates two different stories. The low end is
usually explained by a preference for lottery-like payoffs, the high end by
reluctance to tie up capital for a small return. They need not hold at the same
time, in the same venues, or in the same size, so scoring them apart says which
half (if either) is real here.

Expected weaker than favourite_longshot if both shoulders are genuine, and
roughly equal to it if only this one is."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "longshot_fade",
    "generation": 1,
    "parent_id": None,
    "created_at": "2026-09-01T01:00:00+00:00",
    "rationale": (
        "Fade overpriced longshots only. Isolates the low shoulder from the high one."
    ),
}

EPS = 0.001


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    if p >= 0.35:
        return p
    return max(p - (0.01 if p < 0.05 else 0.03), EPS)
