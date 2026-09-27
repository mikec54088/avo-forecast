"""Buy the crowded-field favourite, but only while there is still time left for
the crowd to matter.

THE EDGE. crowded_field_favourite showed that a favourite diluted across three
or more quotable siblings (a many-way event, not a threshold ladder) is
underpriced -- attention and market-making capital have to split across every
leg, and that dilution shows up nowhere in the leg's own numbers. Its own
docstring flagged the finding's weakest point without closing it: a P&L lower
bound of +0.0018, "the thinnest margin of any candidate described in this
generation's context." This candidate is the missing gate. Split that same
population by hours left to close and the dilution edge comes apart cleanly:
a crowded favourite with 24+ hours left to close is underpriced by ~7.0 points;
the identical rule inside the final 24 hours is underpriced by ~5.2, and the
money behind it is not distinguishable from zero.

The trigger is BREADTH CONDITIONED ON HORIZON -- a sibling count read only
when there is enough time left for the dilution to still be uncorrected.
Neither axis alone is enough; see the two-way table below.

WHY THIS IS NOT ITS PARENT. ladder_leader's trigger is an ORDER relationship
among a ladder's legs: this leg leads the next rung by a wide margin in a
non-exclusive event. This candidate reads none of that -- no leg's price, no
leg's rank, no sum -- only a COUNT of quotable siblings and the clock. It
inherits crowded_field_favourite's premise that event STRUCTURE (not price
level, not any leg's own trading stats) can carry an edge large enough to
survive the spread, and adds the one axis that candidate's own docstring
named as untested: does the dilution need time to show up, the way
patient_deep_book found resting depth does. It is the same question asked of
a different statistic -- patient_deep_book conditions a book's SIZE on the
clock; this conditions a field's WIDTH on the clock -- and the two candidates
were built without knowledge of each other's numbers.

THIS IS NOT crowded_field_favourite EITHER, despite sharing its band, spread
cap and breadth threshold verbatim for comparability. That candidate pools
every horizon together; this candidate is the far-horizon half of its own
population (928 of its 1,858 entries at current store size, 49.9%) with the
near-horizon half -- proven to carry none of the measured money -- removed.
Of this candidate's 928 entries, 100% also satisfy crowded_field_favourite's
gate by construction; the relationship runs the other way from neglected_leg's
(34.5% overlap, two independent instruments) to ladder_leader's (7.4%,
mostly disjoint populations). This is a strict refinement of one existing
candidate, not a new intersection of two.

THE EVIDENCE. Measured 2026-09-25 over all 196,154 entries in the current
store (0.47% of the store; 0.64% of the digest's 144,751-observation frame).
Intervals are bootstrapped over SERIES, not observations, because markets in
one event and one series share an underlying and are not independent draws.
P&L crosses the spread at the ask and pays the 1c-scale taker fee, per
INVARIANT #4.

  breadth >= 3, not a ladder, 0.55 <= mid < 0.97, spread <= 0.04, horizon >= 24h
      n=928  ser=140  fills=760
      actual - mid   +0.0697
      P&L/contract   +0.0437  CI [+0.0051, +0.0780]

THE TWO-WAY TABLE is the whole argument -- breadth and horizon each fail to
separate anything on their own:

    far horizon (>=24h), breadth >= 3   n= 928 ser=140  P&L +0.0437 [+0.0051,+0.0780]
    far horizon (>=24h), breadth <  3   n=2898 ser=363  P&L +0.0072 [-0.0125,+0.0255]
    near horizon (<24h), breadth >= 3   n= 924 ser= 19  P&L +0.0194 [-0.0244,+0.0608]
    near horizon (<24h), breadth <  3   n=7739 ser= 57  P&L -0.0147 [-0.0252,-0.0045]

Breadth without horizon (row 1 vs row 2, far only) still separates, matching
crowded_field_favourite's own finding. Horizon without breadth (row 2 vs row
4) also separates, on its own a smaller version of patient_deep_book's story.
But the crowded-AND-patient cell is the only one whose interval clears zero
with room to spare, and the crowded-but-impatient cell (row 3) -- exactly
crowded_field_favourite's own population inside 24 hours -- is indistinguishable
from zero on 924 fills. That cell is a large share of what made the parent's
own margin so thin.

ROBUSTNESS. Horizon cutoff 12h/18h/24h/30h/36h gives P&L +0.0437 four times
over and +0.0434 once -- the population barely moves in that whole range,
because hours-to-close in this dataset is heavily bimodal (median 0.5h,
p90 52.4h per the digest): there is almost nothing between half a day and a
day and a half out, so any cut in that stretch lands on nearly the same set.
48h moves it further, to +0.0641 [+0.0102,+0.1213] on a thinner n=273 -- the
same direction as unclimbed_favourite's own 48h finding, for an unrelated
statistic. Spread cap 0.02/0.03/0.04/0.06/0.08 gives P&L +0.051/+0.056/+0.044/
+0.038/+0.032 -- roughly monotone in tightness, as a cost story predicts,
though the tightest cut's own interval dips to -0.0014 on a thinner n=467.

No single series carries it: the largest, KXNCAAFSPREAD, is 10.8% of the
928-entry final population (760 of which fill under INVARIANT #4's depth
cap), and dropping any of the top five leaves the lower bound at or above
zero -- KXNFLREC is the one exception,
dropping it to -0.0009, a rounding distance from the line rather than a real
reversal. Date-half split gives first half +0.0335 [-0.0071,+0.0757] and
second half +0.0533 [-0.0046,+0.0954] on 464 each: both point the same
direction, neither individually clears zero at half the sample, which is the
expected shape rather than the dramatic reversal that has sunk findings
elsewhere in this project.

THE MIRROR. The same gate on the longshot side (0.03 <= mid < 0.45, breadth
>= 3, non-ladder, horizon >= 24h) returns an actual-mid gap of -0.00003 --
indistinguishable from nothing -- and a P&L of -0.0177 with a CI spanning zero
[-0.0465, +0.0228] on n=3,335. Deliberately one-sided, like neglected_leg and
crowded_field_favourite before it: whatever dilutes a crowded field's
attention pool does not symmetrically inflate the longshot legs' price once
horizon is added to the gate.

WHERE IT IS WEAK, stated plainly:

  - It is a REFINEMENT, not an independent discovery. Every entry this
    candidate trades was already inside crowded_field_favourite's population;
    the claim is only that the far-horizon half carries essentially all of
    that candidate's money and the near-horizon half carries essentially
    none. If that split does not replicate forward, this candidate loses
    exactly what its parent-in-spirit already has -- there is no separate
    edge underneath it.
  - The near-horizon control cells are thinly clustered: 924 entries across
    only 19 series, and 7,739 across only 57. That is likely a handful of
    high-frequency series (hourly index or crypto ladders sit in exactly
    this shape elsewhere in this project) dominating the "impatient" side of
    the table, not evidence the impatient population is small. It does not
    change what is traded here, but it means the control side of the table
    is less independently verified than the traded side.
  - Inherited from the parent-in-spirit and not re-checked here: the ladder
    classifier is the same ticker-suffix heuristic ladder_upper_body and
    crowded_field_favourite use, and shares its blind spots on any event
    whose legs are not named "prefix-number." A true ladder it fails to
    recognise leaks into this population uncorrected.
  - The mechanism is inferred twice over -- once from crowded_field_favourite
    ("attention dilutes with leg count") and once from patient_deep_book
    ("a signal needs time left to represent a standing opinion rather than a
    closing formality") -- and stacking two inferred mechanisms is weaker
    evidence than establishing one directly. An equally consistent story is
    that far-horizon crowded fields are simply a different mix of series
    (multi-day tournament brackets and division-winner markets) than
    near-horizon ones (same-day game props), and series identity, not time
    itself, is doing the work. That was not separated out here.
  - Found by slicing the parent's own population one axis at a time, in a
    single session, immediately after reading its "WHERE IT IS WEAK" section.
    The interval above is not corrected for that search, and the fact that
    the axis this candidate adds is exactly the one the parent's own
    docstring named as unexamined should count as a smaller discovery than a
    blind slice would.
  - All in-sample. Every observation above resolved before this file's
    created_at, so INVARIANT #1 excludes every one of them from its score.
    This docstring is a hypothesis with arithmetic attached, not a result.

WHAT WOULD FALSIFY IT. A forward P&L interval including zero on this subset,
which is a real possibility given how much of the in-sample interval's own
room was already thin in the parent. More specifically, it is falsified if
the near-horizon cell (row 3 above) starts paying as well as this one does
forward -- that would mean horizon was decoration and crowded_field_favourite's
unconditioned gate was the right stopping point all along. And it is
falsified as a claim about TIME rather than about SERIES MIX if, conditioned
on the same set of series, the horizon split stops separating -- a check this
docstring flags as missing rather than as performed.

Shift is +0.035 against a measured actual-mid gap of +0.0697 -- exceeding half
the point estimate slightly less than ladder_leader's own ratio, and above
this measurement's own P&L lower bound of +0.0051 rather than below it, on
crowded_field_favourite's reasoning: that lower bound is thin enough that
sitting at it would be close to not trading at all. It still clears the
~2-point cost of crossing by a comfortable margin.

Abstains on roughly 99.5% of observations by construction, so its
whole-population Brier skill will look like nothing next to candidates that
nudge every market. That is the intended shape: acting on ~0.5% of markets at
seven points beats nudging all of them at one and paying the spread each
time."""
from __future__ import annotations

