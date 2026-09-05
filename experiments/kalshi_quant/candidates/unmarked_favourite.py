"""Buy the favourite the market has NOT marked up in the last hour.

THE EDGE. In the hour before it resolves, a favourite usually gets marked up:
the price walks toward 1.0 as the outcome firms. That walk is where the market
does its work, and by the time it has happened the price is close to fair. The
edge lives in the favourites the market has left alone. Conditioned on a
fillable book and a price of 0.65-0.95, a favourite whose mid is BELOW where it
stood an hour ago -- or has risen by less than 5 points since -- is underpriced
by ~10.8 points. One that has rallied 5 points or more in that hour is
underpriced by ~5.0, which the ~2-point cost of crossing eats most of.

The trigger is the ABSENCE of a recent markup, not the price level, not the
duration of the quote, and not the smoothness of the path.

THE EVIDENCE. Measured 2026-09-04 over all 65,102 scoreable entries. The
reference is the most recent own-quote at least 60 minutes older than the entry
instant; markets without one are abstained on. Intervals are bootstrapped over
SERIES, not observations, because markets in one series share an underlying and
a day. P&L crosses the spread at the ask and pays the 1c fee, per INVARIANT #4.

  0.65 <= mid < 0.95, spread <= 0.04, 60-min move < +0.05
      n=350  ser=114
      actual - mid  +0.1084  CI [+0.0810, +0.1340]
      P&L/contract  +0.0890  CI [+0.0612, +0.1147]

The control is the same population with the gate inverted, and it is the whole
argument:

  same band and spread, 60-min move >= +0.05
      n=1143 ser=257
      actual - mid  +0.0496  CI [+0.0288, +0.0695]
      P&L/contract  +0.0299  CI [+0.0091, +0.0499]

  the two together (no gate at all)
      n=1493 ser=297   edge +0.0634   P&L +0.0438  CI [+0.0242, +0.0621]

The rallied group is 77% of the population and drags the pooled number down by
half. Both P&L intervals exclude zero, so this is not "signal versus noise" --
it is a 9-point edge and a 3-point one, and only one of them survives the
spread by a margin worth trading.

The profile across the whole move axis is what makes the cut a cut rather than
a fitted threshold. P&L per contract by 60-minute move:

    move <= -0.05   n= 114  +0.1100
    -0.05 .. -0.02  n=  68  +0.1057
    -0.005 .. +0.005 n= 42  +0.1010
    +0.005 .. +0.02 n=  42  +0.1171
    +0.02 .. +0.05  n=  61  +0.0534
    move >= +0.05   n=1143  +0.0299

Everything that is not a rally sits between +0.10 and +0.12; the number falls
off only once the markup is real. Note what this rules out: a FALL is not
special. The bucket that dropped 5+ points scores the same as the bucket that
did not move at all, so the reading is "the market has not repriced this",
not "the market overreacted to bad news and will bounce". I tested the
overreaction version first (fell >= 0.02, n=182, P&L +0.1084) and it is simply
the strongest slice of a flat region, not a separate effect.

Robustness. The rally cut is the least sensitive knob there is here:
0.005/0.02/0.03/0.05/0.08/0.12 gives P&L +0.093/+0.097/+0.097/+0.089/+0.092/
+0.093, against +0.044 with no gate at all. Spread cap 0.02/0.03/0.04/0.06/0.08
gives +0.097/+0.097/+0.089/+0.078/+0.068, monotone in tightness as a cost story
predicts. Price floor 0.50/0.55/0.60/0.65/0.70/0.75 gives +0.077/+0.088/+0.083/
+0.089/+0.078/+0.079 and ceiling 0.85/0.90/0.95/0.99 gives +0.120/+0.109/
+0.089/+0.064. No series carries it: the largest (KXMLBTOTAL) is 8.3% of the
sample, leave-one-series-out over the top eight ranges +0.0826 to +0.0910, and
21 of the 26 series with n >= 4 are P&L-positive on their own.

WHY THE LOOKBACK IS IN MINUTES, not in history points. `len(price_history)` is
partly a property of our capture cadence rather than of the market, and a gate
built on it silently changes meaning if the cadence changes. Reading the
timestamps costs nothing and keeps "an hour ago" meaning an hour ago. The index
form of the same rule (four points back) measures slightly stronger, +0.1169
against +0.1084, and I am shipping the weaker one on purpose.

Lookback length is the one genuinely sensitive knob: 30/45/60/75/90/120 minutes
gives +0.061/+0.071/+0.089/+0.092/+0.091/+0.096. Below 45 minutes the gate
stops working, because a market that has not moved in twenty minutes has not
told you anything. 60 is the shortest window that is clearly in the flat part.

WHERE IT IS WEAK, stated plainly:

  - 348 of the 350 trades also fall inside persistent_quote_favourite's
    population (deep own-quote history, 0.35-0.95, spread <= 0.04). This is a
    REFINEMENT of that candidate, not an independent discovery, and it should be
    read as one: it says that within that population, the 77% the market has
    already marked up return +0.030 and the rest return +0.089. If
    persistent_quote_favourite is right about duration being the axis, this adds
    a second cut to it. If it is wrong, both fall together.
  - It is NOT stable_favourite in different clothes, and that is checkable.
    Splitting this candidate's trades by that candidate's volatility gate gives
    +0.098 (quiet path) against +0.079 (thrashing path) -- both strong -- while
    splitting the RALLIED control the same way gives +0.029 against +0.031 --
    both weak. Path volatility separates nothing; the markup does.
  - The mechanism is inferred, not shown. "The quote has not been refreshed" is
    consistent with the profile, but so is "markets that rally into settlement
    are the ones whose outcome became obvious, so there was never anything left
    in them". Those two stories are not distinguishable from this data and they
    predict different things about what happens when capture is denser.
  - n=350. That is small, and the subset leans on sports and esports totals --
    MLB, NFL, CS2, several football leagues, plus one index series. "The market
    underprices the unmarked favourite" is really "it does so in live sports
    totals, and a few other things agree".
  - Found by scanning. I tested five other mechanisms first: timestamping the
    last trade from volume deltas in the price path, the overround on
    non-ladder multi-leg events, which SIDE of the book moved, volume bursts,
    and hours-to-close. The first four lost money and the fifth turned out to be
    a proxy for quote duration. The intervals here are not corrected for that
    search, so treat +0.089 as the optimistic end.
  - All in-sample. These observations resolved before this file's created_at, so
    INVARIANT #1 excludes every one of them from its score. This docstring is a
    hypothesis with arithmetic attached, not a result.

WHAT WOULD FALSIFY IT. A forward P&L interval including zero on this subset.
More interestingly, it is falsified if the rallied control scores as well
forward as the gate does: that would mean the markup is decoration and the edge
is the ordinary shoulder bias, which nine candidates have already shown is not
tradeable on its own. It is falsified a third way if the effect turns out to be
monotone in the size of the FALL -- that would be an overreaction story, which
is a different mechanism from this one and predicts a different rule.

DELIBERATELY ONE-SIDED. The mirror image does not exist: longshots (0.05-0.35)
that fell over the same hour, bought on the yes side, show an edge of -0.0155.
The whole longshot half of the fillable universe loses money in every version of
this I measured, so it is abstained on rather than traded at reduced size.

Shift is +0.05 against a measured +0.1084, well below the low end of the
interval, on the same reasoning as favourite_longshot and ladder_leader:
carrying a fitted value into a forward test is how in-sample fitting sneaks back
in. It still clears the ~2-point cost of crossing by more than a factor of two.

Abstains on 99.5% of observations, so its whole-population Brier skill will look
like nothing next to candidates that nudge every market. That is the intended
shape.

Parent is last_trade_blend, which is where the reasoning starts and which this
candidate contradicts. That candidate tried to find out where the market really
is by looking at the most recent PRINT; this one gets more out of asking whether
the market has bothered to move at all."""
from __future__ import annotations

