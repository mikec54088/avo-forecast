"""Fade longshots, but only ones whose price has been moving.

The mirror of stable_favourite, and the low shoulder inverts the pattern:

    mid 0.00-0.35   stable -0.0112   moving -0.0233

Longshots that have been moving are overpriced by roughly twice as much as
still ones -- the opposite conditioning to the high shoulder, which is what
makes the pair worth scoring together rather than as one rule.

If both work, the shoulders are genuinely different phenomena that generation 1
was blurring by treating them symmetrically. If the conditioning helps on one
side and not the other, that is still more than generation 1 could say."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "moving_longshot",
    "generation": 2,
    "parent_id": "longshot_fade",
    "created_at": "2026-09-03T17:12:22+00:00",
    "rationale": (
        "The low shoulder is ~2x stronger when the price has been moving -- "
        "the opposite conditioning to the high shoulder."
    ),
}

EPS = 0.001
MIN_POINTS = 4
MOVING_VOL = 0.03
SHIFT = 0.03


def _volatility(history) -> float:
    xs = [h.implied_prob for h in history]
    mean = sum(xs) / len(xs)
    return (sum((x - mean) ** 2 for x in xs) / len(xs)) ** 0.5


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    hist = context.price_history
    if len(hist) < MIN_POINTS or not (0.05 <= p < 0.35):
        return p
    if _volatility(hist) <= MOVING_VOL:
        return p                      # still: the gap roughly halves
    return max(p - SHIFT, EPS)
