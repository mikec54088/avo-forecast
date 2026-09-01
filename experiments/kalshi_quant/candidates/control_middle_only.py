"""CONTROL. Trade only the band the market prices correctly.

The inverse of every shoulder candidate. It applies the same size of shift as
favourite_longshot but ONLY inside 0.35-0.65, where the measured gap was 0.0
points on 5,931 fillable observations.

Expected slightly negative skill and clearly negative P&L. It is the falsifiable
half of the favourite-longshot claim: if the shoulder candidates are picking up
a real effect located at the shoulders, this must fail. If instead it scores
about the same as they do, then the shifts are not exploiting the shape of the
bias at all and something else is producing their numbers.

A control that CAN pass is worth more than one that cannot."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "control_middle_only",
    "generation": 1,
    "parent_id": None,
    "created_at": "2026-09-01T01:00:00+00:00",
    "rationale": (
        "CONTROL. Same shift as favourite_longshot but only in the band that "
        "measured perfectly calibrated. Must fail if the shoulder story holds."
    ),
}

EPS = 0.001


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    if not (0.35 <= p <= 0.65):
        return p
    # Push away from 0.5 inside the band that needs no correction.
    shift = 0.03 if p > 0.5 else -0.03
    return min(max(p + shift, EPS), 1.0 - EPS)
