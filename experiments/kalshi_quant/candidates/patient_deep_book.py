"""Buy the favourite whose book carries real size while it still has days to prove it right.

THE EDGE. Resting size at the touch is a claim by whoever is quoting: I am
willing to trade thousands of these, right now, at this price. deep_book_favourite
showed that claim is worth something -- a favourite backed by >=2,000 contracts
of two-sided depth is underpriced by ~4.8 points, against ~2.7 for a thin book at
the same price -- but it measured that claim at a single instant and never asked
whether the instant matters. It does. The same 2,000-contract book means a
market-maker committing size when there is no news left to arrive (last hour
before a game ends) versus one committing it with days of drift still ahead. Only
the second is a market-maker actually underwriting an opinion; the first is
mostly a book that has not yet been asked to go one-sided.

Split deep_book_favourite's own population by hours to close and the depth
signal comes apart cleanly: a deep favourite with six or more hours left to
close is underpriced by ~6.5 points; the identical rule inside the final six
hours is underpriced by ~3.6, which is close enough to the ordinary favourite
bias that the ~2-point cost of crossing eats most of it.

The trigger is DEPTH CONDITIONED ON HORIZON -- resting size read only when there
is enough time left for that size to represent a standing opinion rather than a
closing formality. Not the size alone, and not the horizon alone.

WHY THIS IS NOT ITS PARENT. unclimbed_tight (and unclimbed_favourite before it)
reduce `price_history` to one number -- how far the current quote sits above the
path's own low -- and read nothing about the book itself. This reads nothing
about the path at all: `yes_bid_size` and `yes_ask_size` at the current instant,
and `close_time - now`. A market can qualify here on its very first snapshot,
with zero price history, which the run-up family cannot act on by construction
(MIN_PATH_MINUTES = 120 requires two hours of quotes just to compute a run-up).
The two gates are structurally disjoint in what they need to see, not merely
tuned to different constants of the same statistic.

WHY THIS IS NOT deep_book_favourite EITHER, despite sharing its depth threshold
and price band verbatim for comparability. That candidate's own "WHERE IT IS
WEAK" section flags exactly this gap and declines to close it: "I did not gate
on horizon for exactly that reason, but I cannot remove the population it
contaminates." This candidate is that missing gate, not a retuned copy of the
existing one -- it reads a field (`close_time`) the parent candidate never
touches.

THE EVIDENCE. Measured 2026-09-23 over all 184,610 entries in the current
store (144,751 of which are in the shared digest; the store has grown since).
Intervals are bootstrapped over SERIES, not observations, because markets in
one series share an underlying and a maker. P&L crosses the spread at the ask
and pays the taker fee, per INVARIANT #4.

  depth >= 2,000, hours to close >= 6, 0.60 <= mid < 0.95, spread <= 0.04
      n=2,127  ser=323   (1.15% of the store)
      actual - mid   +0.0648  CI [+0.0430, +0.0848]
      P&L/contract   +0.0419  CI [+0.0200, +0.0618]

The two controls that matter, since this is a two-axis claim:

  depth <  2,000, same horizon, band and spread   (horizon held fixed, depth gate inverted)
      n=2,759  ser=435
      actual - mid   +0.0469  CI [+0.0301, +0.0646]
      P&L/contract   +0.0219  CI [+0.0050, +0.0397]

  depth >= 2,000, hours to close < 6, same band and spread   (depth held fixed, horizon gate inverted)
      n=3,144  ser=27
      actual - mid   +0.0357  CI [+0.0142, +0.0488]
      P&L/contract   +0.0130  CI [-0.0100, +0.0273]

Depth separates with horizon held fixed (+0.0419 against +0.0219) and horizon
separates with depth held fixed (+0.0419 against +0.0130, the only one of the
four cells whose interval does not clear zero). Read what that means plainly:
gating on depth alone -- deep_book_favourite's own rule -- is a blend of a cell
that clears the cost of trading and one that barely does, and the near-horizon
cell is the weaker HALF of its own population, not a rare contaminant. Over the
full population deep_book_favourite trades under this same band and spread
(n=5,271), the far-horizon 40% carries a pooled P&L of +0.0419 and the
near-horizon 60% carries +0.0130 -- so this candidate is not adding a filter on
top of a working rule, it is separating a working rule from a marginal one
inside the parent's own trades.

Note the near-horizon control's series count: 27, against 323 and 435 for the
other two cells. A deep, two-sided book inside the last six hours before close
is concentrated in a narrow slice of fast-cycling venues (15-minute crypto and
index strikes trade continuously right up to expiry; most other series go
one-sided long before then), so that comparison is a controlled cut of a genuinely
different population, not a coincidence of sample size.

ROBUSTNESS. Thresholds were swept in both dimensions, same band and spread:

  hours-to-close cut 1/2/4/6/12/24/48
      +0.042/+0.042/+0.042/+0.042/+0.042/+0.042/+0.052
  (flat because the far side is already almost entirely >24h once past the
  1-hour cliff; 48h shows the same direction on a smaller, thinner sample --
  n=1,262, ser=227 -- and is not used as the cut for that reason)

  depth floor 500/1,000/1,500/2,000/3,000/5,000, horizon >= 6h fixed
      +0.040/+0.042/+0.044/+0.042/+0.041/+0.038  -- broad plateau, not a cliff

  spread cap 0.02/0.03/0.04/0.06/0.08
      +0.046/+0.043/+0.042/+0.040/+0.038  -- decays with width, as a cost story predicts

Price band split inside the gate:

    0.60-0.70   +0.0601 (n=526)
    0.70-0.80   +0.0436 (n=546)
    0.80-0.90   +0.0442 (n=626)
    0.90-0.95   +0.0144 (n=429)

The top band is the weakest, same shape every favourite candidate in this
project shows near its ceiling -- there is little room left to be underpriced
by. Kept rather than trimmed to the stronger bands, on the same reasoning
unclimbed_favourite gives for keeping its own weak 0.50-0.65 band: narrowing to
the peak after seeing it is how a threshold gets fitted rather than chosen.

No single series carries it. Largest contributor is KXMLBTOTAL at 224 of 2,127
(10.5%); the ten largest together are 29%. Leave-one-out on the six largest
ranges +0.0343 to +0.0455, every one clear of the pooled estimate's own lower
bound. Split by resolution date, the first half gives +0.0496 [+0.0219,+0.0754]
on n=1,063 and the second +0.0343 [+0.0070,+0.0589] on n=1,064 -- both clear
zero, neither the dramatic collapse that sank an earlier finding in this
project.

NOT A STALENESS ARTIFACT, checked the way the project now checks for one: split
by minutes between entry and resolution, gate against the depth-inverted
control at matched horizon:

    staleness 0-15 min    gate +0.028 (n=72)    control +0.048 (n=197)
    staleness 15-30 min   gate +0.064 (n=427)   control +0.053 (n=676)
    staleness 30-60 min   gate +0.037 (n=1,628) control +0.008 (n=1,886)

The gate beats the control in the two buckets that hold the overwhelming
majority of the sample, including 30-60 minutes -- the bucket where a live
trader would actually be filling and where deep_book_favourite's own docstring
worried the effect might disappear. It loses the 0-15 minute comparison, but
that cell holds 72 observations against the other two buckets' 2,000+; read it
as noise, not as evidence the effect concentrates in stale entries.

WHERE IT IS WEAK, stated plainly:

  - The near-horizon control's 27 series is a narrow venue base by construction
    (fast-cycling strikes are what keeps a book deep AND two-sided that close to
    expiry), so "the near-horizon cell is weaker" is also, in part, "this
    particular slice of crypto and index markets prices its book differently
    close to expiry." I cannot fully separate the horizon story from a venue
    story with this sample; the leave-one-out check above at least shows no
    single series among those 27 is required for the far-horizon result to
    hold, since none of them are IN the far-horizon cell by definition.
  - The 48-hour strengthening (+0.052 against +0.042) is suggestive of a
    gradient beyond the shipped cut, the same shape unclimbed_favourite's
    mechanism audit found for its own horizon interaction, but n=1,262 here is
    half the shipped sample and I am not carrying it forward as a tighter
    threshold -- that would be fitting to a number found by the same sweep that
    picked the cut.
  - The mechanism is inferred, not shown. "A maker only commits standing size
    to an opinion when there is time left for it to be tested" fits the
    numbers, but "far-horizon deep books are drawn from a calmer mix of series
    than near-horizon ones" is an equally consistent story, and deep_book_favourite
    already flagged the venue-taxonomy risk for depth alone; conditioning on
    horizon does not remove that risk, it just moves where it might be hiding.
  - Found by directly testing the open question deep_book_favourite's own
    docstring left standing, not by a wide scan -- which means the two-axis
    split above is the whole search, but it also means there is no correction
    for scanning many alternative cuts the way most candidates in this project
    disclose. Treat the interval as more likely to be the honest one than most,
    and no more than that.
  - All in-sample. Every observation above resolved before this file's
    created_at, so INVARIANT #1 excludes every one of them from this
    candidate's own score. This docstring is a hypothesis with arithmetic
    attached, not a result.

WHAT WOULD FALSIFY IT. A forward P&L interval including zero on the gated
subset. More sharply, it is falsified as a claim about HORIZON specifically if
the near-horizon control (depth >= 2,000, hours to close < 6) pays as well
forward as the gate -- that would mean the split found here was this sample's
noise, and deep_book_favourite's ungated rule was already the honest one. And it
is falsified as a claim about PATIENCE rather than VENUE MIX if excluding every
series that appears in the near-horizon control's 27 turns the far-horizon
result negative, which the data here cannot rule out.

DELIBERATELY ONE-SIDED. The mirror was checked and does not hold. On the
longshot side (0.05-0.40) at the same horizon, buying NO on a deep book returns
-0.0331 per contract against -0.0137 on a thin one -- a deep book on a longshot
does not mark it as overpriced, if anything the opposite -- so that half is
abstained on rather than traded small.

Shift is +0.03 against a measured actual-mid gap of +0.0648, below the low end
of the interval (+0.0430) and under half the point estimate, on the same
reasoning as every shipped candidate in this project: carrying a fitted value
into a forward test is how in-sample fitting sneaks back in. It still clears
the ~2-point cost of crossing by a comfortable margin.

Abstains on roughly 99% of observations by construction. Its whole-population
Brier skill will look like nothing beside candidates that nudge every market --
that is the intended shape, not a defect: acting on ~1% of markets at six
points beats nudging all of them at one and paying the spread each time.
"""
from __future__ import annotations

