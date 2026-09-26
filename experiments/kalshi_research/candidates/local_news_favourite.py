"""Fade a game-winner favourite when LOCALLY researched news says THIS side got worse.

A fresh identity for roster_news_favourite's hypothesis, created 2026-09-25
when the human stopped all Sonnet use in the research track and made the local
model the research engine (docs/PLAN-2026-09-22-RESEARCH-REVAMP.md). The plan
forbids appending local-model research under the old Sonnet-researched
identity: a candidate's record must come from one researcher (INVARIANT #7),
and a fresh created_at gives this one a clean forward clock (INVARIANT #1).

ONE CHANGE AT A TIME. Market selection (`wants_research`), the question asked,
and the decision rule -- fade by SHIFT when the news hurts THIS side, abstain
otherwise -- are imported unchanged from roster_news_favourite. Only the
engine differs, so the two records can be compared.

What differs in the reply: the local researcher answers with a fixed token,
THIS_SIDE / OTHER_SIDE / BOTH / NONE, rather than a team name. The old
name-matching gate mis-read crosstown games ("Chicago C" vs "Chicago WS" share
"chicago"); a token cannot. BOTH abstains -- news on both sides is not a
reason to fade one of them.

KNOWN LIMIT, inherited on purpose: the rate. The old arm produced ~1.3
scoreable decisions a day, ~5 months to PNL_GATE_MIN_FILLS. A new engine does
not change how many markets qualify or that it only ever fades. Redesigning
for rate (boost on OTHER_SIDE, magnitude from evidence strength) is the next
candidate, not this one.
"""
from __future__ import annotations

from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research.candidates.roster_news_favourite import (
    EPS,
    SHIFT,
    wants_research,
)
from experiments.kalshi_research.types import ResearchContext, parse_verdict

MANIFEST = {
    "candidate_id": "local_news_favourite",
    "generation": 1,
    "parent_id": "roster_news_favourite",
    "created_at": "2026-09-26T00:00:00+00:00",
    "rationale": (
        "roster_news_favourite's hypothesis and decision rule on the local "
        "research engine (qwen3.5:9b, deterministic retrieval), under a fresh "
        "identity because Sonnet left the research track on 2026-09-25."
    ),
}


def forecast(market: MarketSnapshot, context: ResearchContext) -> float:
    p = market.implied_prob
    if not wants_research(market, context.now):
        return p
    verdict, _ = parse_verdict(context.research(
        f"Has anything dated today changed the expected outcome of this event: "
        f"{market.title} ({market.ticker})? Roster, injury, scratch, "
        f"substitution, postponement or venue change."
    ))
    if verdict != "THIS_SIDE":
        return p
    return max(p - SHIFT, 0.5 + EPS)
