"""Apply the shoulder correction only where it can actually be traded.

INVARIANT #4 caps fills at 8 cents of spread, and crossing plus fees costs about
2 probability points even on the tightest books. A correction worth 3 points on
a 20-cent-wide market is not worth anything at all.

So: the same shifts as favourite_longshot, but only when the book is tight
enough that acting on them is possible. Everywhere else return the market price
unchanged, which takes no position rather than a bad one.

If this beats favourite_longshot on P&L while scoring lower on skill, that is
the clearest possible statement that the two metrics are measuring different
things -- and P&L is the one that pays."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "tight_book_only",
    "generation": 1,
    "parent_id": None,
    "created_at": "2026-09-01T01:00:00+00:00",
    "rationale": (
        "favourite_longshot, restricted to books tight enough to trade. Declining to act is a position."
    ),
}

EPS = 0.001
MAX_SPREAD = 0.04


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    if market.spread > MAX_SPREAD:
        return p                 # too wide to trade: agree with the market
    if p < 0.05:
        shift = -0.01
    elif p < 0.35:
        shift = -0.03
    elif p <= 0.65:
        shift = 0.0
    elif p <= 0.95:
        shift = 0.04
    else:
        shift = 0.01
    return min(max(p + shift, EPS), 1.0 - EPS)
