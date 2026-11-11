"""Buy the LOWEST rung of a political count/threshold ladder when news says it happened.

A variant of cheap_already_happened (2026-11-01) that changes WHICH KIND of
market it researches. Every politics candidate so far (cheap_already_happened,
coinflip_settled_by_news, week_ahead_happened, longshot_already_happened,
confirm_favourite_happened, expensive_ruled_out, politics_already_happened)
drops titles containing "at least", "more than", "above", "or more" and the
like, because a yes/no judge cannot count. That leaves a whole class of
markets unresearched, and every one of those candidates has idled.

THE EDGE, as a hypothesis. A ladder ("Trump says X at least 3 times", "at
least 5", "at least 10") has one rung that is a plain yes/no question in
disguise: the lowest threshold is met by the FIRST qualifying occurrence.
For that rung "did it already happen?" is exactly what the judge is good at,
and the wording trap that makes higher rungs unreadable (does a report of
"several" mean 5 or 6?) does not apply. Ladder books are thin, spread across
many rungs, and the lowest rung is often priced as a lottery ticket by
traders who watch the headline rung, so a quiet first occurrence is where a
stale price is most plausible. The move must beat ~2 points of cost, so only
a decisive jump is worth taking.

WHICH MARKETS (`wants_research`). A politics series (markets.is_politics);
a title with a LOWER-bound ladder phrase ("at least", "or more", "more than",
"above"); the lowest rung of its event, i.e. its price is at least that of
every sibling quoted in the same snapshot (and there is at least one
sibling, otherwise it is not a ladder); 24h-168h to close; spread <= 4c;
mid 0.05-0.35. Upper-bound phrases ("at most", "fewer than") and two-sided
phrases ("between", "exactly") are skipped: their meaning flips or needs a
count.

WHAT IT ASKS. The same local politics judge, one call per market, shared with
any candidate asking about the same market.

HOW IT MAPS. HAPPENED -> max(p, 0.75): lower than the plain-question variants
(0.80-0.85) because rung wording can still hide a count ("at least 2" is not
met by one mention). Everything else abstains: RULED_OUT on a cheap rung was
expected, and CONFLICT/NONE are not evidence.

WHAT WOULD FALSIFY IT. HAPPENED calls resolving NO more than ~30% of the time
(the judge reads a single mention as satisfying a count); fewer than a
handful of qualifying markets a week (ladders are too rare to judge); or
HAPPENED on rungs whose price had already risen from the first quote (the
book was never slow). Read the first dozen by hand. An edge above ~10 points
is outside every measured bias: run scripts/leak_check.py before building on
it.
"""
from __future__ import annotations

import re

from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research.markets import is_politics
from experiments.kalshi_research.types import ResearchContext, parse_verdict

MANIFEST = {
    "candidate_id": "lowest_rung_happened",
    "generation": 3,
    "parent_id": "cheap_already_happened",
    "created_at": "2026-11-01T16:07:48.102704+00:00",
    "rationale": (
        "Variant of cheap_already_happened: research the lowest rung of a "
        "lower-bound count ladder, the markets every other politics candidate "
        "skips, and jump to 0.75 if dated news says it already happened."
    ),
}

MIN_HOURS, MAX_HOURS = 24.0, 168.0
MAX_SPREAD = 0.04
SPREAD_TOL = 1e-9
MIN_PRICE, MAX_PRICE = 0.05, 0.35
TARGET = 0.75
LOWER_BOUND = re.compile(r"\b(?:at least|or more|more than|above)\b", re.IGNORECASE)
NOT_LOWER_BOUND = re.compile(
    r"\b(?:at most|or fewer|fewer than|less than|below|between|exactly|approval|average)\b|%",
    re.IGNORECASE)


def is_lowest_rung(market: MarketSnapshot, siblings: list[MarketSnapshot]) -> bool:
    others = [s for s in siblings if s.ticker != market.ticker and s.has_two_sided_book]
    if not others:
        return False
    return all(market.implied_prob >= s.implied_prob - SPREAD_TOL for s in others)


def wants_research(market: MarketSnapshot, context: ResearchContext) -> bool:
    if not is_politics(market.series_ticker):
        return False
    if not LOWER_BOUND.search(market.title) or NOT_LOWER_BOUND.search(market.title):
        return False
    hours_left = (market.close_time - context.now).total_seconds() / 3600.0
    if not (MIN_HOURS < hours_left <= MAX_HOURS):
        return False
    if not market.has_two_sided_book or market.spread > MAX_SPREAD + SPREAD_TOL:
        return False
    if not (MIN_PRICE <= market.implied_prob <= MAX_PRICE):
        return False
    return is_lowest_rung(market, context.siblings)


def forecast(market: MarketSnapshot, context: ResearchContext) -> float:
    p = market.implied_prob
    if not wants_research(market, context):
        return p
    verdict, _ = parse_verdict(context.research(
        f"Has this already happened: {market.title} ({market.ticker})?"
    ))
    if verdict == "HAPPENED":
        return max(p, TARGET)
    return p
