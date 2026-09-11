"""Back favourites in series the market has barely seen resolve yet.

THE EDGE. `series_history` is usually read for its CONTENT -- the outcomes
themselves, used as a base rate. That has failed twice and once catastrophically
(baseline_base_rate: skill -0.5345). This reads it for its LENGTH instead: how
many resolutions in this market's own series were already known at entry,
independent of what they were. A favourite in a series with under 30 known
prior resolutions is underpriced by ~6.3 points; the same band in a series the
market has resolved 30+ times before is underpriced by ~3.0, which barely
covers the ~2-point cost of crossing and whose interval touches zero.

The reading: an automated market maker calibrates a series against its own
realised outcomes. Early in a series' life there is little to calibrate
against, so its favourite-longshot bias runs close to the unconditional one;
by the time a series has resolved thirty-plus times the maker has had thirty-
plus chances to see where its shoulder priced wrong and narrow it. The trigger
is experience WITH THIS SPECIFIC SERIES, not with the market generally.

WHY THIS IS NOT ITS PARENT. stable_favourite reads price_history and asks
whether THIS market's own quote has been sitting still -- a within-market,
temporal signal that requires the market itself to have traded for a while.
This reads series_history and asks how many OTHER markets in the same series
have already resolved -- a cross-market, structural signal that says nothing
about this market's own path and requires no history on it at all. A market
five minutes old inherits its series' maturity immediately; it can never
inherit its own price stability. The two gates fire on almost disjoint
populations for that reason: of the entries below, 62% have fewer than four
price_history points, which is below stable_favourite's own minimum to act.

THE EVIDENCE. Measured 2026-09-11 over all 104,189 resolved observations in
759 series, captured 2026-08-24 through 2026-09-11 (18 days). "Known
resolutions" is `len(context.series_history[market.series_ticker])`, the count
of that series' own past outcomes visible strictly before `now` -- exactly
what INVARIANT #1 already restricts a candidate to seeing. Intervals are
bootstrapped over SERIES, not observations. P&L crosses the spread at the ask
and pays the 1c fee, per INVARIANT #4.

  known < 30, 0.65 <= mid < 0.95, spread <= 0.04     n=1,155  ser=356  fills=1,045
      actual - mid   +0.0628  CI [+0.0413, +0.0835]
      P&L/contract   +0.0404  CI [+0.0184, +0.0624]

The control is the same rule with the gate inverted:

  known >= 30, same band and spread                  n=5,080  ser=150  fills=4,497
      actual - mid   +0.0303  CI [+0.0180, +0.0432]
      P&L/contract   +0.0114  CI [-0.0009, +0.0237]

Read the control honestly: it is not a null, it is the ordinary favourite bias
that nine already-scored candidates have failed to monetise, and its interval
just barely fails to clear zero on this cut. The claim is that the young half
carries about 3.5x as much of it in P&L terms, which is the difference between
an edge the spread eats and one it does not.

THE SAME-SERIES CHECK, because "young series" could just mean "a different
kind of series" rather than "a series caught early in its life." Restricted to
the 110 series that appear in BOTH groups -- i.e. series this capture window
saw both before and after their 30th resolution:

    young obs of a since-matured series    n= 543  ser=110  P&L +0.0458 [+0.0136,+0.0780]
    mature obs of that same series         n=4,566 ser=110  P&L +0.0107 [-0.0025,+0.0239]
    young obs of a series never seen mature n=612  ser=246  P&L +0.0357 [+0.0056,+0.0658]

The same series pays more when young than when mature -- this is not purely a
cross-sectional split by series type. But the never-matured group pays too,
at a similar size, so part of the effect is plausibly ordinary niche/thin
series rather than freshness as such. Both readings point the same direction;
the evidence does not distinguish them cleanly. See WHERE IT IS WEAK.

DOSE-RESPONSE across the threshold, same band and spread:

    known <  10   n=  598  P&L +0.0330 [+0.0014,+0.0645]
    known <  20   n=  918  P&L +0.0339 [+0.0090,+0.0589]
    known <  30   n=1,155  P&L +0.0404 [+0.0184,+0.0624]
    known <  50   n=1,437  P&L +0.0406 [+0.0200,+0.0611]
    known < 100   n=1,875  P&L +0.0379 [+0.0207,+0.0552]

A broad plateau from +0.033 to +0.041 across a 10x range of the cutoff, not a
single fitted cell. 30 sits inside it, near where the fill count first clears
1% of the full observation set.

ROBUSTNESS.

  spread cap 0.02 / 0.03 / 0.04 / 0.06 / 0.08 (known < 30, same band)
      +0.033 / +0.045 / +0.040 / +0.029 / +0.024 -- decaying with width past
      0.03, as a cost story predicts. (0.01 gives n=70, fills=63, too thin to
      read.)
  price band 0.60-0.90 / 0.60-0.95 / 0.65-0.95 / 0.55-0.95 / 0.50-0.95 / 0.65-0.99
      +0.040 / +0.035 / +0.040 / +0.040 / +0.038 / +0.031 -- flatter than most
      shoulder candidates report; it does not collapse as the ceiling extends
      toward 1.0, which is unlike every price-only candidate in this set and
      is itself a reason not to over-trust the ceiling number.
  crypto (KXBTC/KXETH/KXSOL) excluded: n=1,107, P&L +0.0392 [+0.0171,+0.0614]
      against +0.0404 for the full gated set -- this candidate is NOT a crypto
      story the way neglected_leg and deep_book_favourite partly are; only 48
      of 1,155 gated observations are crypto series.
  selection series   n=822  P&L +0.0425 [+0.0172,+0.0677]
  confirmation series n=333  P&L +0.0354 [-0.0086,+0.0795]
      Same sign and similar size, but confirmation's interval touches zero --
      n=333 across 104 series is thin for a series-clustered estimate.
  resolution date, first half / second half
      +0.0466 [+0.0159,+0.0774]  /  +0.0345 [+0.0043,+0.0647] -- both clear
      zero, unlike neglected_leg where the whole result lived in one half.
  entry staleness, <=30min / >30min
      +0.0543 [+0.0230,+0.0856]  /  +0.0329 [+0.0036,+0.0623] -- pays MORE on
      the fresher entries, not less. If this were the documented staleness
      artifact it should run the other way; it does not.

WHAT IT ADDS. It needs no price history on the market itself and no siblings
at all -- only a count of the series' own past resolutions, which is nonzero
(indeed at its smallest, hence most triggering) for a market in its first
hour of quotation. It reaches markets that stable_favourite, neglected_leg and
every behavioural-gate candidate must abstain on for having no path or no
quoted siblings yet. It is also the only candidate reading series_history for
something other than the outcome frequency itself.

WHERE IT IS WEAK, stated plainly.

  - CAPTURE-WINDOW CONFOUND. This is the honest caveat and it is severe:
    `series_history` only counts resolutions THIS capture has itself observed,
    and capture is 18 days old. A series that has run on Kalshi for two years
    but entered our capture window nine days ago looks exactly as "young" here
    as one that genuinely launched nine days ago. What this candidate measures
    is provably "resolutions we have seen," not "resolutions that have
    happened" -- and I cannot tell those apart from inside this dataset. That
    means the effect could easily be an artifact of which series capture
    happened to sweep up late, rather than of series maturity at all.
  - It follows that the population should SHRINK over calendar time even if
    the underlying rule never changes: every day capture runs, fewer series
    are still under thirty observed resolutions, purely from our own history
    accumulating. A candidate whose action rate decays for reasons unrelated
    to the market is a bad sign to see in three months and an expected one to
    see in three weeks; only the former falsifies the hypothesis.
  - The same-series check does not cleanly separate "matures with experience"
    from "different kind of series" -- both readings are consistent with what
    was measured, see above.
  - The confirmation-series interval touches zero. Read the selection-series
    result as the more optimistic of the two honest estimates, not as the
    settled one.
  - The mechanism is inferred, not shown, same as every other candidate in
    this set. "The maker recalibrates against realised outcomes" fits: so does
    "series that are new tend to be one-off / lower-liquidity / less
    professionally quoted events regardless of experience," which is a venue-
    quality story rather than a learning one and predicts the same shape.
  - Found by scanning: I looked at series_history's binary content first
    (mean recent outcome, recency-weighted base rate) and both separated
    nothing once the ordinary favourite bias was accounted for, which is
    consistent with baseline_base_rate and series_base_rate_blend already
    having failed on that axis. The count, not the content, is what carries
    anything here. The intervals above are not corrected for that search.
  - All in-sample. Every observation above resolved before this file's
    created_at, so INVARIANT #1 excludes all of them from its score. This
    docstring is a hypothesis with arithmetic attached, not a result.

WHAT WOULD FALSIFY IT. A forward P&L interval including zero on this subset.
More specifically: if the action rate collapses toward zero over the next few
weeks while the edge holds, that is the capture-window confound resolving
itself and is not a failure of the idea, merely of this particular cutoff. But
if the edge itself fades as capture accumulates more history on the same
series -- i.e. if six months from now the known<30 population still exists
(new series keep launching) but no longer pays -- that is the calibration
story failing outright, since genuinely new series should keep mispricing the
same way regardless of how much history capture itself has built up. And it
is falsified as anything but a venue-quality artifact if it stops separating
inside the confirmation series specifically, where it is already the weaker
half of a barely-adequate sample.

DELIBERATELY ONE-SIDED. The mirror does not survive contact with the data,
and not merely by falling short -- it points the wrong way. Longshots
(0.05-0.35) in young series show almost no mispricing (mid - actual +0.0049)
and buying NO on them LOSES money, -0.0211 [-0.0360,-0.0062], a proven loser
on its own terms. Mature-series longshots are closer to flat, +0.0032
[-0.0069,+0.0133]. Whatever series immaturity is doing to the high shoulder,
it is not doing the mirrored thing to the low one -- which is mild evidence
against a simple "everything about this series is less calibrated" story, and
for something specific to how a maker's favourite pricing narrows with
repetition. That tail is abstained on rather than traded small.

Shift is +0.03 against a measured actual-mid of +0.0628, below the low end of
its interval (+0.0413) and well under the point estimate, on the same
reasoning as every shift constant in this set: carrying a fitted value into a
forward test is how in-sample fitting sneaks back in. It still clears the
~2-point cost of crossing with room to spare against the low end.

Abstains on 98.9% of observations (1,155 of 104,189), so its whole-population
Brier skill will look like nothing beside candidates that nudge every market.
Acting on 1.1% of markets at four points beats nudging all of them at one and
paying the spread each time -- and 1,155 in-sample fills over 18 days projects
to well over 200 forward fills inside the measurement window this experiment
requires, before any capture-window shrinkage in WHERE IT IS WEAK takes hold.
"""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "young_series_favourite",
    "generation": 3,
    "parent_id": "stable_favourite",
    "created_at": "2026-09-11T14:09:21.958545+00:00",
    "rationale": (
        "Read series_history for its LENGTH, not its content. A favourite in "
        "a series with under 30 known prior resolutions is underpriced by "
        "~6.3 points; the same band once the series has resolved 30+ times "
        "is underpriced by ~3.0, which barely covers the cost of crossing. "
        "Needs no price history and no siblings, so it reaches markets too "
        "young for every history- or sibling-gated candidate. Holds within "
        "the same series before/after its 30th resolution, in both time "
        "halves and on both staleness sides. Confounded with how long "
        "capture itself has been running -- flagged, not resolved. Abstains "
        "on 98.9%."
    ),
}

