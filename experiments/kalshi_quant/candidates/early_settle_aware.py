"""Treat markets that settle long before their close date differently.

Kalshi markets can settle early. Measured 2026-08-31, entries whose close_time
was still more than a day out -- yet which resolved within the hour -- carried a
mean error of -0.036, against -0.007 for those closing within the hour. 15,611
of 40,928 observations fell in that group, so it is not a corner case.

The reading: when an outcome becomes certain well ahead of the scheduled close,
the quote has not caught up, and it lags on the low side. Those markets resolve
below their quoted price more often than the price implies.

This is the one candidate whose signal is about the CALENDAR rather than the
price, so it should be close to uncorrelated with the shoulder family. That is
its main value in the set -- if the shoulder candidates all move together, this
one is the check that the whole set is not one idea wearing ten hats."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "early_settle_aware",
    "generation": 1,
    "parent_id": None,
    "created_at": "2026-09-01T01:00:00+00:00",
    "rationale": (
        "Fade markets that resolved far ahead of their scheduled close. "
        "Calendar-driven, so near-uncorrelated with the shoulder family."
    ),
}

EPS = 0.001
FAR_HOURS = 24.0


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    hours_to_close = (market.close_time - context.now).total_seconds() / 3600.0
    if hours_to_close <= FAR_HOURS:
        return p
    return max(p - 0.03, EPS)
