"""Buy the favourite in a many-way event, purely because it has many rivals.

THE EDGE. Kalshi events range from a single yes/no question to a many-way
split -- a division-winner market with a dozen teams, a weekly top-scorer
board, a categorical outcome with several buckets. Attention and market-making
capital devoted to any ONE leg has to be shared across however many legs the
event has, and that dilution does not show up in the leg's own numbers: a
diluted leg's volume, depth and spread can look completely ordinary in
isolation, because they are ordinary FOR A MARKET, just not ordinary for one
competing against nine siblings for the same finite pool of attention. Every
candidate that reads a leg's own trading stats (volume_weighted, neglected_leg,
deep_book_favourite, book_imbalance_tilt, open_interest_shoulders) is reading
the wrong object to see this: the instrument here is not anything about the
leg, only a COUNT of how many quotable siblings it has.

A favourite with at least three quotable siblings, in an event that is not a
threshold ladder, is underpriced by ~5.9 points. The same band with fewer
siblings, or with three-plus arranged as a monotone ladder, is underpriced by
~3.1 -- close to the ordinary favourite-longshot bias nine already-scored
candidates have failed to turn into money.

WHY THIS IS NOT ITS PARENT. ladder_leader's trigger is an ORDER relationship:
a non-exclusive event (legs overlap, sum well above 1.0) where this leg leads
the next rung by a wide margin. This candidate explicitly EXCLUDES that
population -- see `_is_ladder`, the same ticker-suffix concordance test
ladder_upper_body uses to classify a ladder -- and reads nothing about any
leg's price or price order, only how many siblings exist. Of the 1,748
entries this candidate trades, only 7.4% also trigger ladder_leader's gate,
and by construction it cannot overlap ladder_upper_body at all: that candidate
requires the concordance test to PASS, this one requires it to FAIL.

WHY THIS IS NOT neglected_leg. That candidate's trigger is this leg's share of
the event's own VOLUME, a ratio computed from every sibling's trading numbers.
This candidate's trigger is a bare count of siblings with a two-sided book --
it needs to know that a sibling is quotable, never how much it has traded,
which makes it immune to a stale volume reading on any one sibling in a way a
ratio is not. The two overlap on 34.5% of this candidate's population: real,
but a minority, and the two axes disagree exactly where the story predicts --
a leg can be one of only three siblings and still hold a normal volume share,
or hold a low share among twenty and be nothing special among them.

THE EVIDENCE. Measured 2026-09-23 over all 184,610 entries in the current
store. Intervals are bootstrapped over SERIES, not observations, because
markets in one event and one series share an underlying and are not
independent draws. P&L crosses the spread at the ask and pays the 1c-scale
taker fee, per INVARIANT #4.

  breadth >= 3 quotable siblings, not a ladder, 0.55 <= mid < 0.97, spread <= 0.04
      n=1,748  ser=157  fills=1,337   (0.95% of the store; 1.2% of the
                                        digest's 144,751-observation frame)
      actual - mid   +0.0591
      P&L/contract   +0.0301  CI [+0.0018, +0.0584]
      skill          +0.0236  CI [+0.0110, +0.0373]

THE CONTROL is the same band and spread with the gate inverted -- fewer than
three quotable siblings, OR three-plus arranged as a ladder:

  n=18,085  ser=536  fills=15,757
      actual - mid   +0.0309
      P&L/contract   +0.0097  CI [+0.0003, +0.0191]
      skill          +0.0072  CI [+0.0034, +0.0119]

Read that control honestly: its interval is positive too, barely. It is the
ordinary favourite bias nine candidates have already failed to monetise, and
the claim here is only that a crowded, non-ladder field carries about 3x as
much of it -- the gap between an edge the ~2-point cost of crossing eats
almost whole and one it does not.

ROBUSTNESS. Breadth is a step, not a smooth gradient, and that is worth
stating plainly rather than dressing up: sweeping the minimum sibling count
(same band and spread throughout),

    breadth >= 2   n=3,013  P&L +0.0054 [-0.0145,+0.0253]   CI spans zero
    breadth >= 3   n=1,748  P&L +0.0301 [+0.0018,+0.0584]   <- shipped
    breadth >= 4   n=1,467  P&L +0.0276 [-0.0053,+0.0606]
    breadth >= 5   n=1,249  P&L +0.0238 [-0.0116,+0.0592]
    breadth >= 6   n=1,096  P&L +0.0148 [-0.0231,+0.0527]

there is a real cliff between two and three siblings, not a gradient like
ladder_leader's lead-margin sweep, and it holds roughly flat from three
through five before fading at six as the sample thins. A cliff is weaker
evidence than a gradient: it is at least as consistent with "two populations
mixed together" as with "attention dilutes continuously with leg count."

Spread cap, same band: 0.04/0.06/0.08 gives P&L +0.0301/+0.0176/+0.0111,
monotone in tightness the way a cost story predicts.

Leave-one-series-out on the five largest contributors to the shipped P&L:
dropping KXBTC (the largest single contributor at +22.8% of the pooled P&L)
leaves +0.0244 [-0.0030,+0.0519] on n=1,680 -- the point estimate survives,
though the lower bound dips under zero on the smaller sample. Two of the top
five contributors are NEGATIVE (KXNCAAF1QSPREAD -16.5%, KXDJI -15.3% of the
pooled total), and dropping either of them RAISES the estimate to
+0.0355/+0.0356 with both lower bounds clearing zero. So this is not one
lucky series carrying a flat set: several series pull in both directions and
the aggregate still holds, which is reassuring about breadth, but it also
means the dilution story does not fire cleanly on every crowded field it
should -- something about NCAAF spreads and the Dow close specifically works
against it. No single series among the 157 holds more than 8.9% of the trade
count (KXSOLD, 155 of 1,748).

DATE-HALF STABILITY, because research-track history says to check this before
anything else. Split by resolution date:

    first half   n=874  P&L +0.0233 [-0.0117,+0.0583]  skill +0.0157 [+0.0016,+0.0318]
    second half  n=874  P&L +0.0387 [-0.0058,+0.0831]  skill +0.0343 [+0.0109,+0.0600]

Both halves point the same direction and neither's P&L interval clears zero on
its own, which is expected at half the sample; skill does clear zero in both
halves. This is not the dramatic first-half/second-half split that sank an
earlier finding elsewhere in this project, but the whole-sample P&L interval
above is already the weakest passing margin of any candidate in this set
(+0.0018 at the low end), so there is very little room for the true forward
value to sit anywhere but close to zero.

WHERE IT IS WEAK, stated plainly:

  - The P&L lower bound is +0.0018. That is barely positive, and it is the
    thinnest margin of any candidate described in this generation's context.
    Read the headline number as fragile, not as a discovery.
  - The breadth cliff (weak at 2, strong at 3+) is a step rather than a
    gradient, which is a weaker shape of evidence than the gradients this
    project has generally required before trusting a threshold.
  - The ladder classifier is the identical ticker-string heuristic
    ladder_upper_body uses, and inherits its blind spots: it drops the sign of
    a negative threshold and is invisible on any event whose legs are not
    named "prefix-number". A true ladder that the heuristic fails to
    recognise leaks into this candidate's population uncorrected, since this
    candidate ABSTAINS on what it detects as a ladder but has no way to catch
    what it does not detect.
  - The mechanism is inferred, not shown. "Attention dilutes with leg count"
    fits the numbers, but "many-way events happen to be a specific mix of
    series -- crypto range buckets, bracket-style sports props" is an equally
    good alternative story, and the series list above (KXBTC, KXSOLD, KXETHD,
    several NFL and NCAAF prop families) leans on exactly that mix. Breadth
    may be standing in for series identity rather than causing anything by
    itself.
  - Found by slicing a wider table -- I looked at breadth alone before
    excluding ladders, and at OI/volume shares among siblings first, both of
    which turned out to substantially re-derive neglected_leg or nothing at
    all. The interval above is not corrected for that search.
  - The mirror was not tested. Whether the longshot side of this same gate
    (many siblings, not a ladder, low mid) is overpriced was not measured
    before shipping, for lack of session time. Unlike neglected_leg, whose
    docstring reports a checked and failed mirror, absence of a check here
    should not be read as evidence either way.
  - All of it is in-sample. Every observation above resolved before this
    file's created_at, so INVARIANT #1 excludes every one of them from this
    candidate's own score. This docstring is a hypothesis with arithmetic
    attached, not a result.

WHAT WOULD FALSIFY IT. A forward P&L interval including zero on this subset --
more likely here than for a typical candidate in this project, given how thin
the in-sample lower bound already is. It is falsified more specifically if
breadth 3 and breadth 2 stop being distinguishable forward, which would mean
the cliff above was sampling noise rather than a real threshold. And it is
falsified as a claim about DILUTION rather than about a handful of favoured
series if excluding KXBTC, KXSOLD and KXETHD together turns the sign negative,
which the single-series leave-one-out checks above do not yet rule out.

Shift is +0.03 against a measured actual-mid gap of +0.0591 -- under half of
the point estimate and, unusually for this project, above the low end of the
P&L interval rather than below it, because the low end here is only +0.0018
and shifting to it would be indistinguishable from not trading at all. It
still clears the ~2-point cost of crossing.

Abstains on roughly 99% of observations by construction, so its
whole-population Brier skill will look like nothing next to candidates that
nudge every market. That is the intended shape: acting on ~1% of markets at
six points beats nudging all of them at one and paying the spread each time.

Parent is ladder_leader, which is where this reasoning starts: it showed that
event STRUCTURE, not price level, can carry an edge large enough to survive
the spread. This candidate keeps that premise and changes the structural
signal from "how the legs are ordered" to "how many legs there are"."""
from __future__ import annotations

