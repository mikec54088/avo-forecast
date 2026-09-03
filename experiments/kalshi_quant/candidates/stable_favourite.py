"""Back favourites, but only ones that have been sitting still.

The shoulder bias is not uniform in how the price arrived. Measured 2026-09-03
on fillable observations with at least four history points, splitting each band
by whether recent price volatility was below or above the median:

    mid 0.65-1.00   stable +0.0476   moving +0.0268
    mid 0.00-0.35   stable -0.0112   moving -0.0233

The high shoulder is nearly TWICE as strong on stable prices. A favourite that
has held its level is underpriced by ~4.8 points; one that has been thrashing
by half that. Every shoulder candidate in generation 1 ignored the distinction
and averaged the two together.

Reading: a stable high price reflects settled consensus that the market still
underprices, while a moving one carries live disagreement already in the quote.

Caveat carried deliberately: that split is measured on markets that stayed
quotable for an hour or more, which is a selected population -- the 0.35-0.65
band shows +0.047 there against ~0.000 across all fillable markets. Hypothesis,
not measurement."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "stable_favourite",
    "generation": 2,
    "parent_id": "favourite_boost",
    "created_at": "2026-09-03T17:12:22+00:00",
    "rationale": (
        "The high shoulder is ~2x stronger when the price has been stable. "
        "Generation 1 averaged the two regimes together."
    ),
}

EPS = 0.001
MIN_POINTS = 4
STABLE_VOL = 0.03
SHIFT = 0.04


def _volatility(history) -> float:
    xs = [h.implied_prob for h in history]
    mean = sum(xs) / len(xs)
    return (sum((x - mean) ** 2 for x in xs) / len(xs)) ** 0.5


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    hist = context.price_history
    if len(hist) < MIN_POINTS or not (0.65 < p <= 0.95):
        return p
    if _volatility(hist) > STABLE_VOL:
        return p                      # moving: the gap roughly halves
    return min(p + SHIFT, 1.0 - EPS)
