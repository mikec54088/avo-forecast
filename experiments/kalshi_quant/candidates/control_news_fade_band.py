"""Control: roster_news_favourite's fade WITHOUT the research that justifies it.

NOT a candidate. An instrument, in the sense cli.py already means -- it is
excluded from the parent pool, and it is not here because anyone expects it to
make money. It is here because without it the research track's numbers cannot
be read at all.

WHAT IT CONTROLS FOR. On 2026-09-21 the research track had 20 resolved
actionable decisions: skill +0.0097 and decaying as n grew, direction right 7
of 20, and -- the number that matters -- a median mid of 0.66 against a
realised outcome rate of 0.65 on exactly the markets research fires on. The
market is calibrated there. So a fixed -0.040 fade is not harvesting a
mispriced favourite; it is a four-point tax on a correct price, on top of the
~2-point cost hurdle. Those 20 decisions cannot distinguish

    (a) research adds no information, from
    (b) fading a favourite at mid 0.50-0.90 in these series is simply a
        losing trade, research or no research.

This candidate is (b) measured on its own. Every gate of
roster_news_favourite.wants_research is reproduced exactly -- series, tie
exclusion, spread, price band, and the game-day window parsed from the ticker
-- and then the same max(p - SHIFT, 0.5 + EPS) is applied unconditionally.
The ONLY difference is that no verdict is consulted.

IT IS NOT A BACKTEST, AND THE REASON MATTERS. The obvious argument for this
module was that its gate is pure price and metadata, so unlike its subject it
could replay against captured history -- 25,711 distinct tickers passed this
gate in the research log's own 14 days and 24,999 have already resolved.
INVARIANT #1 forbids exactly that: a candidate is scored only on ground truth
resolving strictly after its created_at, and this file was written on
2026-09-21 by a session that had already measured the realised outcome rate
inside this very band (0.65 against a median mid of 0.66). That is precisely
the contamination the invariant exists to stop, and the fact that the gate was
copied rather than fitted does not undo having chosen which gate to copy.
Backdating created_at would be the bypass the invariant says never exists.

THE SAMPLE, forward. The argument survives in a weaker but sufficient form:
this gate passes a median of 2,568 distinct markets per DAY (measured over the
research log's 14 days), against 2-3 actionable decisions a day for the
research arm. PNL_GATE_MIN_FILLS is 200. So the control reaches a verdict in
days where its subject needs months -- roughly 3 days allowing for the ~2-day
resolution backfill lag, rather than the March answer the research arm is on
course for. That is the whole argument for the module, and it does not need
the history.

HOW TO READ THE RESULT. All three outcomes are informative, which is why it is
worth the module:

  - Loses money. The fade is a losing trade on its own and the research arm's
    entire acted set is explained without reference to research.
  - Flat. Research finally has a real zero to beat, and the paired comparison
    against it becomes meaningful.
  - Makes money. The edge is in the price band rather than the news, and it
    belongs here as a candidate rather than behind an API bill.

WHAT IT IS NOT. It is not a matched control for the research-ACTED subset.
Research fires where a sourced report exists, which skews toward particular
games within these series; this fires on every market passing the price gate.
It bounds the question rather than settling it. Read it as "is this band a
losing trade", not as "research adds nothing".

SAMPLE_1_IN is not reproduced: it has been 1 since 2026-09-17, so the hash
gate is a no-op, and carrying a dead rate-limiter into an instrument would
only invite someone to match it later and halve the sample for nothing.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "control_news_fade_band",
    "role": "control",
    "generation": 0,
    "parent_id": None,
    "created_at": "2026-09-21T05:40:00+00:00",
    "rationale": (
        "Control for the kalshi_research track. Reproduces "
        "roster_news_favourite's entry gate exactly and applies the same "
        "-0.04 fade with no research call, to separate 'research adds no "
        "information' from 'this band is a losing trade'. Backtestable "
        "because nothing here searches the web."
    ),
}

# Copied deliberately rather than imported. core/ never imports from
# experiments/ (INVARIANT #3) and one experiment reaching into another is the
# same mistake one level down: kalshi_research is forward-only and may widen
# its gates again, and a control that silently followed those changes would
# stop controlling for the thing it was written to control for. If the two
# drift apart, that is a fact about the experiment worth seeing, not a bug.
RESEARCH_SERIES = frozenset({
    "KXNCAAFGAME", "KXMLBGAME", "KXMLSGAME", "KXEPLGAME", "KXLALIGAGAME",
    "KXSERIEAGAME", "KXCS2GAME", "KXLOLGAME",
    "KXNPBGAME", "KXKBOGAME", "KXVALORANTGAME", "KXDOTA2GAME", "KXFIBAGAME",
    "KXBRASILEIROGAME", "KXLIGAMXGAME", "KXUSLGAME", "KXEFLCHAMPIONSHIPGAME",
    "KXSERIECGAME", "KXBRASILEIROBGAME", "KXR6GAME", "KXNCAAMSOCCERGAME",
    "KXARGNACBGAME", "KXETTANGAME", "KXECULPGAME", "KXURYPDGAME",
    "KXEREDIVISIEGAME", "KXLVAVIRGAME",
})
MIN_PRICE, MAX_PRICE = 0.50, 0.90
MAX_SPREAD = 0.04
SHIFT = 0.04
GAME_DAY_START_UTC = 6
EPS = 0.001

# A nominal four-cent book parses as 0.040000000000000036, and a bare
# `> MAX_SPREAD` silently drops a third of them. The research candidate
# carries the same tolerance; an instrument that gated differently from its
# subject would not be measuring the same set.
SPREAD_TOL = 1e-9

_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")
_MON = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT",
     "NOV", "DEC"], 1)}


def game_date(ticker: str) -> datetime | None:
    """The game's own date, from the ticker (KXMLBGAME-26SEP081940PITCWS-PIT).

    Kalshi sets close_time two to three days AFTER the game, so hours-to-close
    says nothing about kickoff and the ticker is the only honest source.
    """
    m = _DATE.search(ticker)
    if not m or m.group(2) not in _MON:
        return None
    try:
        return datetime(2000 + int(m.group(1)), _MON[m.group(2)], int(m.group(3)),
                        tzinfo=timezone.utc)
    except ValueError:
        return None


def in_gate(market: MarketSnapshot, now: datetime) -> bool:
    """roster_news_favourite.wants_research, minus the dead sample gate."""
    if market.series_ticker not in RESEARCH_SERIES:
        return False
    if market.title.lower().startswith("tie"):
        return False
    if market.spread > MAX_SPREAD + SPREAD_TOL:
        return False
    if not (MIN_PRICE <= market.implied_prob <= MAX_PRICE):
        return False
    gd = game_date(market.ticker)
    if gd is None:
        return False
    return gd.replace(hour=GAME_DAY_START_UTC) <= now <= gd.replace(hour=23, minute=59)


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    if not in_gate(market, context.now):
        return p
    # The subject's action, with the verdict removed. Same clamp, so the
    # truncation near 0.54 that produces its -0.014/-0.024/-0.034 tail is
    # reproduced too rather than quietly smoothed away.
    return max(p - SHIFT, 0.5 + EPS)
