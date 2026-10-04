"""Research each game ONCE, in the last hours before kickoff, when lineups are real.

PARENT: bidirectional_news_shift. It maps both verdict directions, but WHEN it
asks is accidental: any pass from 06:00 UTC on the game's date, so the first
pass to see a quotable favourite spends the (cached) call, often many hours
before the facts exist. This candidate changes exactly one thing: the timing
of the question. The question text, the verdict mapping and the favourite
gate are the parent's, so the two records are directly comparable.

THE EDGE, as a hypothesis. The facts that move a game-winner price -- the
starting goalkeeper, the scratched starter, the star ruled out the morning of
-- are published late: NHL/NBA scratches and soccer team sheets land 1-3 hours
before the start. An early call reads the day's earlier, already-absorbed
headlines and the verdict is cached; a call in the final window reads the
newest dated reports, the ones the book has had the least time to price. If
the book is slow to absorb dated roster news anywhere, it is slowest right
after publication, so that is where research should be spent.

WHICH MARKETS. RESEARCH_SERIES game-winner legs, not a tie leg, favourite side
(mid 0.50-0.90), spread <= 0.04 so the fill is real, and only while
now is within [start - 4h, start - 15min] (markets.game_start; for date-only
tickers that is close - 48h, an estimate, so the window is approximate there).
Never after kickoff (markets.game_started). The window also bounds cost: a game
is eligible for ~4 hourly passes, not a whole day, and the researcher's
cache means one call settles it for every candidate asking.

WHAT IT ASKS. The parent's question, one call. Query text is ignored by the
local engine apart from the "(TICKER)".

HOW THE ANSWER MAPS (SHIFT = 0.05, a little above the parent's 0.04 because
news this fresh should be less priced in):
    NONE       -> abstain
    BOTH       -> abstain: cuts on both sides cannot be netted from a token
    THIS_SIDE  -> shrink the favourite toward 0.5 by SHIFT (clamped above 0.5)
    OTHER_SIDE -> push the favourite up by SHIFT (clamped below 1)

WHAT WOULD FALSIFY IT. Over ~200 fills across many series: a P&L interval
(series-clustered) including zero; or fills no better than bidirectional_news_
shift's on the same games, which would mean timing adds nothing and the two
should merge. Also falsified if the window leaves too few games to judge: the
action rate is unmeasured and may be lower than the parent's, since the gate
is strictly narrower. That is the price of the bet and it is stated up front.

Cannot be backtested: replaying a game against today's web returns the score.
Every claim above is a hypothesis until the forecast log says otherwise.
"""
from __future__ import annotations

from datetime import timedelta

from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research.candidates.roster_news_favourite import (
    EPS,
    MAX_PRICE,
    MAX_SPREAD,
    MIN_PRICE,
    RESEARCH_SERIES,
    SPREAD_TOL,
)
from experiments.kalshi_research.markets import game_start, game_started
from experiments.kalshi_research.types import ResearchContext, parse_verdict

MANIFEST = {
    "candidate_id": "lineup_window_shift",
    "generation": 2,
    "parent_id": "bidirectional_news_shift",
    "created_at": "2026-10-04T15:06:56.530401+00:00",
    "rationale": (
        "Same favourite gate, question and two-direction mapping as the parent, "
        "but research only in the 4h before kickoff, when lineups and scratches "
        "are published and the book has had least time to absorb them, instead "
        "of whenever a pass first sees the game."
    ),
}

SHIFT = 0.05
WINDOW = timedelta(hours=4)


def in_window(market: MarketSnapshot, now) -> bool:
    start = game_start(market.event_ticker, market.close_time)
    return start - WINDOW <= now and not game_started(
        market.event_ticker, market.close_time, now)


def wants_research(market: MarketSnapshot, now) -> bool:
    if market.series_ticker not in RESEARCH_SERIES:
        return False
    if market.title.lower().startswith("tie"):
        return False
    if market.spread > MAX_SPREAD + SPREAD_TOL:
        return False
    if not (MIN_PRICE <= market.implied_prob <= MAX_PRICE):
        return False
    return in_window(market, now)


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
