"""Buy the favourite whose recent trading CREATED positions instead of churning
existing ones.

THE EDGE. Volume and open interest are two different questions about the same
flow. Volume says how many contracts changed hands; open interest says how many
of them are still held. Divide the change in one by the change in the other
along the market's own quote path and you get a single number -- call it the
capture ratio -- saying what fraction of recent trading opened new positions
rather than passing existing ones between traders.

A favourite whose recent flow was mostly position-opening is underpriced by
~6.7 points. A favourite at the same price, on an equally tight book, with an
equally busy tape, whose flow was mostly churn is underpriced by ~2.2 points,
which the ~2-point cost of crossing eats entirely.

The trigger is the COMPOSITION of the flow, not its size. That distinction is
the whole hypothesis, and it is why this is not its parent.

WHY THIS IS NOT volume_weighted. The parent reads volume as a scalar measure of
how much a market knows -- deviate more from the quiet ones, less from the busy
ones -- and scored a near-null. Its own docstring concluded that the mispriced
markets are the untradeable ones, because quiet markets are wide. The claim here
is that volume was never the right axis: the same 10,000 contracts mean one thing
when they leave 10,000 open positions behind and another when they leave none,
and only the first kind of market carries the bias. Split the same population by
both axes at once (own volume against the median of 9,436 contracts):

    own volume high, capture >= 0.6   n= 656 ser=140  P&L +0.0447 [+0.0082,+0.0759]
    own volume high, capture <  0.6   n=1165 ser=103  P&L +0.0033 [-0.0447,+0.0251]
    own volume low,  capture >= 0.6   n=1163 ser=235  P&L +0.0473 [+0.0287,+0.0707]
    own volume low,  capture <  0.6   n= 657 ser= 70  P&L +0.0038 [-0.0207,+0.0303]

Capture separates inside both halves of the volume distribution and volume
separates nothing inside either capture group. The parent's own gate run over
the same band is if anything backwards: volume >= 10,000 pays +0.0194 with an
interval spanning zero against +0.0302 below it. Heavily traded markets are not
the efficient ones. Heavily CHURNED markets are.

THE MECHANISM, offered as a reading and not as a demonstrated cause. The
favourite-longshot effect this project keeps rediscovering is usually explained
as retail demand accumulating against a patient maker. Accumulation is exactly
what open interest measures: it only grows when someone opens a position and
holds it. A market being scalped between bots produces enormous volume and no
accumulation, and it has no reason to carry a retail-demand bias at all. So the
prediction is that the bias lives where positions are being built and vanishes
where contracts are only being recycled. That is what the numbers below show,
and the honest caveat about them is in WHERE IT IS WEAK.

THE EVIDENCE. Measured 2026-09-05 over all 65,225 scoreable entries. Capture is
`(open_interest - open_interest at the oldest held quote) / (volume - volume at
the same point)`, i.e. measured over whatever price history we hold, up to
MAX_PRICE_HISTORY = 24 points. Intervals are bootstrapped over SERIES, not
observations, because markets in one series share an underlying and a day. P&L
crosses the spread at the ask and pays the 1c fee, per INVARIANT #4.

  capture >= 0.6, 0.60 <= mid < 0.95, spread <= 0.04, path flow >= 25 contracts
      n=1819  ser=302
      actual - mid  +0.0670  CI [+0.0492, +0.0861]
      P&L/contract  +0.0464  CI [+0.0284, +0.0655]

The control is the same rule with the capture gate inverted, and it is the whole
argument:

  capture < 0.6, same band, same spread, same flow requirement
      n=1822  ser=144
      actual - mid  +0.0222  CI [-0.0071, +0.0405]
      P&L/contract  +0.0035  CI [-0.0245, +0.0216]

Run through the real scorer rather than a private harness, the candidate acts on
those same 1,819 entries; simulate_fill rejects 214 of them on its 25%-of-depth
cap, and the 1,605 that do fill pay +0.0466, so the depth cap is not selecting
the profitable half.

Note what that control is NOT: it is not a null population. It is the ordinary
favourite bias, positive on average, and already shown by nine scored candidates
to be untradeable once the spread is paid. The claim is that the unchurned
favourite carries about three times as much of it.

The profile across the whole capture axis is what makes the cut a cut rather
than a fitted threshold. P&L per contract by capture bucket, same band and
spread:

    capture < 0.02       n=  48  +0.0021
    0.02 - 0.20          n= 167  +0.0110
    0.20 - 0.50          n=1151  +0.0016
    0.50 - 0.80          n=1361  +0.0294
    0.80 - 0.98          n= 665  +0.0503
    0.98 and above       n= 375  +0.0306

Flat and near zero below 0.5, then a step. The cut sweep is correspondingly flat
on the far side: 0.3/0.4/0.5/0.6/0.7/0.8/0.9 gives P&L +0.028/+0.032/+0.038/
+0.046/+0.048/+0.049/+0.044. 0.6 is the near edge of that plateau, chosen so the
population stays large rather than because it is the peak.

The statistic is bounded where it should be, which is a small piece of evidence
that it is measuring what it claims: capture never once exceeds 1.0 across all
37,187 entries where it is defined -- open interest never grows faster than the
volume that created it -- and is negative on 378, where positions were being
closed out.

ROBUSTNESS. Nothing is perched on one cell.

  path flow floor 0/5/25/100/500 contracts
      +0.043/+0.044/+0.046/+0.047/+0.057  -- rises with the floor, as a
      signal-to-noise story predicts, since a ratio computed on three contracts
      of turnover is noise. 25 is a guard, deliberately not the peak.
  spread cap 0.02/0.03/0.04/0.06/0.08
      +0.050/+0.038/+0.043/+0.034/+0.027  -- broadly decaying with width as a
      cost story predicts, though not cleanly monotone: 0.03 dips below 0.04.
  price floor 0.50/0.55/0.60/0.65/0.70
      +0.042/+0.042/+0.043/+0.041/+0.033
  price ceiling 0.85/0.90/0.95/0.99
      +0.058/+0.055/+0.043/+0.020  -- the usual story that there is no room left
      to be underpriced by near 1.0.

No series carries it. The largest is KXETHD at 5.3% of the gated observations,
leave-one-series-out over the top eight ranges +0.0413 to +0.0496, and the
contributors are a mix of crypto dailies, sports and esports totals and index
markets (ETH, MLB totals, SOL, CS2 maps and games, index up/down, LoL maps).
11 of the 13 resolution days are P&L-positive.

STABILITY IN TIME. Splitting on resolution date, the first half gives +0.0258
[+0.0003, +0.0511] and the second +0.0618 [+0.0416, +0.0824], against a control
of +0.0150 and -0.0065. Both halves carry it, but the first half only barely --
its interval low end is +0.0003 -- so half the strength of the headline number
comes from the more recent week.

IT IS NOT THE STALENESS ARTIFACT documented in observations.py, which inflates
Brier skill while leaving P&L flat. The gate beats the control in every staleness
bucket including the freshest, where the control loses money:

    0-15 min   gate +0.0342   control -0.0260
    15-30 min  gate +0.0506   control +0.0151
    30-60 min  gate +0.0408   control +0.0185

IT IS NOT persistent_quote_favourite's history-depth gate, and that is checkable
as a 2x2, which is the control that matters most here because 70% of the trades
fall inside that candidate's population:

                        capture >= 0.6        capture < 0.6
    history >= 8 pts    +0.0537 (n=1274)      +0.0199 (n= 406)
    history <  8 pts    +0.0293 (n= 545)      -0.0012 (n=1416)

Capture separates inside both depth halves. This is the test the 60-minute
version of the same statistic FAILS -- see below -- and it is the reason the
window here is the whole held path rather than a fixed recent one.

WHERE IT IS WEAK, stated plainly. There is a lot of this, and the first two
items are the ones that would stop me trading it with real money:

  - IT DOES NOT SEPARATE WITHIN A SERIES. Restricting to the 26 series carrying
    at least 10 observations on both sides of the gate, the gated half pays more
    in 15 of them. That is a coin flip. So most of what the gate does is choose
    between POPULATIONS of markets -- roughly, position-taking sports and index
    markets against scalped crypto ladders -- rather than distinguishing two
    states of the same market. Two readings are available. The generous one is
    that this is what the mechanism predicts: churn versus accumulation is a
    property of who trades a market, so it should be mostly a between-market
    axis, and a within-series test is then the wrong test. The unkind one is
    that "capture ratio" is an elaborate way of writing "not a crypto
    fifteen-minute ladder", and that the honest statement of the finding is a
    taxonomy of venues rather than a signal. I cannot separate those with this
    data and I lean about 60/40 toward the unkind one.
  - THE MIRROR IS NOT MERELY ABSENT, IT IS INVERTED, and this is the sharpest
    argument against the mechanism. If position-taking flow creates the
    favourite-longshot bias, the longshot half should be MORE overpriced in
    high-capture markets. It is less. Buying NO on longshots priced 0.05-0.35:

        capture >= 0.6   n=3675 ser=425  edge -0.0182  P&L -0.0030
        capture <  0.6   n=2221 ser=199  edge -0.0357  P&L +0.0156

    The churned markets carry the larger longshot overpricing, which the
    accumulation story says should not happen. Either the mechanism is wrong and
    the gate is picking up something else that happens to sort favourites well,
    or the two tails of these markets are set by different processes. The
    candidate trades only the favourite side, so it is not exposed to this
    directly -- but a mechanism whose mirror runs backwards is a mechanism I do
    not really understand.
  - The 60-minute version of the statistic does not work. Recomputing capture
    over only the last hour of the path gives, at a 0.7 cut, +0.0444 for the
    gate against +0.0460 for the control -- no separation at all. So this is not
    a claim about recent flow; it only works as a claim about the character of
    the market over its whole held path. That is a coherent position, and it is
    also exactly what you would expect if the statistic were a market-type
    proxy, which is why it appears in this section rather than the last one.
  - There is a hole in the middle of the path-length distribution. By the span
    of held history, the gate pays +0.0518 (n=307) under 30 minutes, -0.0115
    (n=223, 17 series) between 30 and 120 minutes, +0.0341 (n=116) from 2 to 6
    hours, and +0.0571 (n=1173) beyond. The bulk sits in the last bucket and the
    negative cell is small, but a clean effect would not have that hole, and no
    span gate is applied because carving it out would be fitting to the sample.
  - The two negative days are not the usual tiny ones. 2026-08-26 loses 0.0346
    on 157 observations and 2026-09-02 loses 0.0039 on 146. Neighbouring
    candidates can dismiss their bad days as n=3; this one cannot.
  - Overlap with what is already here. 70% of the trades fall inside
    persistent_quote_favourite's population; removing them leaves n=545 at
    +0.0293 [+0.0051, +0.0660], clear of zero but across only 42 series. 49%
    sit in ladder-shaped events (legs summing above 1.2), the terrain of
    ladder_upper_body; removing those leaves n=920 at +0.0312 [+0.0042,
    +0.0551]. Overlap with unmarked_favourite is 19%, with unclimbed_favourite
    13%, and with neglected_leg 22% (removing that last group changes nothing,
    +0.0460). Overlap with untraded_favourite is exactly zero, by construction:
    a market with no volume has no capture ratio.
  - Found by scanning, and the scan was wide. Before this I measured and
    discarded: the event-complement gap on exclusivity-bracketed multi-leg
    events (flat or negative across its whole range, n=431); the leg whose event
    rivals have all been marked to zero (n=6, useless); markets still quoted
    after their close_time (218 entries in the whole sample, almost all at
    extreme prices); coarse whole-cent quoting inside fine-grid deci-cent
    markets (only 12 series, and the direction ran opposite to the story); event
    overround; sibling book quality; quote refresh rate; spread trajectory along
    the path; flow decay between the first and second half of the path; top-of-
    book size ratio; and two other open-interest normalisations (OI over
    lifetime volume, and OI growth against its own base), both of which measured
    weaker than this one. The intervals above are not corrected for that search,
    so treat +0.0464 as the optimistic end of what a forward test should show.
  - All in-sample. Every observation above resolved before this file's
    created_at, so INVARIANT #1 excludes all of them from its score. This
    docstring is a hypothesis with arithmetic attached, not a result.

WHAT WOULD FALSIFY IT. A forward P&L interval including zero on this subset.
More sharply, and this is what the file exists to test, it is falsified as a
statement about FLOW COMPOSITION if the effect stops surviving the split against
own volume -- if high-volume high-capture markets stop paying while low-volume
ones carry it, then this is the parent's quiet-market gradient after all, at a
threshold the parent did not try. It is falsified as a MECHANISM, though not
necessarily as a signal, if the longshot inversion above persists forward: the
accumulation story requires both tails to move together and they currently do
not. And it is falsified as anything more than a taxonomy if a series carrying
enough observations on both sides shows no split -- which is close to what the
26 series testable today already say, and is the single result most likely to
kill this candidate.

DELIBERATELY ONE-SIDED. Only the favourite side is traded. The longshot mirror
is measured above and is both weaker and pointed the wrong way, so it is
abstained on rather than traded small.

Shift is +0.03 against a measured edge of +0.0670, below the low end of the
interval (+0.0492) and under half the point estimate, on the same reasoning as
favourite_longshot and ladder_leader: carrying a fitted value into a forward
test is how in-sample fitting sneaks back in. It still clears the ~2-point cost
of crossing.

Abstains on 97.2% of observations. Its whole-population Brier skill at this
shift is +0.0006, so it will look like nothing beside candidates that nudge
every market. That is the intended shape: acting on 2.8% of markets at three
points beats nudging all of them at one and paying the spread each time.
"""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "unchurned_favourite",
    "generation": 3,
    "parent_id": "volume_weighted",
    "created_at": "2026-09-05T07:09:47.825862+00:00",
    "rationale": (
        "Divide the change in open interest by the change in volume along the "
        "market's own path: the fraction of recent trading that opened "
        "positions rather than recycling them. Favourites whose flow built "
        "positions are underpriced by ~6.7 points; equally busy favourites "
        "whose flow was churn by ~2.2, which the spread eats. The parent read "
        "volume as a scalar; the same turnover means different things "
        "depending on whether anyone kept what they bought. Abstains on 97.2%."
    ),
}