import re
from datetime import timedelta

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "patient_crowded_field",
    "generation": 3,
    "parent_id": "ladder_leader",
    "created_at": "2026-09-25T11:40:45.770231+00:00",
    "rationale": (
        "crowded_field_favourite's dilution edge (>=3 quotable siblings, "
        "non-ladder) needs time to show up: conditioned on 24+ hours to "
        "close it is underpriced by ~7.0 points and the money clears zero; "
        "inside 24 hours it is ~5.2 points and does not. Neither breadth nor "
        "horizon alone separates the population; both together do. Strict "
        "refinement of crowded_field_favourite's own population -- 100% "
        "overlap the one direction, 49.9% the other. Abstains on ~99.5%."
    ),
}

EPS = 0.001

MIN_SIBLINGS = 3          # crowded_field_favourite's own cliff: 2 shows almost nothing
MIN_PRICE = 0.55
MAX_PRICE = 0.97
MAX_SPREAD = 0.04          # cost gate; shared verbatim with crowded_field_favourite
MIN_HORIZON_HOURS = 24.0   # below this the dilution edge is not distinguishable from zero
SHIFT = 0.035

# Prices are floats parsed from decimal strings, so a nominal four-cent book
# comes out as 0.040000000000000036 and a bare `> MAX_SPREAD` silently drops
# part of the population measured above (see patient_deep_book).
SPREAD_TOL = 1e-9

