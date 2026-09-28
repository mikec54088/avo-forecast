"""Mirror the favourite's own research onto its opponent's leg, at zero extra cost.

PARENT: roster_news_favourite. It and bidirectional_news_shift both trade
ONE leg of the event: the favourite's own market, price 0.50-0.90. Neither
ever touches the underdog's market at all -- not because the question does
not apply there, but because nobody asked it there. This candidate differs
in WHICH MARKET is traded, not in the hypothesis: dated roster/injury/scratch
news the book has not absorbed, same as the parent.

THE EDGE, as a hypothesis. A Kalshi game-winner event lists each side as its
OWN market with its own book (not a single market with a NO side), so the
underdog's market carries its own quote, its own spread, and its own mispricing
if the book is stale. If a report hurts the favourite, that is good news for
the underdog priced on the OTHER ticker, and if it hurts the underdog, that
is good news for the favourite -- but "good news for the underdog" is not the
same claim as "bad news for the favourite reported by name", and the two
markets can be mispriced by different amounts on different books, so trading
only one side leaves money on the other book's table if the effect is real at
all.

WHICH MARKETS. `sibling_favourite()` scans `context.siblings` -- the other
legs of the SAME event from the SAME snapshot, per `ResearchContext` -- for one
that passes the parent's own `wants_research` gate unchanged: series in
RESEARCH_SERIES, not a tie leg, mid in [0.50, 0.90], spread <= 0.04, and on
the game's own UTC date. THIS candidate then acts on `market` itself only if
`market` is the OTHER leg of that same event (not the favourite leg itself,
which the parent and bidirectional_news_shift already cover), its own mid is
in [UNDERDOG_MIN, UNDERDOG_MAX] = [0.10, 0.50], its own book has two sides, and
its own spread clears the same cost gate. No new gate invented for the
underdog side beyond price band and spread; timing and series eligibility both
inherit from whichever ticker is actually researched.

WHAT IT ASKS, AND WHY IT COSTS NOTHING EXTRA. The query embeds the FAVOURITE
SIBLING's ticker, not this market's own ticker -- `f"... {fav.title}
({fav.ticker})?"`. `LocalResearcher` parses the ticker out of the query text,
looks it up in the bound snapshot, and resolves THIS_SIDE / OTHER_SIDE from
the favourite's own perspective regardless of which candidate asked; its
per-ticker cache means a pass that also runs the parent or
bidirectional_news_shift on this same event pays for the call once, not
twice, exactly as bidirectional_news_shift's docstring records for its own
reuse of the parent's question. Run alone, this candidate costs exactly one
call per eligible event -- never two -- because it always asks about the
favourite leg, never about itself.

HOW THE ANSWER MAPS, mirrored rather than restated. Because the ticker in the
query is the FAVOURITE's, THIS_SIDE names the favourite and OTHER_SIDE names
its opponent -- which, on the event this candidate is scoring, is this
market's own side:

    NONE / BOTH -> abstain: no usable single-direction signal
    THIS_SIDE   -> bad news for the FAVOURITE -> this (underdog) market's
                   side is MORE likely to win -> push p UP by SHIFT
    OTHER_SIDE  -> bad news for the favourite's opponent, i.e. THIS market's
                   own side -> push p DOWN by SHIFT

This is the mirror image of bidirectional_news_shift's mapping, not a copy of
it: that candidate shifts the FAVOURITE's own quote in both directions off one
call; this one shifts the OPPONENT's quote off the identical call. A pass
running all three candidates together prices both legs of a two-way event
from one piece of research instead of leaving one leg untouched or paying for
a second, separate call to reach it.

WHAT WOULD FALSIFY IT. A forward P&L interval including zero on this
candidate's own fills. More specifically: if the favourite-leg shift
(bidirectional_news_shift, or the parent's THIS_SIDE-only fade) replicates
forward but THIS candidate's mirrored underdog-leg shift does not, that would
mean the two books do not share the same mispricing -- the favourite's market
absorbs news but the underdog's own book, quoted separately, already priced it
in, or is too thin/stale to move on it at all. That is a real possibility this
docstring does not rule out: the underdog leg of a lopsided game-winner event
is usually the thinner book of the two, which is exactly why UNDERDOG_MIN is
set to 0.10 rather than 0.01 -- to stay off the least liquid tail where a
resting quote may not reflect any belief at all. It is falsified as a
zero-cost claim specifically if, in practice, this candidate rarely finds an
eligible sibling already cached and ends up paying for its own call on most
events -- in which case running it alongside the favourite-leg candidates
would roughly double the research spend per event rather than sharing it, and
the "zero extra cost" framing above would need to be retracted.

Cannot be backtested: replaying an old game against today's web returns the
score. Every claim above is a hypothesis until the forecast log says
otherwise.
"""
from __future__ import annotations

from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research.candidates.roster_news_favourite import (
    MAX_SPREAD,
    SPREAD_TOL,
    wants_research,
)
from experiments.kalshi_research.types import ResearchContext, parse_verdict

MANIFEST = {
    "candidate_id": "underdog_mirror_shift",
    "generation": 1,
    "parent_id": "roster_news_favourite",
    "created_at": "2026-09-27T15:10:12.229585+00:00",
    "rationale": (
        "Trade the OTHER leg of the same event the parent already researches: "
        "when a sibling favourite market is eligible under the parent's own "
        "gate, mirror its research answer onto this underdog market instead of "
        "onto the favourite's own quote -- bad news for the favourite pushes "
        "the underdog's probability up, bad news for the underdog pushes it "
        "down. Reuses the favourite's cached call, so it prices a market "
        "population the parent and bidirectional_news_shift never touch at no "
        "extra research cost when run alongside them."
    ),
}

EPS = 0.001
UNDERDOG_MIN, UNDERDOG_MAX = 0.10, 0.50   # complement of the parent's 0.50-0.90 band
SHIFT = 0.04                              # same magnitude as the parent's fade


def sibling_favourite(market: MarketSnapshot, context: ResearchContext) -> MarketSnapshot | None:
    """The other leg of this event that the parent's own gate would research,
    if there is one. None if this market has no such sibling, or is not
    itself a genuine second leg of a two-way event."""
    for s in context.siblings:
        if (s.event_ticker == market.event_ticker and s.ticker != market.ticker
                and s.has_two_sided_book and wants_research(s, context.now)):
            return s
    return None


def forecast(market: MarketSnapshot, context: ResearchContext) -> float:
    p = market.implied_prob
    if market.title.lower().startswith(("tie", "draw")):
        return p
    if not market.has_two_sided_book or market.spread > MAX_SPREAD + SPREAD_TOL:
        return p
    if not (UNDERDOG_MIN <= p <= UNDERDOG_MAX):
        return p
    fav = sibling_favourite(market, context)
    if fav is None:
        return p
    verdict, _ = parse_verdict(context.research(
        f"Has anything dated today changed the expected outcome of this event: "
        f"{fav.title} ({fav.ticker})? Roster, injury, scratch, "
        f"substitution, postponement or venue change."
    ))
    if verdict == "THIS_SIDE":    # bad news for the favourite -> this side gains
        return min(p + SHIFT, 1.0 - EPS)
    if verdict == "OTHER_SIDE":   # bad news for the favourite's opponent -> this side loses
        return max(p - SHIFT, EPS)
    return p
