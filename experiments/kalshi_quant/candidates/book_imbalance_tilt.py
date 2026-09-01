"""Lean toward the side with more resting size.

Measured 2026-08-31 across all books, the imbalance (bid_size - ask_size) /
(bid_size + ask_size) tracks outcome direction monotonically across quintiles:
mean error -0.029 when the ask is heavy against +0.014 when the bid is.

But within FILLABLE books the signal mostly collapses -- -0.010 to -0.000. That
is the honest reading: this is largely a wide-market phenomenon, and wide
markets are the ones we cannot trade. Included anyway because microstructure
signals are cheap to test and this one is measurable; expected weak.

Note the sign convention. A heavy bid means buyers are queued, which historically
preceded outcomes ABOVE the mid, so imbalance is added rather than subtracted."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "book_imbalance_tilt",
    "generation": 1,
    "parent_id": None,
    "created_at": "2026-09-01T01:00:00+00:00",
    "rationale": (
        "Tilt toward the heavier side of the book. Strong signal overall, "
        "mostly absent where it can be traded."
    ),
}

EPS = 0.001
TILT = 0.02


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    bid, ask = market.yes_bid_size, market.yes_ask_size
    if bid is None or ask is None or (bid + ask) <= 0:
        return p
    imbalance = (bid - ask) / (bid + ask)      # +1 all bid, -1 all ask
    return min(max(p + TILT * imbalance, EPS), 1.0 - EPS)
