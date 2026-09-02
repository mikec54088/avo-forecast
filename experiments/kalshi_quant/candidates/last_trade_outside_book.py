"""Take the shoulder correction only when the last trade printed outside the book.

The edge is an interaction, not a new signal. favourite_longshot located a bias
at the shoulders; this asks *where that bias lives*, and the answer measured
here is: in the markets whose quote has visibly fallen behind the tape.

A last trade outside the current bid-ask means somebody transacted at a price
the resting book no longer offers. The quote has moved away from where trading
actually happened, which is direct evidence that the resting orders are stale --
and INVARIANT #2 scores us against exactly that quote.

Measured 2026-08-31 on 21,961 fillable observations (spread <= 0.08) that had
ever traded, splitting each mid band by whether last_price fell outside
[yes_bid, yes_ask]. 37.3% did. Mean (outcome - mid):

    mid band        inside book      outside book
    0.00-0.05         -0.0168          -0.0179
    0.05-0.35         -0.0212          -0.0401     <- doubles
    0.35-0.65         -0.0154          +0.0095
    0.65-0.95         +0.0347          +0.0413
    0.95-1.00         +0.0120          +0.0098

The low shoulder is the finding: the gap roughly doubles, -0.0212 to -0.0401,
and it is the only band where the two 95% intervals barely meet ([-0.0324,
-0.0100] against [-0.0517, -0.0284]). The high shoulder moves the right way but
the intervals overlap heavily, and both extremes and the middle show nothing.
So this is one-and-a-half bands of evidence, not five, and those intervals are
i.i.d. rather than series-clustered, so they are optimistic. Treat the high
shoulder here as a prediction being tested, not a result being harvested.

The counterintuitive part, and the reason this is worth scoring: the DIRECTION
of the outside print does not matter. Buying through the offer is supposed to
mean upward pressure, but in the 0.05-0.35 band trades above the ask and trades
below the bid both carried a mean error near -0.04 (-0.0387 and -0.0426). The
correction follows the shoulder, not the print. What an outside trade conveys is
staleness, not direction -- which is what makes it a modifier on an existing
bias rather than a signal of its own.

Hence the design: shift ONLY when the last trade printed outside the book, and
return the market unchanged otherwise. favourite_longshot and tight_book_only
already take the band-only position; duplicating it would say nothing. Declining
on the ~63% of markets where the tape and the book agree is what makes this
falsifiable -- if it scores like its parent, the interaction is not real and the
band alone was doing all the work.

Shifts are held to 0.02, about half the measured gap, because the measurement is
in-sample and crossing the spread costs roughly 2 points either way."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "last_trade_outside_book",
    "generation": 1,
    "parent_id": "favourite_longshot",
    "created_at": "2026-09-01T04:47:45.130788+00:00",
    "rationale": (
        "The shoulder bias concentrates in markets whose last trade printed outside the "
        "current book -- a stale quote. Acts only there; abstains where tape and book agree."
    ),
}

EPS = 0.001

# Matches INVARIANT #4's 8-cent fill cap, and the set the table above was measured on.
MAX_SPREAD = 0.08

LOW_SHOULDER = (0.05, 0.35)
HIGH_SHOULDER = (0.65, 0.95)
SHIFT = 0.02


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob

    # No book, no market belief to disagree with (see has_two_sided_book).
    if not market.has_two_sided_book:
        return p
    if market.spread > MAX_SPREAD:
        return p                       # too wide to trade the correction back out

    last = market.last_price
    if last is None:
        return p                       # never traded: no tape to compare the book to

    # The whole gate. Direction is deliberately ignored -- see the docstring.
    if market.yes_bid <= last <= market.yes_ask:
        return p                       # tape and book agree: take no position

    if LOW_SHOULDER[0] <= p < LOW_SHOULDER[1]:
        shift = -SHIFT
    elif HIGH_SHOULDER[0] < p <= HIGH_SHOULDER[1]:
        shift = SHIFT
    else:
        return p                       # extremes and the calibrated middle: leave alone

    return min(1.0 - EPS, max(EPS, p + shift))
