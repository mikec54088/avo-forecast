"""Fade a last trade that printed OUTSIDE the book, instead of following it.

The edge: `last_price` carries no timestamp, so on a market that is still quoted
two-sided it is the OLDER of the two prices. When the print sits outside the
current book, the book has already moved away from it -- somebody lifted the ask
and the resting quotes did not follow. That is transient price impact, and the
outcome tends to land back on the book's side of the print, not the print's.

Measured 2026-09-01 on the 15,500 entries with a fillable book (spread <= 0.08)
and entry staleness <= 20 min, with the mean error residualised on eight price
bands so this is NOT the favourite-longshot effect wearing a different hat:

    last <= bid   n=5,278  residual +0.0065   outcome lands ABOVE the mid
    inside book   n=1,766  residual -0.0067
    last >= ask   n=5,796  residual -0.0094   outcome lands BELOW the mid

Monotone, and monotone in both halves of the price range taken separately
(mid<0.5 and mid>=0.5), which is the check that matters -- a 1.6pt gap that only
appeared pooled would just be price level again. Individually only the
mid>=0.5 / last>=ask cell clears its own CI (-0.0172 +- 0.0170), so the honest
read is a weak effect with a consistent sign, not a strong one.

This is the sign-flip of `last_trade_blend`, which blends TOWARD the print, and
the two are worth reading against each other. They do not actually contradict:
that candidate measured the full observation set, where the informative tail is
the lower one, and this one measures the fillable, fresh subset, where the
informative tail is the upper one and points the other way. Restricting to
spread <= 0.08 is therefore part of the claim, not a detail -- on wide or stale
books this candidate has no evidence behind it and deliberately declines to act.

Expected small: an in-sample Brier skill of +0.0013 on the fresh fillable set,
against -0.0022 for blending toward the print at the same weight. A candidate
whose whole thesis is a 1-point shift cannot score much more than that, and if
it posts strong skill the staleness artifact in `observations.py` is the first
thing to suspect, not this.

Magnitude scales with how far outside the book the print sits, measured in
spreads rather than in cents: a 3-cent-away print means something different on a
1-cent book than on a 6-cent one. Steps are round numbers, not fitted values --
the measurement above comes from data this candidate is not scored on
(INVARIANT #1), and carrying a fitted coefficient across that line is how
in-sample fitting sneaks back in.
"""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "last_trade_fade",
    # Hand-written like the rest of generation 1 rather than bred from a scored
    # parent, so parent_id stays None even though the idea is last_trade_blend's
    # inverted. Nothing has been scored yet; claiming lineage would invent it.
    "generation": 1,
    "parent_id": None,
    # Later than the other generation-1 files on purpose. The measurement above
    # was run against entries loaded today, so under INVARIANT #1 backdating
    # this to 01:00 with its siblings would score it on observations it has
    # already seen.
    "created_at": "2026-09-01T15:21:19.546784+00:00",
    "rationale": (
        "Fade a last trade that printed outside the current book. The print is "
        "the older price and the book has already moved away from it. Sign-flip "
        "of last_trade_blend, restricted to fillable books."
    ),
}

EPS = 0.001
MAX_SPREAD = 0.08          # INVARIANT #4's fill cap, and the set this was measured on
STEP = 0.01
MAX_STEPS = 3.0            # cap the shift at 3 cents however far away the print is


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    last = market.last_price
    if last is None:                                # never traded: nothing to fade
        return p
    if not (0.0 < market.spread <= MAX_SPREAD):     # too wide to trade, or no book
        return p

    if last >= market.yes_ask:
        excess, direction = last - market.yes_ask, -1.0   # bought up: fade down
    elif last <= market.yes_bid:
        excess, direction = market.yes_bid - last, +1.0   # sold down: fade up
    else:
        return p                    # print inside the book: it agrees with the quote

    # +1.0 so a print resting exactly on the touch still gets one full step.
    steps = min(excess / market.spread + 1.0, MAX_STEPS)
    return min(max(p + direction * steps * STEP, EPS), 1.0 - EPS)