EPS = 0.001

MIN_PRICE = 0.60        # below this the favourite bias does not cover the spread
MAX_PRICE = 0.95        # above this there is no room left to be underpriced by
MAX_SPREAD = 0.04       # cost gate; the edge broadly decays as the book widens
MIN_PATH_FLOW = 25.0    # contracts; a capture ratio on three contracts is noise
MIN_CAPTURE = 0.60      # near edge of the plateau, not its peak
SHIFT = 0.03

# Prices are floats parsed from decimal strings, so a nominal four-cent book
# comes out as 0.040000000000000036 and a bare `> MAX_SPREAD` silently drops
# part of the population measured above. The gate is meant to read "no wider
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

    # Both quantities are lifetime cumulative on the snapshot, so the flow over
    # the window is the difference against the oldest quote we hold. The window
    # is the whole held path on purpose: recomputed over the last hour this
    # statistic separates nothing, which is stated plainly in the docstring.
    traded = market.volume - hist[0].volume
    if traded < MIN_PATH_FLOW:
        return p

    # The whole hypothesis. Not how much traded -- what the trading left behind.
    # Near 1.0, every contract that changed hands is still held by someone who
    # opened a position; near 0.0, the same contracts are being passed around.
    opened = market.open_interest - hist[0].open_interest
    if opened / traded < MIN_CAPTURE:
        return p

    return min(p + SHIFT, 1.0 - EPS)
