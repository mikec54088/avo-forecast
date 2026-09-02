"""The shoulder gap is a premium on remaining uncertainty, so decay it to zero at close.

favourite_longshot applies a fixed shift per price band, the same 3 points on a
market closing in four minutes as on one closing in four days. The standard
explanation for the favourite-longshot effect says that is the wrong shape: the
longshot is overpriced because it still carries hope value, and hope value is
priced on the clock left to run. As close approaches the price converges to the
outcome and the residual gap collapses -- there is nothing left to be wrong about.

So the same band structure, multiplied by a horizon weight that goes to zero at
close:

    w = hours_to_close / (hours_to_close + 6)

    at close     w = 0.00   -> agrees with the market exactly
    1h out       w = 0.14
    6h out       w = 0.50
    24h out      w = 0.80
    1wk out      w = 0.97

The base shifts are slightly LARGER than favourite_longshot's (-0.04 / +0.05
against -0.03 / +0.04). That is required by the hypothesis rather than fitted to
anything: the measured -3.2 / +4.3 point gaps are averages over a horizon mix
whose median staleness is 77 minutes, and if the effect concentrates at long
horizons then its long-horizon value has to sit above that average. They stay
round numbers for the reason favourite_longshot gives -- the measurements come
from data this candidate is not scored on, and carrying fitted decimals forward
is how in-sample fitting sneaks back in.

Two honest caveats.

Near close, where the book is tightest and INVARIANT #4 will actually fill, this
converges on baseline_market and takes no position. Its deviations live at long
horizons, and long horizons are wide books. That is the same wall every
candidate in this set has hit; the difference is that here it is a prediction of
the hypothesis rather than a disappointment -- if this scores well precisely
where it cannot trade, the hypothesis is confirmed and worthless in the same
breath.

It overlaps early_settle_aware in one corner: both push down on far-from-close
longshots. They separate on favourites, where early_settle_aware also fades
(-0.03 flat, price-blind) and this backs (+0.05 * w). If both score, the
favourite band is what tells them apart.
"""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "longshot_time_decay",
    "generation": 1,
    "parent_id": "favourite_longshot",
    "created_at": "2026-09-01T15:10:02.611853+00:00",
    "rationale": (
        "favourite_longshot's bands, scaled by time remaining to close. The "
        "shoulder gap is a premium on uncertainty that has not resolved yet, so "
        "it should decay to zero at close rather than stay flat."
    ),
}

EPS = 0.001

# Hours at which the correction runs at half strength. 6h rather than a longer
# constant because it puts the steep part of the curve inside the same day the
# market closes, which is where the horizon mix actually varies -- past a couple
# of days w is flat and the choice stops mattering.
HALF_LIFE_HOURS = 6.0


def _band_shift(p: float) -> float:
    if p < 0.05:
        return -0.01  # the gap collapses again at the very bottom
    if p < 0.35:
        return -0.04
    if p <= 0.65:
        return 0.0  # calibrated middle; do not pay the spread for nothing
    if p <= 0.95:
        return 0.05
    return 0.01


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    if not market.has_two_sided_book:
        # No book means implied_prob is an artifact, not a belief. Nothing to
        # correct, and a shift off an artifact is noise with a sign.
        return p

    hours_to_close = max(
        (market.close_time - context.now).total_seconds() / 3600.0, 0.0
    )
    weight = hours_to_close / (hours_to_close + HALF_LIFE_HOURS)

    shifted = p + weight * _band_shift(p)
    return min(max(shifted, EPS), 1.0 - EPS)
