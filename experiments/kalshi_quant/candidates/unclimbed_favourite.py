"""Buy the favourite that never had to climb to get where it is.

THE EDGE. Reduce a market's whole quote path to one number: how far it currently
sits ABOVE the lowest point it has visited. Call that the run-up. A favourite
with a small run-up has held its level for the entire session -- it was never
meaningfully cheaper than it is now -- and it is underpriced by ~10.9 points. A
favourite with the same price, the same book and an equally long history, but
which climbed into favouritism, is underpriced by ~4.9, which the ~2-point cost
of crossing eats most of.

The trigger is the DEPTH OF THE PATH BELOW the current quote. Not the price
level, not the length of the quote history, not the volatility of the path, and
not the change over any fixed recent window.

THE EVIDENCE. Measured 2026-09-05 over all 65,225 scoreable entries. Run-up is
`mid - min(mid over price_history)`; the window is whatever history we hold, up
to MAX_PRICE_HISTORY = 24 points (~6h). Intervals are bootstrapped over SERIES,
not observations, because markets in one series share an underlying and a day.
P&L crosses the spread at the ask and pays the 1c fee, per INVARIANT #4.

  run-up < 0.10, path spans > 2h, 0.50 <= mid < 0.95, spread <= 0.04
      n=454  ser=152
      actual - mid  +0.1085  CI [+0.0668, +0.1510]
      P&L/contract  +0.0885  CI [+0.0463, +0.1314]

The control is the same rule with the run-up gate inverted, and it is the whole
argument:

  run-up >= 0.10, same band, same spread, same path length
      n=1704 ser=317
      actual - mid  +0.0494  CI [+0.0297, +0.0685]
      P&L/contract  +0.0289  CI [+0.0090, +0.0484]

Note what that control is NOT: it is not a null. It is the ordinary favourite
bias, positive and already shown by nine candidates to be untradeable once the
spread is paid. The claim is only that the unclimbed favourite carries about
three times as much of it.

The gate holds inside every price band, which rules out the obvious composition
story -- the gated population is cheaper on average (mean mid 0.684 against
0.780), so it is not simply sitting where the bias is largest:

                  gate                control
    0.50-0.65   +0.0687 (n=223)     +0.0135 (n=329)
    0.65-0.80   +0.1222 (n=117)     +0.0504 (n=501)
    0.80-0.95   +0.0926 (n=114)     +0.0223 (n=874)

WHY THE MINIMUM AND NOT THE LATEST MOVE. This is the sharp claim, and it is a
direct correction to unmarked_favourite, which asks the same question over a
fixed 60-minute window. Splitting the deep-path favourite population by both
gates at once:

    run-up < 0.10 and 60-min move < +0.05   n= 409  P&L +0.0962 [+0.0555,+0.1326]
    run-up < 0.10 and 60-min move >= +0.05  n=  45  P&L +0.0180 (interval useless)
    run-up >= 0.10 and 60-min move < +0.05  n= 227  P&L +0.0453 [-0.0039,+0.0876]
    run-up >= 0.10 and 60-min move >= +0.05 n=1477  P&L +0.0263 [+0.0052,+0.0473]

Inside the quiet-last-hour group the run-up gate roughly doubles P&L and moves
the interval clear of zero. So an hour is the wrong window: a market can sit
still for sixty minutes and still have climbed twenty points earlier in the
session, and those are the ones that pay like ordinary favourites. The run-up is
a statement about the whole path, and it is the one that carries the money.

It is also not stable_favourite's volatility gate, and that is checkable: inside
run-up < 0.10 the quiet paths pay +0.0862 and the thrashing ones +0.0976 -- both
strong, so path volatility separates nothing here -- while run-up >= 0.10 with a
quiet path is +0.0213 with an interval spanning zero.

Nor is it an oversold-bounce story. Splitting the gated set by whether the market
FELL to its low or simply never left it gives +0.0771 (n=266) against +0.1046
(n=188). Both work, and the flat half is if anything the stronger, so the reading
is "no ascent happened", not "the dip will be bought back".

ROBUSTNESS. Nothing here is perched on one cell. Every threshold was swept and
every variation keeps a P&L interval clear of zero:

  run-up cut 0.03/0.05/0.08/0.10/0.15/0.20/0.30
      +0.086/+0.094/+0.096/+0.089/+0.078/+0.071/+0.068  -- monotone, broad plateau
  path length 30/60/120/180/240/360 min
      +0.061/+0.091/+0.089/+0.088/+0.088/+0.086
  spread cap 0.02/0.03/0.04/0.06/0.08
      +0.086/+0.081/+0.089/+0.073/+0.069  -- decays with width, as a cost story predicts
  price floor 0.35/0.45/0.50/0.60/0.65/0.70
      +0.065/+0.079/+0.089/+0.095/+0.108/+0.094
  price ceiling 0.80/0.90/0.95/0.99
      +0.087/+0.093/+0.089/+0.077

The statistic itself is robust, not just the thresholds. Using the SECOND-lowest
point of the path instead of the minimum gives +0.0818 on n=431, so a single
anomalous low print is not doing the work. Note also the direction in which that
error would push: a spurious low quote INCREASES the measured run-up and
therefore pushes an observation OUT of the gate. The statistic fails toward
abstention.

It is not a restatement of the history-depth gate either. Adding a minimum
history-point requirement on top changes nothing at all -- nh >= 2/4/8/12/16
gives +0.0885/+0.0891/+0.0889/+0.0878/+0.0857 -- because the two-hour span
already selects deep paths (median 24 points, the cap).

It is NOT the staleness artifact documented in observations.py, which inflates
Brier skill while leaving P&L flat. The gate beats the control in every
staleness bucket, including the freshest: 0-15 min +0.111 vs +0.059, 15-30 min
+0.176 vs +0.060, 30-60 min +0.067 vs +0.012.

STABILITY, which is where this differs most from its neighbours. Splitting on
resolution date, the first half gives +0.0812 [+0.0282,+0.1344] and the second
+0.0958 [+0.0443,+0.1437], against a control of +0.0323 and +0.0245. Both halves
carry it. neglected_leg and untraded_favourite both live almost entirely in the
back half of the same sample; this does not. 10 of the 12 days are P&L-positive,
and the two that are not have n=43 and n=3.

No series carries it: the largest is KXMLBTOTAL at 36 of 454 (7.9%),
leave-one-series-out over the top eight ranges +0.0821 to +0.0958 with every
interval clear of zero, and the top contributors are sports and esports totals
(MLB, CS2, LoL, NFL, J-League, EFL Championship) rather than the daily crypto
ladders that dominate ladder_upper_body and neglected_leg.

WHERE IT IS WEAK, stated plainly:

  - IT IS LARGELY A SHARPENING OF unmarked_favourite, not an independent
    discovery, and it should be read as one. 409 of the 454 trades also satisfy
    that candidate's 60-minute gate. The 45 that do not are too few to say
    anything. What this file adds is the claim that the RIGHT window is the whole
    path rather than the last hour, and the 2x2 above is the only evidence for
    that -- one 227-observation cell doing the separating. If unmarked_favourite
    is wrong about the direction, this falls with it.
  - THE GATE INVERTS BELOW 0.35, and I cannot explain why. Same rule, same path
    length, on longshots: at 0.20-0.35 the gated set returns -0.0275 against a
    control of +0.0272, and at 0.02-0.20 both sides lose. "A price that never had
    to climb is underpriced" ought to be a statement about persistence, and
    persistence does not obviously stop applying at 0.35. Either the mechanism is
    really about the favourite side specifically -- retail demand accumulating
    against a maker who does not lift the offer -- or part of the effect is
    something narrower that happens to live at high prices.
  - 233 of the 454 are in ladder-shaped events (legs summing above 1.2), the same
    terrain as ladder_upper_body, and they are the stronger half: +0.1341 against
    +0.0404 for the non-ladder half, whose interval spans zero [-0.0117,+0.1073].
    So the claim is well established on ladders and merely suggestive elsewhere.
    140 of the 454 also fall inside neglected_leg's volume-share gate; removing
    them leaves n=314 at +0.0715 [+0.0276,+0.1198], which is the one overlap check
    that comes back clean.
  - The 0.50-0.65 band is the weakest third and its interval barely clears zero
    (+0.0687 [+0.0054,+0.1331]). It is shipped anyway, deliberately: no candidate
    here trades below 0.60, the band-wise control comparison holds there, and
    0.65 is the maximum of the floor sweep, so anchoring on it would be fitting to
    the peak. It is the part most likely to disappoint forward.
  - The window is our instrument. `price_history` is capped at 24 points, so
    "never been cheaper" means "never been cheaper in the last ~6 hours of
    capture", not over the market's life. A longer cap would change the
    population, and the fact that the path-length sweep is flat from 60 minutes
    out says only that most gated markets are already at the cap.
  - The mechanism is inferred, not shown. "The market never repriced upward, so
    the accumulated retail demand for the favourite never got corrected" fits the
    profile, but so does "a market that has held one level all session is one
    whose maker has a stale volatility number", and so does "these are the
    outcomes that were always going to happen, and the price simply lags". Those
    predict different things about denser capture and this data cannot separate
    them.
  - Found by scanning. I tested three mechanisms before this one: longshot rally
    reversal (cheap contracts that just jumped, bought on the NO side -- the
    rallied longshots turned out to be UNDERpriced, so the hypothesis was
    backwards), frozen quotes measured by counting quote changes along the path
    (the many-changes bucket was the better one, opposite to the story), and a
    44-gate sweep across sibling structure, order-flow composition, book depth,
    tick structure and time-to-close. The intervals here are not corrected for
    that search, so treat +0.0885 as the optimistic end of what a forward test
    should show.
  - All in-sample. Every observation above resolved before this file's
    created_at, so INVARIANT #1 excludes all of them from its score. This
    docstring is a hypothesis with arithmetic attached, not a result.

WHAT WOULD FALSIFY IT. A forward P&L interval including zero on this subset.
More sharply, and this is the claim the file exists to test, it is falsified as a
statement about WINDOWS if the `run-up >= 0.10 and 60-min move < +0.05` cell pays
as well forward as the gated cell: that would mean the last hour is a sufficient
lookback after all, the whole path adds nothing, and this is unmarked_favourite
with a more expensive statistic. It is falsified as a statement about ascent if
the effect turns out to be monotone in the size of the FALL from the path high --
that would be an overreaction story, which the flat/fell split above argues
against but does not close. And it is falsified as a mechanism if the inversion
below 0.35 disappears forward while the favourite side holds, which would mean
the price band, not the path, is what the gate is really selecting.

DELIBERATELY ONE-SIDED. The mirror was measured and does not exist. Longshots
sitting at their own session HIGH -- the same statistic reflected, which should
mark the overpriced cheap contract -- are underpriced by +0.0146, and buying NO
there returns -0.0347 per contract. Wrong direction and loses money, so that half
is abstained on rather than traded small.

Shift is +0.04 against a measured edge of +0.1085, below the low end of the
interval (+0.0668) and around a third of the point estimate, on the same
reasoning as favourite_longshot and ladder_leader: carrying a fitted value into a
forward test is how in-sample fitting sneaks back in. It still clears the
~2-point cost of crossing by a factor of two.

Abstains on 99.3% of observations. Its whole-population Brier skill at this shift
is +0.0004, so it will look like nothing next to candidates that nudge every
market. That is the intended shape: acting on 0.7% of markets at four points
beats nudging all of them at one and paying the spread each time.

Parent is last_trade_blend, which is where the reasoning starts and which this
candidate answers. That candidate reached for one historical price -- the last
print -- because the mid is "where nobody traded", and got +0.0019 for it. The
objection was never that history is uninformative; it was that the last print is
the wrong summary of it, and it arrives without a timestamp. This candidate keeps
the idea that the path knows something the current quote does not, and takes the
MINIMUM of the path instead of its most recent point."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "unclimbed_favourite",
    "generation": 3,
    "parent_id": "last_trade_blend",
    "created_at": "2026-09-05T06:56:02.311468+00:00",
    "rationale": (
        "Reduce the quote path to one number: how far the market sits above the "
        "lowest point it has visited. A favourite that never had to climb to get "
        "here is underpriced by ~10.9 points; one that climbed into favouritism "
        "by ~4.9, which the spread eats. Corrects unmarked_favourite's window -- "
        "inside its quiet-last-hour population the run-up gate doubles P&L, "
        "because a market can sit still for an hour and still have climbed "
        "twenty points earlier in the session. Abstains on 99.3%."
    ),
}

EPS = 0.001

MIN_PRICE = 0.50          # below 0.35 the gate inverts; 0.50 is the safe side of that
MAX_PRICE = 0.95          # above this there is no room left to be underpriced by
MAX_SPREAD = 0.04         # cost gate; the edge decays monotonically as the book widens
MIN_PATH_MINUTES = 120.0  # a run-up is only meaningful over a real observation window
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
