"""The "calibrated middle" is a pooling artifact. Split it by tick grid.

favourite_longshot's central claim is that the market is perfectly calibrated in
the 0.35-0.65 band (0.0 pts on fillable books) and biased only at the shoulders,
so every candidate in that family declines to trade the middle. That pooled zero
is two opposite populations cancelling, and `price_level_structure` separates
them.

Measured 2026-09-01 on 19,376 fillable observations (two-sided book, spread <=
0.04), reported as series-clustered means -- the mean of per-series mean errors,
matching scoring.py's primary_ci -- with `k` the number of contributing series:

                     linear_cent                tapered_deci_cent
    mid 0.00-0.05   -0.0162  se 0.0052 k=299   +0.0209  se 0.0087 k=12
    mid 0.05-0.35   -0.0076  se 0.0124 k=359   -0.0374  se 0.0184 k=12
    mid 0.35-0.65   +0.0637  se 0.0259 k=211   -0.0368  se 0.0139 k=12
    mid 0.65-0.95   +0.0550  se 0.0173 k=233   +0.0181  se 0.0144 k=12
    mid 0.95-1.00   +0.0185  se 0.0083 k=125   -0.0061  se 0.0078 k=12

Two readings, both of which cut against the existing shoulder family:

  The middle band is where the largest linear_cent gap lives (+0.064), not where
  there is nothing. It is the one band every gen-1 candidate refuses to trade.

  The low shoulder, which longshot_fade exists to exploit, is indistinguishable
  from zero on linear_cent (-0.008, se 0.012). Whatever that candidate scores on
  0.05-0.35, it is coming from tapered markets, not from a broad longshot bias.

Kalshi assigns finer tick grids to its larger markets, so this is confounded with
liquidity -- tapered books carry a median volume near 400 against 2 for
linear_cent. But it is not a thin-market story: inside the linear_cent middle
band the gap GROWS with volume (+0.004 under 10 contracts, +0.031 over 1,000),
which is the opposite of what volume_weighted assumes.

Unlike baseline_sharpened, whose skill sat where the book was too wide to trade,
this effect is stronger at the tighter cap: the linear_cent middle band measures
+0.064 at spread <= 0.04 against +0.055 at <= 0.08. It lives where INVARIANT #4
will actually fill.

This forecasts on linear_cent only. The tapered column is a real in-sample
pattern but it comes from 12 series, and 12 clusters is not a sample -- acting on
it would be fitting a dozen event families. Returning the market price there also
makes the candidate a clean test of the split: if it beats favourite_longshot, it
is because conditioning on the grid works, not because it traded more markets.

Shifts are round numbers well inside the measured gaps. The measurements come
from data this candidate is not scored on (INVARIANT #1), and carrying a fitted
point estimate forward is how in-sample fitting sneaks back in.
"""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "tick_grid_conditioned",
    "generation": 1,
    "parent_id": "favourite_longshot",
    # After every resolution used in the docstring above (latest was
    # 2026-09-01T06:45Z), so INVARIANT #1 scores this only on markets that had
    # not resolved when the split was measured.
    "created_at": "2026-09-01T07:11:17.163165+00:00",
    "rationale": (
        "Condition the shoulder correction on price_level_structure. The "
        "0.35-0.65 band that favourite_longshot calls calibrated is +0.064 on "
        "linear_cent and -0.037 on tapered_deci_cent; pooling hides both."
    ),
}

EPS = 0.001

# INVARIANT #4 will not fill wider, and the effect is strongest here anyway.
MAX_SPREAD = 0.04

# The grid this was measured on. tapered_deci_cent and deci_cent are declined
# rather than inverted -- see the docstring.
GRID = "linear_cent"


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob

    # A one-sided or crossed book has no midpoint to correct.
    if not market.has_two_sided_book:
        return p
    if market.price_level_structure != GRID:
        return p
    if market.spread > MAX_SPREAD:
        return p

    if p < 0.05:
        shift = -0.01
    elif p < 0.35:
        shift = 0.0          # measured -0.008, se 0.012: nothing to act on
    elif p <= 0.65:
        shift = 0.04         # the band the shoulder family declines to trade
    elif p <= 0.95:
        shift = 0.04
    else:
        shift = 0.01
    return min(max(p + shift, EPS), 1.0 - EPS)