EPS = 0.001

MIN_PRICE = 0.65        # below this the favourite bias does not cover the spread
MAX_PRICE = 0.95        # above this there is no room left to be underpriced by
MAX_SPREAD = 0.04       # cost gate; the edge decays as the book widens
MAX_KNOWN_RESOLUTIONS = 30  # series with fewer known prior outcomes than this
SHIFT = 0.03

# Prices are floats parsed from decimal strings, so a nominal four-cent book
# can come out as 0.040000000000000036 and a bare `> MAX_SPREAD` would silently
# drop part of the population measured above.
SPREAD_TOL = 1e-9


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob

    # Cost gate first. Nothing below matters on a book we cannot cross cheaply,
    # and simulate_fill rejects a spread above 0.08 outright.
    if market.spread > MAX_SPREAD + SPREAD_TOL or not (MIN_PRICE <= p < MAX_PRICE):
        return p

    # The whole hypothesis: how many of this market's own SERIES-mates had
    # already resolved by `now`, not what any of them resolved to. A market
    # with zero known siblings-in-time is not a missing measurement here the
    # way it would be for price_history or context.siblings -- zero is itself
    # the most triggering value, exactly the population this is built to reach.
    known = context.series_history.get(market.series_ticker, [])
    if len(known) >= MAX_KNOWN_RESOLUTIONS:
        return p

    return min(p + SHIFT, 1.0 - EPS)
