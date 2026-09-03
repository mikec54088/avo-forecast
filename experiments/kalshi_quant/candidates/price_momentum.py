"""Lean with the direction the market has been moving.

The first candidate that can see the price PATH. Generation 1 scored a clean
null, and every one of its 24 candidates was arithmetic on a single photograph
of the book -- a candidate saw 0.42 with no way to know whether that had
drifted down from 0.70 or up from 0.15. `ForecastContext.price_history` was
added on 2026-09-02 (G1) to close that blindness.

The hypothesis is the oldest one in markets: a quote that has been moving in
one direction tends to keep going, because the information causing the move
arrives over minutes rather than instantly. If true here, the last hour of
drift predicts the next move, and the market price at entry has not fully
absorbed it.

Deliberately simple: compare the current mid to the oldest point held and
shift a fraction of that drift. No fitted coefficients, because there is
nothing yet to fit against -- the history field did not exist when any prior
candidate was scored, so this is the first observation of it, not a refinement.

Abstains when history is thin. Two points is a line through noise.
"""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "price_momentum",
    "generation": 2,
    "parent_id": None,
    "created_at": "2026-09-03T02:00:00+00:00",
    "rationale": (
        "Lean with recent drift. First candidate to use price_history; tests "
        "whether generation 1 failed because it was blind to the path."
    ),
}

EPS = 0.001
MIN_POINTS = 4
FRACTION = 0.25


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    hist = context.price_history
    if len(hist) < MIN_POINTS:
        return p
    drift = p - hist[0].implied_prob
    return min(max(p + FRACTION * drift, EPS), 1.0 - EPS)
