"""Take the favourite side, but only in markets that have been quoted for hours.

THE EDGE. The favourite-longshot correction has been applied nine different ways
in this project and none of it paid, because every version gated on the PRICE.
The gate that matters is not where the market is priced, it is how long the
market has been continuously quotable. Conditioned on a deep own-quote history,
the high side is underpriced by ~6 points -- three times the ~2-point cost of
crossing -- and conditioned on a shallow one it is underpriced by zero.

The mechanism, offered as a reading and not as a demonstrated cause: the
classic favourite-longshot effect is retail demand for cheap YES accumulating
against patient makers, and accumulation takes time. A market that has held a
two-sided book across hours of capture has had time to build that imbalance. An
hourly index or map-score market that existed for forty minutes has not.

THE EVIDENCE. Measured 2026-09-04 over all 65,102 scoreable entries. Confidence
intervals are bootstrapped over SERIES, not observations, because markets in one
series share an underlying and a day. P&L crosses the spread at the ask and pays
the 1c fee, per INVARIANT #4.

  depth >= 8, spread <= 0.04, mid 0.35-0.95   n=2,503  ser=382
      actual - mid  +0.0622  CI [+0.0401, +0.0843]
      P&L/contract  +0.0425  CI [+0.0204, +0.0649]

The control is the same rule with the depth gate inverted, and it is the whole
argument:

  depth <  8, same price band and spread   n=9,010  ser=57
      actual - mid  +0.0017  CI [-0.0063, +0.0111]   includes zero
      P&L/contract  -0.0164  CI [-0.0246, -0.0070]   negative, excludes zero

Same price band, same cost gate, opposite sign of money. That is what every
price-banded shoulder candidate was averaging together.

The P&L interval excludes zero, which has happened once before here
(ladder_leader) and is the only reason this is worth trading rather than merely
worth noticing.

Robustness: nothing is perched on one cell. Spread cap 0.02/0.03/0.04/0.06/0.08
gives P&L +0.054/+0.049/+0.043/+0.033/+0.029, monotone in tightness as a cost
story predicts. Depth 6/8/12/16 gives +0.043/+0.043/+0.043/+0.042. Price floor
0.30/0.35/0.45/0.55 gives +0.035/+0.043/+0.040/+0.041. No series carries it: the
largest is 6.3% of the sample, leave-one-series-out ranges +0.0342 to +0.0459,
and 35 of the 50 series with n >= 10 are P&L-positive on their own.

This is NOT the staleness artifact documented in observations.py. That one
inflates Brier skill while leaving P&L flat or negative; here the money moves
with the skill.

WHERE IT IS WEAK, stated plainly:

  - The control population is nearly a different universe of markets: 57 series
    against 382, with almost no overlap. So the contrast is substantially
    "long-lived markets versus ephemeral intraday ones" rather than a clean
    within-market comparison, and some of the effect could be series
    composition. Restricting to the 4 series carrying at least 15 observations
    on BOTH sides of the depth gate gives deep +0.0584 against shallow -0.0066,
    with 3 of 4 series agreeing -- the right sign, on far too little data to
    settle it.
  - `len(price_history)` is partly a property of OUR INSTRUMENT, not of the
    market. It is capped at MAX_PRICE_HISTORY = 24 and accrues at the capture
    cadence, so it measures "how long this market sat in the near-pass window"
    as much as anything intrinsic. In the current data it is close to bimodal --
    a market has either a handful of points or nearly the full 24 -- which is
    why depth 6 through 16 all select almost the same set. That is robustness
    against threshold-fitting and simultaneously an admission that the gate
    cannot tell two hours from six. If the capture cadence changes, this gate
    silently changes meaning.
  - Found by scanning. I tested executed order flow, two-leg complement
    pricing, and path-shape conditioning first, and all three were right about
    direction and lost money in the way the brief warns about. The interval is
    not corrected for that search; treat +0.06 as the optimistic end.
  - All in-sample. These observations resolved before this file's created_at,
    so INVARIANT #1 excludes every one of them from its score. This docstring is
    a hypothesis with arithmetic attached, not a result.

WHAT WOULD FALSIFY IT. A forward P&L interval including zero on this subset.
It is also falsified, and more interestingly, if the depth < 8 control scores
as well forward as the rule does: that would mean quote persistence is
decoration and the edge is simply the shoulder bias, which nine candidates have
already shown is not tradeable.

DELIBERATELY DOES NOT TRADE LONGSHOTS. On the same deep population, mid < 0.35
shows the other half of the classic effect at +0.0146 of edge in the correct
direction -- and P&L of -0.0102. Right about direction, still loses money, which
is precisely the failure the governing constraint describes. So that half is
abstained on rather than traded at reduced size.

Distinct from stable_favourite, which gates on the VOLATILITY of the path inside
its window rather than on the length of the window. 78% of what this candidate
trades falls outside stable_favourite's cell and is worth +0.0248 per contract
on its own; and inside the deep population, splitting by that volatility rule
moves P&L only from +0.0237 to +0.0334. The duration gate is doing the work.

Shift is +0.03 against a measured +0.0622 -- below even the low end of the
interval, on the same reasoning as favourite_longshot and ladder_leader:
carrying a fitted value into a forward test is how in-sample fitting sneaks back
in. It is still comfortably above the ~2-point cost of crossing.

Abstains on 96.2% of observations, so its whole-population Brier skill will look
small next to candidates that nudge every market. That is the intended shape."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "persistent_quote_favourite",
    "generation": 3,
    "parent_id": "last_trade_blend",
    # Stamped by hand on 2026-09-05, because the harness did not. This file
    # was the second accepted candidate of one invocation, and generate_once
    # stamped only the first, so it reached the registry carrying the timestamp
    # the agent wrote for itself -- 2026-09-04T00:00:00, backdating it 29 hours
    # and making 83% of its scored observations ones that had already resolved
    # when it was written. The value below is the file's actual creation time,
    # bounded by its run's start (04:54:41) and the next attempt's file
    # (05:27:06). The hole is closed in generate.py; this is the one candidate
    # that went through it.
    "created_at": "2026-09-05T05:08:35+00:00",
    "rationale": (
        "Gate the favourite correction on how long the market has been "
        "continuously quoted, not on its price. Deep own-quote history: high "
        "side underpriced ~6 points and P&L-positive. Shallow history, same "
        "price band: zero edge and negative P&L. Abstains on 96% of markets "
        "and on longshots entirely, since that half loses money."
    ),
}

EPS = 0.001

MIN_HISTORY = 8      # ~2h+ of continuous two-sided quoting at the capture cadence
MAX_SPREAD = 0.04    # cost gate; the edge is monotone in how tight the book is
MIN_PRICE = 0.35     # below this the effect inverts and does not pay to trade
MAX_PRICE = 0.95     # above this there is no room left to be underpriced by
SHIFT = 0.03

# Prices are floats parsed from decimal strings, so a nominal four-cent book
# comes out as 0.040000000000000036 and a bare `> MAX_SPREAD` silently drops
# part of the population measured above. Measured 2026-09-05 on the observations
# passing this candidate's history and price gates: 97 of 110 passed a bare
# `<= 0.04`, so the untoleranced gate discarded 12% of its own population for a
# representation artifact. The gate is meant to read "no wider than four cents";
# make it do that.
SPREAD_TOL = 1e-9


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob

    # Cost gate first. Nothing below matters on a book we cannot cross cheaply.
    if market.spread > MAX_SPREAD + SPREAD_TOL:
        return p

    if not (MIN_PRICE <= p < MAX_PRICE):
        return p

    # The whole hypothesis. A market we have watched hold a two-sided book
    # across many passes is a different population from one that flickered
    # into existence and resolved.
    if len(context.price_history) < MIN_HISTORY:
        return p

    return min(p + SHIFT, 1.0 - EPS)
