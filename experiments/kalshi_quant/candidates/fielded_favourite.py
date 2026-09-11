"""Back favourites that belong to an event with a field. Fade nothing about a
market that stands alone.

THE EDGE. The favourite-longshot effect, wherever it has been documented off
this exchange, is a story about CHOICE: bettors picking among a field of
horses, teams, or candidates overvalue the exciting longshot and undervalue
the boring favourite. A standalone yes/no market -- "will it rain tomorrow",
one economic print against one threshold -- offers no such choice. There is
nothing to pick the favourite OVER. If the bias is what the literature says it
is, it should live in markets with a field and be close to absent in markets
without one.

It is. A favourite (0.60 <= mid < 0.95, spread <= 0.04) with at least one
quotable sibling is underpriced by ~4.7 points. The same band with NO quotable
sibling -- a market that is the only leg of its event -- is underpriced by
~1.0 point, indistinguishable from zero and well under the ~2-point cost of
crossing. The trigger is whether an event with a field EXISTS, nothing about
its content: not a volume share, not a margin, not a sum, not which leg leads.

WHY THIS IS NOT ITS PARENT. stable_favourite reads price_history and asks
whether THIS market's own quote has sat still -- a temporal signal, private to
one market's path, that needs the market to have already traded for a while.
This reads context.siblings and asks only whether ANY other leg of the same
event is quotable right now -- a structural, cross-sectional signal, true or
false the instant a market opens, that says nothing about its own price path
and needs no history on it at all. A market five minutes old already knows
whether it has a field; it cannot yet know whether its own price has been
stable.

THE EVIDENCE. Measured 2026-09-11 over all 104,189 resolved observations in
759 series. "Quotable sibling" is `market.has_two_sided_book` on
`context.siblings`, exactly the object neglected_leg and event_dominant_leg
already trust. Intervals are bootstrapped over SERIES, not observations,
because legs of one event resolve together. P&L crosses the spread at the ask
and pays the 1c fee, per INVARIANT #4.

  has >=1 quotable sibling, 0.60 <= mid < 0.95, spread <= 0.04
      n=5,488  ser=377  fills=4,897
      actual - mid  +0.0472  CI [+0.0370, +0.0607]
      P&L/contract  +0.0258  CI [+0.0142, +0.0373]

The control is the same band and spread with NO quotable sibling at all:

  0 quotable siblings, same band and spread
      n=2,527  ser=67  fills=2,292
      actual - mid  +0.0102  CI [-0.0086, +0.0326]
      P&L/contract  -0.0079  CI [-0.0273, +0.0116]

Read that control as the governing constraint made concrete: a real but small
bias, about one point, sits even on a market with no field at all -- and one
point is exactly what the ~2-point cost of crossing was always going to eat.
The claim here is not that isolated markets are perfectly calibrated. It is
that whatever residual bias they carry cannot be traded, and the moment a
second leg exists to compare against, the bias roughly quadruples and clears
the hurdle with room to spare.

DOSE-RESPONSE, same band and spread, by minimum quotable siblings:

    >= 0   n=8,015  gap +0.0356   (everything; includes the isolated control)
    >= 1   n=5,488  gap +0.0472
    >= 2   n=4,338  gap +0.0474
    >= 3   n=3,570  gap +0.0544
    >= 5   n=2,824  gap +0.0494
    >= 8   n=1,744  gap +0.0524

The step from zero to one sibling (+0.0356 -> +0.0472 on the pooled set, or
+0.0102 -> +0.0472 comparing the two disjoint populations directly) is the
whole story. Requiring a second, third or eighth sibling moves the gap by
under a point across that entire range -- a broad plateau, not a threshold
that wants tuning. MIN_SIBLINGS = 1 sits at the edge of that plateau on
purpose: existence is the claim, not degree.

ROBUSTNESS. Nothing is perched on one cell.

  spread cap 0.02/0.03/0.04/0.06/0.08 (>=1 sibling, same band)
      +0.033/+0.029/+0.026/+0.022/+0.017 -- decaying with width, as a cost
      story predicts.
  price band 0.55-0.95/0.60-0.90/0.65-0.95/0.60-0.99/0.50-0.95
      +0.027/+0.031/+0.024/+0.013/+0.023 -- the usual collapse only once the
      ceiling extends toward 1.0, where there is no room left to be
      underpriced by.
  crypto (KXBTC/KXETH/KXSOL/KXXRP/KXBNB) removed entirely (41% of the gated
  set): n=3,216 ser=367  P&L +0.0330 [+0.0167,+0.0493] -- against +0.0159
  [+0.0034,+0.0284] for crypto alone. Both intervals clear zero; this is not a
  crypto story wearing a structural label.
  selection series   n=3,744  P&L +0.0286 [+0.0156,+0.0415]
  confirmation series n=1,744  P&L +0.0199 [-0.0008,+0.0406] -- same sign,
  confirmation's interval just touches zero, the usual honest gap between the
  two halves of a series-clustered split.
  resolution date, first half / second half
      +0.0288 [+0.0138,+0.0438]  /  +0.0227 [+0.0092,+0.0362] -- both clear
      zero and close to the same size, unlike neglected_leg where the whole
      pooled result lived in one half.
  entry staleness, <=28min (median) / >28min
      +0.0259 [+0.0097,+0.0421]  /  +0.0256 [+0.0105,+0.0407] -- essentially
      identical. If this were the documented staleness artifact it should run
      higher on the stale side; it does not.
  leave-one-series-out over the five largest contributors (KXBTCD 15.5% of the
  gated set down to KXCS2GAME 3%): P&L ranges +0.0250 to +0.0293, every one of
  them still clear of zero.

WHAT IT ADDS, and what it does not. Four shipped candidates already require
`context.siblings` to be non-empty -- neglected_leg, event_dominant_leg,
ladder_leader, ladder_upper_body -- but every one of them needs the sibling
set for ARITHMETIC: a volume share, a margin to the best rival, a sum, a
monotone order. None of them measured whether sibling PRESENCE alone, with no
arithmetic on their content at all, was already carrying most of the gap their
conditioning further sharpens. It is: MIN_SIBLINGS=1 with no further
condition reaches 5.3% of all 104,189 observations, the widest gate in this
family by a wide margin (neglected_leg trades 1.3%, the ladder pair well under
1%), which is also the point of shipping it -- it is the fastest of the
sibling-reading candidates to accumulate the 200 fills PNL_GATE_MIN_FILLS
needs to render any verdict at all.

What it is honestly not: a sharper cut than what already exists. Overlap with
neglected_leg's exact gate (share < 0.10, same band and spread) is 1,480 of
5,488 gated observations, 27% -- most of this candidate's trades are NOT
inside neglected_leg's tighter selection, but where they coincide neglected_leg
already claims more edge per trade (+0.0498 on its own reported sample against
+0.0258 here). This candidate does not out-earn the sharper cuts on their own
turf. Its case is the isolated control none of them reported, and an action
rate none of them can match.

WHERE IT IS WEAK, stated plainly.

  - THE CONTROL IS NOT A CLEAN ZERO. -0.0079 with a CI of [-0.0273,+0.0116] is
    "does not clear costs", not "provably flat". A forward isolated-control
    P&L confidently above zero would not kill this candidate outright, but it
    would sand down the story from "the bias needs a field" to "the bias is
    smaller without one", which is a weaker and much less interesting claim.
  - THE MECHANISM IS A READING, NOT SHOWN, same as every candidate in this
    set. "Choice among alternatives is what creates the bias" fits the shape,
    but so does "isolated markets are a different population" -- narrower
    economic questions (single yes/no weather or macro prints) that a
    different, more careful kind of participant prices, independent of any
    field effect. Both predict the same isolated-vs-fielded split and I cannot
    separate them with this data.
  - NO MARGIN AND NO LEADER CHECK. A favourite one cent ahead of a genuine
    rival and a favourite forty cents ahead of a token, barely-quoted sibling
    both pass this gate identically. I looked at requiring the market to be
    the outright leader of its field (rank 1 by implied_prob) as an extra
    condition and it did not clearly help: on a restricted 2-4 sibling slice,
    leaders paid +0.0117 [-0.0135,+0.0370] against +0.0537 [+0.0107,+0.0967]
    for non-leaders on the same slice -- backwards from what a rank story
    predicts, on a sample thin enough (n=254 for the non-leader cell) that I
    do not trust the sign. I left rank out rather than add a condition that
    reversed itself on inspection.
  - SIBLING COVERAGE IS AN INSTRUMENT, NOT THE WORLD. `context.siblings` is
    capped to quotes within SIBLING_TOLERANCE_MIN of the entry and to whatever
    capture actually swept. A true sibling our sweep missed reads as
    "isolated" here. That failure mode pushes an observation OUT of the gate
    (toward the population this candidate does NOT act on), which is the safe
    direction, but it does mean the isolated control is somewhat contaminated
    by capture gaps rather than by genuinely single-leg events, and the
    magnitude of that contamination is not something this analysis can bound.
  - Found by scanning. Sibling COUNT (not just presence) was checked first --
    see the dose-response table -- and only explains a further point or two
    once presence is granted, most of it inside terrain the ladder candidates
    already trade (nsib>=9 is 100% events summing above 1.2). Absolute open
    interest level was checked too, as a cruder alternative to
    unchurned_favourite's ratio, and did not separate independent of book
    depth. The rank/leader check above is the other one that did not survive
    contact with the data. The interval above is not corrected for that
    search.
  - All in-sample. Every observation above resolved before this file's
    created_at, so INVARIANT #1 excludes all of them from its score. This
    docstring is a hypothesis with arithmetic attached, not a result.

WHAT WOULD FALSIFY IT. A forward P&L interval including zero on the gated
population. Distinctly, it is falsified as a claim about FIELDS specifically,
rather than about siblings-reading candidates in general, if the isolated
control starts paying as well as the gated set -- that would mean whatever
this measures has nothing to do with an event having a field of alternatives.
And it is falsified as anything worth shipping alongside neglected_leg if the
27% overlap grows toward all of it while the non-overlapping 73% stops paying
on its own -- that would mean this candidate is only neglected_leg's edge
diluted by an unselective gate, not a complementary measurement of it.

DELIBERATELY ONE-SIDED. The mirror was checked and is not there. Longshots
(0.05-0.35, spread <= 0.04) with no siblings show a small overpricing gap
(-0.0300) with P&L +0.0098 [-0.0088,+0.0285] buying NO; with siblings the gap
is similar (-0.0238) and P&L is flat, -0.0021 [-0.0108,+0.0066]. Sibling
presence does not sharpen the longshot side the way it sharpens the favourite
side, so that half is abstained on rather than traded small.

Shift is +0.03 against a measured actual-mid of +0.0472, below the low end of
its interval (+0.0370), on the same reasoning as every shift constant in this
set: carrying a fitted value into a forward test is how in-sample fitting
sneaks back in. It still clears the ~2-point cost of crossing.

Acts on 5.3% of all observations (5,488 of 104,189) -- the highest action rate
of any siblings-reading candidate in this set, chosen deliberately so this is
among the fastest candidates in the family to reach a P&L verdict rather than
sitting in PNL_GATE_UNPROVEN for lack of fills."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "fielded_favourite",
    "generation": 3,
    "parent_id": "stable_favourite",
    "created_at": "2026-09-11T14:26:03.667211+00:00",
    "rationale": (
        "The favourite-longshot bias is a story about choice among a field of "
        "alternatives. A favourite with at least one quotable sibling is "
        "underpriced by ~4.7 points; the same band with no sibling at all is "
        "underpriced by ~1.0 point, indistinguishable from zero and under the "
        "~2-point cost of crossing. Reads only whether a quotable sibling "
        "EXISTS, no arithmetic on its content -- unlike neglected_leg, "
        "event_dominant_leg or the ladder pair, which all need siblings for "
        "arithmetic but never measured the isolated case directly. Widest "
        "action rate of any siblings-reading candidate at 5.3%. Overlaps "
        "neglected_leg's tighter gate on only 27% of its trades."
    ),
}

EPS = 0.001

MIN_PRICE = 0.60        # below this the favourite bias does not cover the spread
MAX_PRICE = 0.95        # above this there is no room left to be underpriced by
MAX_SPREAD = 0.04       # cost gate; the edge decays as the book widens
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

    # The whole hypothesis: does this market belong to an event with a field
    # at all? Not how big the field is, not who leads it, not how the volume
    # or the price sums split across it -- only whether a second leg exists
    # for a bettor to have picked this one OVER. A one-sided sibling book is
    # not a live alternative (see MarketSnapshot.has_two_sided_book).
    if not any(s.has_two_sided_book for s in context.siblings):
        return p

    return min(p + SHIFT, 1.0 - EPS)
