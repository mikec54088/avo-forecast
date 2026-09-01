"""favourite_longshot, gated on the conditions where it should hold.

Three filters, each from a measurement rather than a hunch:

  spread <= 0.04   -- INVARIANT #4 will not fill wider, and the ~2-point cost of
                      crossing swamps a 3-point edge on a wide book
  volume > 0       -- a market nobody has traded has no price discovery behind
                      its quote, only a market maker's guess
  outside 0.35-0.65 -- the middle band measured a 0.0 pt gap; trading it pays the
                      spread for nothing

Expected to score LOWER on skill than favourite_longshot (it declines far more
markets) and HIGHER on P&L. If that inversion shows up, it is the cleanest
demonstration in the set that Brier skill and money are different objectives --
which is the whole reason the P&L gate exists."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "ensemble_shoulders",
    "generation": 1,
    "parent_id": None,
    "created_at": "2026-09-01T01:00:00+00:00",
    "rationale": (
        "Shoulder correction gated on tight spread, real volume, and being "
        "outside the calibrated middle. Fewer trades, better ones."
    ),
}

EPS = 0.001
MAX_SPREAD = 0.04


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    if market.spread > MAX_SPREAD or market.volume <= 0.0:
        return p
    if 0.35 <= p <= 0.65:
        return p
    if p < 0.05:
        shift = -0.01
    elif p < 0.35:
        shift = -0.03
    elif p <= 0.95:
        shift = 0.04
    else:
        shift = 0.01
    return min(max(p + shift, EPS), 1.0 - EPS)
