"""Scale the shoulder correction by open interest: thin books are more wrong.

favourite_longshot applies one fixed shift per price band. This asks whether the
size of that shift should depend on how much money is actually at risk in the
market. `open_interest` is the one liquidity field nothing else here reads --
`liquidity` is always 0.0, and volume is already spoken for by volume_weighted.

Measured 2026-09-01 over the 28,724 fillable entries (two-sided book,
spread <= 0.08), split into open-interest terciles inside each price band:

    mid < 0.35      OI 0-56    -0.0332    OI 56-1.9k  -0.0243    OI 1.9k+  -0.0208
    mid 0.35-0.65   OI 0-20    -0.0003    OI 20-1.2k  -0.0064    OI 1.2k+  -0.0020
    mid > 0.65      OI 0-246   +0.0346    OI 246-4.5k +0.0251    OI 4.5k+  +0.0224

The shoulder bias is monotone in open interest on both sides, in opposite
directions, and absent in the middle band where the market is calibrated
anyway. Thin markets are not biased in a *different* direction -- they are
biased ~50% harder in the same direction. That is the claim: open interest is a
confidence weight on an effect we already believe in, not a new effect.

Two honest caveats. Each individual thin-vs-thick gap is about 0.012 with a
combined interval of roughly the same width, so no single gap clears
significance on its own; what carries it is that the ordering repeats across
both shoulders and vanishes in the middle. And open_interest == 0 (2,707
longshot entries) measures like the thinnest tercile rather than like a separate
population, so it is treated as maximally thin rather than special-cased.

Deliberately NOT spread-gated, even though the measurement was. Its parent isn't
either, and tight_book_only / ensemble_shoulders already cover that axis -- if
this scores differently from favourite_longshot, the difference should be
attributable to open interest and nothing else.

Multipliers are round (0.7x to 1.3x) rather than the fitted ratios, for the same
reason the parent's shifts are round: the terciles come from data this candidate
is not scored on, and carrying a fitted constant forward is how in-sample
fitting sneaks back in."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "open_interest_shoulders",
    "generation": 1,
    "parent_id": "favourite_longshot",
    "created_at": "2026-09-01T16:05:12.424616+00:00",
    "rationale": (
        "favourite_longshot with the shift scaled by open interest. The "
        "shoulder bias is monotone in OI on both sides and absent in the "
        "calibrated middle, so OI acts as a confidence weight on a known "
        "effect rather than as a signal of its own."
    ),
}

EPS = 0.001

# Open interest at which the correction is applied at full parent strength.
# Near the median of the fillable set (1,131 contracts).
OI_REF = 1000.0

# Thin books get 1.3x the parent shift, deep ones 0.7x.
MIN_SCALE = 0.7
SCALE_RANGE = 0.6


def _base_shift(p: float) -> float:
    """The parent's shift schedule, unchanged."""
    if p < 0.05:
        return -0.01
    if p < 0.35:
        return -0.03
    if p <= 0.65:
        return 0.0          # calibrated band: no shift, so OI cannot matter here
    if p <= 0.95:
        return 0.04
    return 0.01


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    shift = _base_shift(p)
    if shift == 0.0:
        return p

    oi = max(market.open_interest, 0.0)
    thinness = OI_REF / (OI_REF + oi)      # 1.0 at OI=0, -> 0.0 as OI grows
    scale = MIN_SCALE + SCALE_RANGE * thinness
    return min(max(p + scale * shift, EPS), 1.0 - EPS)
