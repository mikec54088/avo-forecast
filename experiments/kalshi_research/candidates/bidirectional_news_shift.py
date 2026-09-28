"""Act on BOTH verdict directions, not just the fade, on the same favourite gate.

PARENT: roster_news_favourite. Its docstring records the failure this fixes:
"six actions in eight days is 267 days to the 200-fill gate" -- an arithmetic
problem, not a quality one. local_news_favourite named the fix explicitly and
deliberately left it undone: "Redesigning for rate (boost on OTHER_SIDE,
magnitude from evidence strength) is the next candidate, not this one." This
is that candidate, with only the mapping changed -- market selection and the
question asked are untouched, so the two records stay comparable.

THE EDGE, as a hypothesis, restated: dated roster/injury news that a
game-winner book has not yet absorbed. Same hypothesis as the parent. What
changes is which half of the researcher's answer gets used.

WHY OTHER_SIDE WAS BEING THROWN AWAY FOR FREE. The local researcher's judge
scores `bad_for_named_side` and `bad_for_opponent` independently in ONE model
call (local_research.py:JUDGE_SYSTEM) and collapses them to a single token --
NONE, THIS_SIDE, OTHER_SIDE or BOTH. The parent's funnel measured (Sonnet-era,
2026-09-17 note): of 9 calls that named a side, 6 named this one and 3 named
the opponent. The parent fades on the 6 and abstains on the other 3 --
discarding a third of its already-scarce actionable signal after paying for
it. It was safe to discard under name-matching (identifying "the opponent"
from a title was guesswork, per the parent's own docstring), but the local
engine's token is not guesswork: `bad_for_opponent` is a structured judgment
against a named opponent field the retrieval pipeline already resolved
(local_research.py:Market.opponent), not a substring match on the title.

WHICH MARKETS, THE QUESTION ASKED. Unchanged from the parent: `wants_research`
(favourite side, mid in [0.50, 0.90], spread <= 0.04, game's own UTC date,
RESEARCH_SERIES only) and the same research query. Reusing the parent's gate
and question means this candidate spends NO extra research calls per market --
the researcher answers both directions from one call regardless of who asks,
and the runner's cache (LocalResearcher._cache) means a pass that also runs
the parent pays for this market once, not twice.

HOW THE ANSWER MAPS.
    NONE       -> abstain (no dated news on either side)
    THIS_SIDE  -> shrink toward 0.5 by SHIFT: the favourite got worse news, same
                  as the parent's fade
    OTHER_SIDE -> push AWAY from 0.5 by SHIFT: the opponent got worse news, so
                  the favourite is more likely than the book says
    BOTH       -> abstain, deliberately: news cut against both sides in the same
                  window and there is no principled way to net them from a
                  token alone

EXPECTED EFFECT ON RATE. If OTHER_SIDE calls are roughly as common as
THIS_SIDE calls -- the one measurement available (2026-09-17, Sonnet-era) put
them at 6 vs 3, same order of magnitude -- this should produce on the order of
1.5-2x the parent's action rate at zero extra research cost, which is the
whole point: the parent's own arithmetic makes rate the binding constraint,
not precision.

WHAT WOULD FALSIFY IT. If the OTHER_SIDE actions, scored separately from the
THIS_SIDE actions, show a forward P&L interval including zero while the
THIS_SIDE-only subset (the parent's existing bet) does not, that means "bad
news reinforces the other side" does NOT hold at this token's granularity even
though "bad news hurts the named side" does, and the two directions should be
split back into separate candidates rather than pooled. It is also falsified,
same as the parent, if OTHER_SIDE verdicts turn out to be too rare in practice
to matter, though the funnel note above argues against that in advance.

Cannot be backtested: replaying an old game against today's web returns the
score. Every claim above is a hypothesis until the forecast log says
otherwise.
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
    "candidate_id": "bidirectional_news_shift",
    "generation": 1,
    "parent_id": "roster_news_favourite",
    "created_at": "2026-09-27T15:07:00.319686+00:00",
    "rationale": (
        "Act on both verdict directions from the local researcher's single call "
        "-- shrink the favourite on THIS_SIDE as the parent does, but also push "
        "toward the favourite on OTHER_SIDE instead of discarding it -- to raise "
        "the action rate the parent's own docstring names as the binding "
        "constraint, at zero extra research cost."
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
    if verdict == "THIS_SIDE":
        return max(p - SHIFT, 0.5 + EPS)
    if verdict == "OTHER_SIDE":
        return min(p + SHIFT, 1.0 - EPS)
    return p