import re

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "crowded_field_favourite",
    "generation": 3,
    "parent_id": "ladder_leader",
    "created_at": "2026-09-23T11:38:52.108740+00:00",
    "rationale": (
        "A favourite with >=3 quotable siblings in a non-ladder event is "
        "underpriced by ~5.9 points, against ~3.1 for fewer siblings or a "
        "ladder -- attention dilutes with leg count, independent of any "
        "leg's own price or trading stats. Abstains on ~99%."
    ),
}

EPS = 0.001

MIN_SIBLINGS = 3     # a step, not a gradient: 2 siblings shows almost nothing
MIN_PRICE = 0.55
MAX_PRICE = 0.97
MAX_SPREAD = 0.04    # cost gate; the edge is monotone in how tight the book is
SHIFT = 0.03

# The numeric suffix of a ladder leg, e.g. "T6.68", "3.9600", "-5". Identical
# to ladder_upper_body's classifier: this candidate needs the same test, run
# the other way round, to stay disjoint from that candidate's population.
_SUFFIX = re.compile(r"^([A-Za-z]{0,3})(-?\d+(?:\.\d+)?)$")


def _rung(ticker: str) -> tuple[str, float] | None:
    m = _SUFFIX.match(ticker.rsplit("-", 1)[-1])
    return (m.group(1).upper(), float(m.group(2))) if m else None


def _is_ladder(market: MarketSnapshot, quotable: list[MarketSnapshot]) -> bool:
    """True if this leg and its siblings form a monotone threshold ladder.

    A partial or unparseable match returns False (not a ladder we can read),
    which sends the observation to the control side of the gate rather than
    dropping it -- abstaining on a market this candidate cannot classify would
    be a third outcome the gate does not have.
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
    if market.spread > MAX_SPREAD or not (MIN_PRICE <= p < MAX_PRICE):
        return p

    quotable = [s for s in context.siblings if s.has_two_sided_book]
    if len(quotable) < MIN_SIBLINGS:
        return p

    # The whole hypothesis stops here: how many rivals this leg has, nothing
    # about their prices. A monotone ladder is a different population
    # (ladder_leader, ladder_upper_body) and is excluded so the two do not
    # trade the same observations under different names.
    if _is_ladder(market, quotable):
        return p

    return min(p + SHIFT, 1.0 - EPS)
