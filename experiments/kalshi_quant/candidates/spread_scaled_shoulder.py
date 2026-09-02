"""Scale the shoulder correction by how wide the book is.

Every shoulder candidate here (favourite_longshot, longshot_fade, favourite_boost,
tight_book_only) applies a FIXED shift per price band. This one asks a different
question: not which direction the mid is wrong, but by HOW MUCH. The claim is
that spread width measures how much the midpoint is a fiction -- on a one-cent
book the mid is a real price two traders are standing next to, and on a 30-cent
book it is the average of two numbers nobody will trade at.

Measured 2026-09-01 over 44,941 two-sided entries, holding the price band fixed
so this is not the shoulder effect relabelled. Within EVERY band the correction
grows monotonically with spread -- e.g. p in (0.0, 0.15]: -0.015 at spread <=0.01
rising to -0.057 at 0.08-0.2; p in (0.85, 1.0]: +0.008 rising to +0.087.

The size is what makes it a candidate rather than an observation. Signed toward
the near boundary, the correction divided by sqrt(spread) is 0.163, 0.197, 0.166,
0.159, 0.144, 0.163 across six spread buckets spanning a 33x range of widths.
That constant does not drift, so the shift is K*sqrt(spread) with K=0.15 fitted
by minimising mean residual -- not a hand-picked round number.

Sqrt, not linear: the same table divided by spread runs 1.63 down to 0.28, so a
linear rule fitted anywhere is wrong everywhere else.

Expected to score well on skill and NOT to make money, for the reason
tight_book_only exists: the corrections worth acting on are the large ones, the
large ones live on wide books, and INVARIANT #4 will refuse to fill there. Its
value in the set is diagnostic -- it separates "the mid is biased" from "the mid
is noisy", which the fixed-shift candidates conflate."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "spread_scaled_shoulder",
    "generation": 1,
    "parent_id": None,
    # After the last resolution used to fit K (2026-09-01T15:51Z), so INVARIANT #1
    # scores this only on observations it was not fitted on.
    "created_at": "2026-09-01T16:25:19.188573+00:00",
    "rationale": (
        "Shoulder correction scaled by sqrt(spread) rather than a fixed shift. "
        "Spread measures how much the midpoint is a fiction."
    ),
}

EPS = 0.001
K = 0.15
LOW, HIGH = 0.35, 0.65


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    if not market.has_two_sided_book:
        # No book means no spread to read and no market belief to correct.
        return p
    if LOW <= p <= HIGH:
        # Middle band has no near boundary, and measured mean error there is
        # -0.005 -- indistinguishable from zero. Take no position.
        return p
    direction = 1.0 if p > HIGH else -1.0
    shift = direction * K * (market.spread ** 0.5)
    return min(max(p + shift, EPS), 1.0 - EPS)
