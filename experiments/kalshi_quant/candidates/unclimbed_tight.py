"""unclimbed_favourite with the run-up gate where the edge actually is.

VARIANT, not a replacement. The parent stays in the registry untouched and
keeps accumulating its own uncontaminated record; this carries a fresh
created_at so INVARIANT #1 scores it only on markets resolving after the
observation that motivated it.

THE ONE CHANGE. MAX_RUNUP 0.10 -> 0.05. The parent's mechanism audit measured
P&L monotone in run-up over six bands:

    0.00-0.02  +0.0866   0.05-0.10  +0.0480 (spans zero)
    0.02-0.05  +0.0816   0.10-0.20  +0.0399

so the parent's 0.10 cutoff drags in a band whose interval already includes
zero and roughly halves the concentration of whatever the effect is.

WHAT WOULD FALSIFY IT. A forward P&L interval including zero, or -- more
interesting -- a forward edge no larger than the parent's. If tightening buys
nothing forward, the monotonicity was an in-sample artifact and the parent's
looser gate is the honest one. That is a real possibility: the gradient was
measured on the data that selected the parent.

THE COST. Acting on roughly half as many markets, so it needs about twice as
long to reach the 200-fill gate. That is the price of the test.
"""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "unclimbed_tight",
    "generation": 3,
    "parent_id": "unclimbed_favourite",
    "created_at": "2026-09-20T21:21:01+00:00",
    "rationale": (
        'unclimbed_favourite with MAX_RUNUP tightened from 0.10 to 0.05, where the measured edge actually lives. Its own forward clock; the parent is untouched.'
    ),
}

EPS = 0.001

MIN_PRICE = 0.50          # below 0.35 the gate inverts; 0.50 is the safe side of that
MAX_PRICE = 0.95          # above this there is no room left to be underpriced by
MAX_SPREAD = 0.04         # cost gate; the edge decays monotonically as the book widens
MIN_PATH_MINUTES = 120.0  # a run-up is only meaningful over a real observation window
MAX_RUNUP = 0.05          # dollars above the path's own low; middle of a broad plateau
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