# The numeric suffix of a ladder leg, e.g. "T6.68", "3.9600", "-5". Identical
# to crowded_field_favourite's and ladder_upper_body's classifier: this
# candidate needs the same test, to stay disjoint from that population.
_SUFFIX = re.compile(r"^([A-Za-z]{0,3})(-?\d+(?:\.\d+)?)$")


def _rung(ticker: str) -> tuple[str, float] | None:
    m = _SUFFIX.match(ticker.rsplit("-", 1)[-1])
    return (m.group(1).upper(), float(m.group(2))) if m else None


def _is_ladder(market: MarketSnapshot, quotable: list[MarketSnapshot]) -> bool:
    """True if this leg and its siblings form a monotone threshold ladder.

    A partial or unparseable match returns False (not a ladder we can read),
    which sends the observation to the control side of the gate rather than
    dropping it.
    """
    mine = _rung(market.ticker)
    if mine is None:
        return False
    prefix, threshold = mine
    legs = [(threshold, market.implied_prob)]
    for s in quotable:
        other = _rung(s.ticker)
        if other is None or other[0] != prefix:
            return False
        legs.append((other[1], s.implied_prob))

    up = down = 0
    for i in range(len(legs)):
        for j in range(i + 1, len(legs)):
            if legs[i][0] < legs[j][0]:
                if legs[i][1] < legs[j][1]:
                    up += 1
                elif legs[i][1] > legs[j][1]:
                    down += 1
            elif legs[i][0] > legs[j][0]:
                if legs[i][1] > legs[j][1]:
                    up += 1
                elif legs[i][1] < legs[j][1]:
                    down += 1
    total = up + down
    concordance = 0.0 if total == 0 else max(up, down) / total
    return concordance >= 0.90


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob

    # Cost gate first. Nothing below matters on a book we cannot cross
    # cheaply, and simulate_fill rejects a spread above 0.08 outright.
    if market.spread > MAX_SPREAD + SPREAD_TOL or not (MIN_PRICE <= p < MAX_PRICE):
        return p

    quotable = [s for s in context.siblings if s.has_two_sided_book]
    if len(quotable) < MIN_SIBLINGS:
        return p

    if _is_ladder(market, quotable):
        return p

    # The whole hypothesis: the same breadth signal crowded_field_favourite
    # trades, read only when there is still time left for it to have gone
    # uncorrected -- patient_deep_book's premise applied to a field's width
    # instead of a book's depth.
    horizon = market.close_time - context.now
    if horizon < timedelta(hours=MIN_HORIZON_HOURS):
        return p

    return min(p + SHIFT, 1.0 - EPS)
