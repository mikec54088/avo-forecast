"""Buy the favourite in a market that is still far from its own close.

THE EDGE. Every candidate that has touched a favourite so far reads something
about the market's OWN trajectory -- its price path (unclimbed_favourite), its
volume relative to siblings (neglected_leg), its raw price level
(favourite_longshot). None of them read the one field that requires no history
at all and is known the instant a market is first quoted: how much CALENDAR
TIME stands between now and its scheduled close. A favourite priced 0.50-0.95
that is more than 48 hours from close is underpriced by ~6.3 points; the
governing constraint in this project is that crossing the spread plus fees
costs about 2, so this clears it by a comfortable margin using an input three
different candidate families have left completely alone.

The trigger is HORIZON, not price level, not path shape, and not volume. A
market can trade this signal on its very first snapshot, before it has any
`price_history` and before it has traded enough to have a volume-share reading
against its siblings -- the two things every history-gated favourite candidate
in this project requires before it can act at all.

THE READING. A season-long futures market or a multi-day sports total is
usually quoted first from a generic, un-converged prior -- whatever number the
market or its maker opened with -- and nothing FORCES that number toward the
truth until trading activity accumulates. Close to expiry, days of accumulated
trading have had time to correct it. Far from expiry, the quote is closer to
its opening guess, and the favourite-longshot bias (favourites underpriced,
longshots overpriced, present at every price band in the shared digest) has
had the least time to be arbitraged out.

THE EVIDENCE. Measured 2026-09-27 over the current entries store (206,084
resolved observations with a two-sided book at the time of measurement, up
from the shared digest's 144,751 -- the store grows continuously). Horizon is
`market.close_time - market.observed_at` in hours, exactly `context.now` at
scoring time (`observations.py` stamps `now=entry.market.observed_at`).
Intervals are bootstrapped over SERIES, not observations, because markets in
one series and one day share an underlying and are not independent draws.
P&L crosses the spread at the ask and pays the taker fee, per INVARIANT #4.

  horizon > 48h, 0.50 <= mid < 0.95, spread <= 0.04
      n=2,690  ser=372  fills=2,545   (1.3% of the store)
      actual - mid   +0.0625  CI [+0.0401, +0.0834]
      P&L/contract   +0.0420  CI [+0.0191, +0.0632]

Both intervals clear zero on the pooled set, and by price band:

    0.50-0.65   n= 761  ser=252  P&L +0.0353  CI [-0.0028, +0.0690]
    0.65-0.80   n= 832  ser=250  P&L +0.0637  CI [+0.0266, +0.0933]
    0.80-0.95   n=1097  ser=252  P&L +0.0300  CI [+0.0065, +0.0520]

The 0.50-0.65 band is the weakest and the only one whose interval touches
zero; the other two clear it comfortably. 372 series contribute to the pooled
set -- more series than any other candidate described in this generation's
context -- which is the direct consequence of trading a field every market has
rather than one only some markets accumulate.

WHY THIS IS NOT unclimbed_favourite OR unclimbed_far. Those candidates' own
mechanism audit found the same interaction from the opposite direction --
inside THEIR gate (run-up < 0.10), horizon beyond 48h roughly tripled the edge
(+0.1032 against +0.0307) -- and unclimbed_far was written to trade exactly
that intersection. But both require a run-up gate on top: `price_history` with
a span over two hours, and the current price sitting close to the path's own
minimum. Splitting THIS candidate's own population by that same run-up
statistic:

    run-up < 0.10   (unclimbed_far's population)   n= 883  P&L (see below)
    run-up >= 0.10  (unclimbed_far excludes this)   n=1,767
    no price_history at all                         n=   40

Two thirds of this candidate's trades are the run-up >= 0.10 population that
unclimbed_far cannot see at all, restricted to the 0.65-0.80 band where the
split is cleanest:

    horizon > 48h, 0.65-0.80: run-up < 0.10   n=235  P&L +0.1214
    horizon > 48h, 0.65-0.80: run-up >= 0.10  n=584  P&L +0.0421
    horizon > 48h, 0.65-0.80: no price_history n= 13  P&L -0.0690 (n too small to read)

The run-up < 0.10 slice pays far better, which says unclimbed_far is reading
the stronger half of this same interaction and reading it correctly. The claim
here is only that the other half -- a favourite that DID climb, or has climbed
gradually across a multi-day market -- still clears the cost of crossing on
its own, at a population more than twice the size of the one unclimbed_far
already trades. The near-empty no-history slice says this candidate is not,
in practice, mostly finding brand-new listings; it is finding markets that
have traded for a while but are still calendar-distant from resolution.

ROBUSTNESS. Horizon itself is a step rather than a gradient, and the population
above 48h is a small, distant tail of the store -- the shared digest's own
hours-to-close p90 is 52.4h, so this candidate trades close to the edge of
where any data exists at all:

    horizon >= 24h   n=6,458  P&L +0.0305
    horizon >= 36h   n=6,437  P&L +0.0303
    horizon >= 48h   n=2,690  P&L +0.0420   <- shipped
    horizon >= 72h   n=    8  (population effectively exhausted)

24h and 36h land on nearly the same population (6,458 vs 6,437 -- the
distribution is sparse in between) at a lower P&L than 48h, so the cut is not
finely tunable; 48h is close to the last point with enough population left to
trust. Spread cap decays the way a cost story predicts: 0.02/0.03/0.04/0.06/0.08
gives P&L +0.047/+0.043/+0.042/+0.038/+0.036.

Not concentrated in one series or one sport. The five largest contributors are
MLB run-total and spread markets (KXMLBTOTAL 360, KXMLBSPREAD 204, and three
smaller MLB families), 30.2% of the population combined, but excluding the
whole KXMLB family entirely leaves n=1,877 across 361 OTHER series --
football and cricket totals across MLS, Serie A/C, J-League, the Bundesliga,
La Liga, T20 cricket, the EFL Championship and more -- at P&L +0.0489, if
anything stronger than the MLB slice's +0.0263. Excluding any single one of
the top five MLB series individually leaves P&L between +0.0338 and +0.0460.
This is a claim about multi-day markets generally, not about one sport's
quoting convention.

STABILITY. Splitting by resolution date, the first half gives P&L +0.0572 and
the second +0.0270 -- both positive, sign intact, but the pooled number is
carried disproportionately by the first half. This is the honest read, not a
reassuring one: unlike unclimbed_favourite (whose two halves were +0.0812 and
+0.0958), this one decays. Read the pooled +0.0420 as an average of a stronger
early period and a weaker late one, not as a stable rate.

THE MIRROR FAILS, which argues the effect is about favourites specifically and
not a general property of far-horizon quotes. Applying the identical gate
(horizon > 48h, spread <= 0.04) to the NO side of longshots:

    mid 0.05-0.20   NO-side P&L +0.0028  (essentially flat)
    mid 0.20-0.35   NO-side P&L -0.0410
    mid 0.35-0.50   NO-side P&L -0.0815

Buying NO on a far-horizon longshot loses money, and loses more the closer the
price sits to 0.50. Whatever un-converged-prior story explains the favourite
side, it does not survive being reflected onto the other one.

WHERE IT IS WEAK, stated plainly:

  - THE SECOND HALF IS MATERIALLY WEAKER. +0.0270 against +0.0572 is not the
    dramatic first-half collapse that sank an earlier finding in this project,
    but it is a real decay, not noise around a flat line, and it is the
    single biggest reason to discount the pooled number.
  - The 0.50-0.65 band's interval touches zero. It is shipped because the
    other two bands clear it and a band-by-band gate would just be fitting a
    third threshold to two already-swept ones, but it is the part most likely
    to disappoint forward.
  - The mechanism is a story, not a demonstration. "The quote has not
    converged yet" predicts that horizon should matter continuously, but the
    only sweep available (24h/36h/48h/72h) is too coarse and too close to the
    edge of the data to show a gradient the way unclimbed_favourite's run-up
    sweep did. It is entirely possible this is a step because of how these
    multi-day markets happen to be quoted on day one specifically, not a
    smooth function of time remaining.
  - It is a large-scale RESTATEMENT of the same favourite-longshot bias every
    price-gated candidate already trades, with a calendar filter in front.
    That is exactly neglected_leg's and crowded_field_favourite's shape too --
    an ordinary bias, filtered onto the slice where it is large enough to
    survive the spread -- and it inherits that family's central risk: if the
    forward favourite-longshot bias itself weakens, so does everything built
    on top of it, and three separate filters agreeing about where the bias
    concentrates is not the same as the bias being a different thing three
    times over.
  - Horizon is measured at ENTRY (`market.observed_at`), and the population is
    the small tail of the store where snapshots happen to have caught a market
    that far out. Capture cadence, not market behaviour, decides how many
    far-horizon snapshots exist at all, so this candidate's fill rate is more
    exposed to a capture change than a candidate reading price level alone.
  - Found by extending unclimbed_favourite's own mechanism audit rather than
    by an independent search, so the usual multiple-comparisons caveat is
    weaker here than most -- but the audit that produced the +0.1032/+0.0307
    split was itself found by scanning 44 gates on data that selected a
    different candidate, and that search is not corrected for either.
  - All in-sample. Every observation above resolved before this file's
    created_at, so INVARIANT #1 excludes every one of them from its own
    score. This docstring is a hypothesis with arithmetic attached, not a
    result.

WHAT WOULD FALSIFY IT. A forward P&L interval including zero on the pooled
subset kills it outright. It is falsified more specifically as an INDEPENDENT
finding, rather than a restatement of unclimbed_far, if the run-up >= 0.10
slice stops paying while the run-up < 0.10 slice keeps paying at
unclimbed_far's own rate -- that would mean this candidate is only ever
catching unclimbed_far's population late and adding a weaker, unnecessary
tail. And it is falsified as a genuine horizon effect, rather than a MLB- or
soccer-total-shaped coincidence, if excluding the ten largest contributing
series together turns the sign negative, which the individual leave-one-out
checks above do not rule out.

Shift is +0.03 against a measured actual-mid gap of +0.0625, below the low end
of that interval (+0.0401), on the same reasoning as every shift in this
project: carrying a fitted value into a forward test is how in-sample fitting
sneaks back in. It clears the ~2-point cost of crossing, though by a narrower
margin than most candidates here -- appropriate given the second-half decay
above.

Abstains on roughly 98.7% of observations by construction. Its whole-population
Brier skill will look like nothing next to candidates that nudge every market;
that is the intended shape, and at 1.3% of the store this is one of the
higher-volume abstaining candidates in the set rather than one of the
thinnest.

Parent is ladder_leader, which established that event STRUCTURE can carry an
edge large enough to survive the spread where price level alone cannot. This
candidate keeps that lesson -- look for a structural fact about the market
that isn't its price -- but changes the axis entirely, from how an event's
legs relate to each other to how far away its own close is. It reads none of
`context.siblings` and trades a population ladder_leader cannot touch at all:
a single-leg market, with no siblings whatsoever, still has a close time."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "long_horizon_favourite",
    "generation": 4,
    "parent_id": "ladder_leader",
    "created_at": "2026-09-27T11:57:44.941442+00:00",
    "rationale": (
        "A favourite priced 0.50-0.95 that is more than 48 hours from its own "
        "close is underpriced by ~6.3 points, against the ~2-point cost of "
        "crossing. Trigger is calendar distance to close, not price path or "
        "volume -- the only input here every market has from its first "
        "snapshot. Two thirds of its trades are favourites that already "
        "climbed, a population unclimbed_far explicitly excludes. Second-half "
        "P&L is materially weaker than the first. Abstains on ~98.7%."
    ),
}

EPS = 0.001

MIN_PRICE = 0.50           # below this the mirror check shows the effect fails
MAX_PRICE = 0.95           # above this there is little room left to be underpriced by
MAX_SPREAD = 0.04          # cost gate; P&L decays monotonically as the book widens
MIN_HOURS_TO_CLOSE = 48.0  # near the top decile of horizon in the whole store
SHIFT = 0.03

# Prices and the spread are floats parsed from decimal strings, so a nominal
# four-cent book can come out as 0.040000000000000036 -- see unclimbed_favourite
# and unclimbed_far, which both hit this and lost observations to a bare `>`.
SPREAD_TOL = 1e-9


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob

    # Cost gate first. Nothing below matters on a book we cannot cross
    # cheaply, and simulate_fill rejects a spread above 0.08 outright.
    if market.spread > MAX_SPREAD + SPREAD_TOL or not (MIN_PRICE <= p < MAX_PRICE):
        return p

    # The whole hypothesis: how much calendar time stands between now and
    # this market's own scheduled close. Requires no price history and no
    # sibling structure, unlike every other favourite candidate in this file's
    # lineage.
    hours_to_close = (market.close_time - context.now).total_seconds() / 3600.0
    if hours_to_close < MIN_HOURS_TO_CLOSE:
        return p

    return min(p + SHIFT, 1.0 - EPS)