from datetime import timedelta

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "unmarked_favourite",
    "generation": 3,
    "parent_id": "last_trade_blend",
    "created_at": "2026-09-05T05:54:06.543982+00:00",
    "rationale": (
        "A favourite the market has already marked up in the last hour is "
        "nearly fair (+0.030/contract); one it has left alone or marked down is "
        "underpriced by ~10.8 points (+0.089/contract). The trigger is the "
        "absence of a recent markup, not the price level or the age of the "
        "quote. Lookback is in minutes, not history points, so the capture "
        "cadence cannot change what it means. Abstains on 99.5%."
    ),
}

EPS = 0.001

LOOKBACK_MINUTES = 60.0  # the shortest window clearly inside the flat region
MAX_RALLY = 0.05         # above this the market has repriced and the edge halves
MIN_PRICE = 0.65         # below this the shoulder bias is not worth the spread
MAX_PRICE = 0.95         # above this there is no room left to be underpriced by
MAX_SPREAD = 0.04        # cost gate; the edge is monotone in how tight the book is
SHIFT = 0.05


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob

    # Cost gate first. Nothing below matters on a book we cannot cross cheaply,
    # and simulate_fill rejects a spread above 0.08 outright.
    if market.spread > MAX_SPREAD or not (MIN_PRICE <= p < MAX_PRICE):
        return p

    # The price an hour ago, read off the timestamps rather than off a count of
    # history points, so that a change in capture cadence cannot quietly turn
    # "an hour" into twenty minutes. No quote that old means no reference, and
    # no reference means stand aside.
    cutoff = context.now - timedelta(minutes=LOOKBACK_MINUTES)
    older = [h for h in context.price_history if h.observed_at <= cutoff]
    if not older:
        return p

    # The whole hypothesis. A favourite the market has already walked upward is
    # one it has thought about; the edge is in the ones it has not.
    if p - older[-1].implied_prob >= MAX_RALLY:
        return p

    return min(p + SHIFT, 1.0 - EPS)
