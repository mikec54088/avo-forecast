"""CONTROL. Deviate from the market for no reason at all, and pay for it.

Not a strategy. It perturbs the price by a fixed amount whose direction depends
only on a hash of the ticker -- carrying no information about the market
whatsoever -- and is therefore expected to score ~0 skill and clearly NEGATIVE
P&L. It measures the cost of trading, nothing else.

Two things it pins down that no other candidate in generation 1 does:

  What zero looks like on money. Every real candidate must beat this, not just
  beat zero skill. It quantifies the drag the spread and fees impose on anyone
  who trades at all.

  Whether the harness leaks. A forecast built from a hash of the ticker cannot
  know anything. If it ever posts meaningfully positive skill, the defect is in
  the pipeline -- lookahead in the entry join, a mis-sliced context, an inverted
  outcome -- and no result from any other candidate should be believed until
  that is explained.

Deterministic on purpose: the same ticker always gets the same perturbation, so
a rescore reproduces exactly. Randomness here would make failures unrepeatable."""
from __future__ import annotations

import hashlib

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "control_cost_of_trading",
    "generation": 1,
    "parent_id": None,
    "created_at": "2026-09-01T01:00:00+00:00",
    "rationale": (
        "CONTROL, not a strategy. Ticker-hash perturbation: ~0 skill by "
        "construction, negative P&L. Measures the cost of trading and acts "
        "as a leak detector for the harness."
    ),
}

EPS = 0.001
NUDGE = 0.03


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    # Hash of the ticker: deterministic, reproducible, and carries no
    # information about the market. Any skill found here is a harness bug.
    digest = hashlib.sha256(market.ticker.encode()).digest()
    direction = 1.0 if digest[0] % 2 else -1.0
    return min(max(p + direction * NUDGE, EPS), 1.0 - EPS)
