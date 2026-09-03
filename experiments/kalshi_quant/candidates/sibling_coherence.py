"""Correct a market whose event legs do not price to 1.0.

The first candidate that can see OTHER markets. Kalshi events carry several
legs, and mutually exclusive ones should sum to about 1.0 in implied
probability. When the total drifts away from 1.0, at least one leg is
mispriced, and the arithmetic says which direction the whole event is leaning.

`ForecastContext.siblings` was added on 2026-09-02 (G1). Measured immediately
after: 65% of observations have at least two siblings, and a sampled
Allsvenskan match priced to 1.005 across its legs -- close, which is what makes
the deviations interesting rather than the level.

If the legs sum above 1.0 the event is collectively overpriced, so this leg
probably is too; below 1.0 and the reverse. The shift is a fraction of each
leg's share of the excess rather than the whole of it, because the excess
belongs to the event and this is one leg of it.

This is WITHIN-event structure, not cross-venue arbitrage -- INVARIANT #6 rules
that out for being the easiest thing to find and not the research question.

Abstains without at least two quotable siblings: a partial set sums low for a
boring reason, and reading that as mispricing would be a bug wearing a
strategy's clothes.
"""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "sibling_coherence",
    "generation": 2,
    "parent_id": None,
    "created_at": "2026-09-03T02:00:00+00:00",
    "rationale": (
        "Fade legs of an event whose implied probabilities do not sum to 1.0. "
        "First candidate to use siblings; within-event structure, not arbitrage."
    ),
}

EPS = 0.001
MIN_SIBLINGS = 2
FRACTION = 0.30
MAX_EXCESS = 0.25


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    quotable = [s for s in context.siblings if s.has_two_sided_book]
    if len(quotable) < MIN_SIBLINGS:
        return p

    total = p + sum(s.implied_prob for s in quotable)
    excess = total - 1.0
    # A wild total means the event is not mutually exclusive, or the legs are
    # not all present. Either way the premise does not hold; stand aside.
    if abs(excess) > MAX_EXCESS:
        return p

    share = p / total if total > 0 else 0.0
    return min(max(p - FRACTION * excess * share, EPS), 1.0 - EPS)
