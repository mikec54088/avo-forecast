"""Correct the favourite-longshot bias, and only where it exists.

Measured 2026-08-31 over 40,928 observations restricted to fillable books
(spread <= 0.08), the market is essentially perfect in the middle and biased at
both shoulders:

    mid 0.00-0.05   actual -1.9 pts below mid
    mid 0.05-0.35   actual -3.2 pts below
    mid 0.35-0.65   actual  0.0 pts        <- calibrated, leave alone
    mid 0.65-0.95   actual +4.3 pts above
    mid 0.95-1.00   actual +1.3 pts above

Longshots are overpriced and favourites underpriced, which is the textbook
favourite-longshot effect and the oldest documented anomaly in betting markets.

This is why baseline_sharpened earned nothing: it pushes away from 0.5
everywhere, paying the spread across the 0.35-0.65 band where there is no edge
at all, and at the very extremes where the gap collapses again.

Shifts are round numbers rather than the fitted values -- the measured gaps come
from data this candidate is NOT scored on (INVARIANT #1), and carrying three
decimals of a past sample into a forward test is how in-sample fitting sneaks
back in."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "favourite_longshot",
    "generation": 1,
    "parent_id": None,
    "created_at": "2026-09-01T01:00:00+00:00",
    "rationale": (
        "Shift the shoulders, leave the calibrated middle alone. "
        "Longshots overpriced, favourites underpriced."
    ),
}

EPS = 0.001


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    if p < 0.05:
        shift = -0.01
    elif p < 0.35:
        shift = -0.03
    elif p <= 0.65:
        shift = 0.0          # the market is calibrated here; do not pay to trade
    elif p <= 0.95:
        shift = 0.04
    else:
        shift = 0.01
    return min(max(p + shift, EPS), 1.0 - EPS)