from datetime import timedelta

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "patient_deep_book",
    "generation": 3,
    "parent_id": "unclimbed_tight",
    "created_at": "2026-09-23T11:54:30.643194+00:00",
    "rationale": (
        "deep_book_favourite reads resting size at one instant and never asks "
        "whether the instant matters. Split its own population by hours to "
        "close: a deep favourite with >=6h left is underpriced by ~6.5 points, "
        "the same book inside the final 6h by ~3.6, which the spread nearly "
        "eats. Depth separates with horizon fixed and horizon separates with "
        "depth fixed -- a genuine two-axis interaction, not a retuned copy of "
        "either parent. Needs no price_history, unlike unclimbed_tight. "
        "Abstains on ~99%."
    ),
}

EPS = 0.001

MIN_PRICE = 0.60          # below this the favourite bias does not cover the spread
MAX_PRICE = 0.95          # above this there is no room left to be underpriced by
MAX_SPREAD = 0.04         # cost gate; the edge decays as the book widens
MIN_DEPTH = 2000.0        # contracts at the touch, both sides summed -- deep_book_favourite's own threshold
MIN_HORIZON_HOURS = 6.0   # below this a deep book is a closing formality, not a standing opinion
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

    bid_size, ask_size = market.yes_bid_size, market.yes_ask_size
    if bid_size is None or ask_size is None or bid_size + ask_size < MIN_DEPTH:
        return p

    # The whole hypothesis. A book this deep is a standing opinion only if there
    # is enough time left for it to be tested; inside the last few hours the
    # same size is at least as consistent with a maker who is simply about to
    # go one-sided as with one committing to a view. Needs no price_history,
    # so it can act on a market's very first snapshot -- unclimbed_tight's
    # run-up statistic cannot be computed at all until two hours of quotes exist.
    horizon = market.close_time - context.now
    if horizon < timedelta(hours=MIN_HORIZON_HOURS):
        return p

    return min(p + SHIFT, 1.0 - EPS)
