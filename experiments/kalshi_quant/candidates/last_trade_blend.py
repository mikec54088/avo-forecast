"""Trust the last trade a little, not just the resting quotes.

The midpoint is where nobody has traded; the last price is where somebody did.
Measured 2026-08-31, when the last trade sits well below the mid the outcome
also lands below it -- mean error -0.074 in the bottom quintile of
(last_price - mid), against roughly zero in the other four.

The asymmetry is the interesting part: a last trade far BELOW the mid is
informative, one far above is not. That is consistent with the mid being pulled
up by a stale or thin ask while real transactions happen lower.

Weighting is deliberately mild. last_price has no timestamp on it, so it may be
far older than the quote and cannot be leaned on hard."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "last_trade_blend",
    "generation": 1,
    "parent_id": None,
    "created_at": "2026-09-01T01:00:00+00:00",
    "rationale": (
        "Blend the midpoint toward the last traded price. The mid is where nobody traded; the last price is where somebody did."
    ),
}

EPS = 0.001
WEIGHT = 0.25


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    last = market.last_price
    if last is None:            # never traded: nothing to learn from
        return p
    blended = (1.0 - WEIGHT) * p + WEIGHT * last
    return min(max(blended, EPS), 1.0 - EPS)
