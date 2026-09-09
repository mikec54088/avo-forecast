"""Control: the market's own probability, no research, no cost.

The zero point for kalshi_research in the way baseline_market is for
kalshi_quant. It scores exactly 0 skill and takes no position by construction,
and it never calls `context.research`, so a runner carrying only this candidate
spends nothing -- which is how the forecast log's clock is started before any
research candidate exists (docs/PLAN-2026-09-09.md, B4).

If this ever scores non-zero skill the join between the forecast log and the
resolutions is broken, not the market.
"""
from __future__ import annotations

from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research.types import ResearchContext

MANIFEST = {
    "candidate_id": "research_market",
    "generation": 0,
    "parent_id": None,
    "role": "control",
    "created_at": "2026-09-09T00:00:00+00:00",
    "rationale": "Market-implied probability; the zero point. Never researches.",
}


def forecast(market: MarketSnapshot, context: ResearchContext) -> float:
    return market.implied_prob
