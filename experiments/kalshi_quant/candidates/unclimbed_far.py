"""unclimbed_favourite restricted to the horizon where its edge concentrates.

VARIANT, not a replacement. The parent is untouched and keeps its own record;
this has a fresh created_at and its own forward clock.

THE ONE CHANGE. Adds a minimum time to close. The parent ignores horizon
entirely, and its mechanism audit found the edge is not evenly spread:

    hours to close <= 48   +0.0307  [-0.0028,+0.0643]   does not clear zero
    hours to close  > 48   +0.1032  [+0.0704,+0.1361]

Three times the edge beyond two days. No story is offered for why, and that is
deliberate: the parent's stated mechanism has already been falsified once (the
mirror test -- the same gate inverts below 0.50), so inventing a second story
to fit a second split would be the same mistake twice. This is a measured
interaction being tested forward, nothing more.

WHAT WOULD FALSIFY IT. A forward interval including zero, or an edge no better
than the parent's. Note the near-close half is where most of the volume is, so
if this holds it also says the parent is diluting itself.
"""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "unclimbed_far",
    "generation": 3,
    "parent_id": "unclimbed_favourite",
    "created_at": "2026-09-20T21:21:01+00:00",
    "rationale": (
        "unclimbed_favourite restricted to markets more than 48h from close, where the parent's audit found 3x the edge. Its own forward clock."
    ),
}

EPS = 0.001

MIN_PRICE = 0.50          # below 0.35 the gate inverts; 0.50 is the safe side of that
MAX_PRICE = 0.95          # above this there is no room left to be underpriced by
MAX_SPREAD = 0.04         # cost gate; the edge decays monotonically as the book widens
MIN_PATH_MINUTES = 120.0
MIN_HOURS_TO_CLOSE = 48.0   # see the docstring; the parent ignores horizon  # a run-up is only meaningful over a real observation window
MAX_RUNUP = 0.10          # dollars above the path's own low; middle of a broad plateau
SHIFT = 0.04

# Prices are floats parsed from decimal strings, so a nominal four-cent book
# comes out as 0.040000000000000036 and a bare `> MAX_SPREAD` silently drops 33
# of the 454 observations measured above. The gate is meant to read "no wider
# than four cents"; make it do that.
SPREAD_TOL = 1e-9


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob

    # Cost gate first. Nothing below matters on a book we cannot cross cheaply,
    # and simulate_fill rejects a spread above 0.08 outright.
    if market.spread > MAX_SPREAD + SPREAD_TOL or not (MIN_PRICE <= p < MAX_PRICE):
        return p

    if (market.close_time - context.now).total_seconds() / 3600.0 < MIN_HOURS_TO_CLOSE:
        return p

    hist = context.price_history
    if not hist:
        return p

    # The window is measured in MINUTES, not in history points, so that a change
    # in the capture cadence cannot quietly turn "the session" into forty
    # minutes. A path that does not span two hours cannot say whether this
    # market has ever been cheaper.
    span = (context.now - hist[0].observed_at).total_seconds() / 60.0
    if span <= MIN_PATH_MINUTES:
        return p

    # The whole hypothesis. Not the latest move and not the average level: the
    # single lowest point the market has visited. If it is close to here, this
    # price was never bought up to -- it has simply held.
    #
    # Note the direction of the instrument's error. A spurious low quote raises
    # the measured run-up and pushes the observation OUT of the gate, so a noisy
    # path costs trades rather than manufacturing them.
    if p - min(h.implied_prob for h in hist) >= MAX_RUNUP:
        return p

    return min(p + SHIFT, 1.0 - EPS)
