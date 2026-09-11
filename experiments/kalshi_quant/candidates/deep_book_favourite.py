"""Buy the favourite that has a large two-sided book standing behind its quote.

THE EDGE. Volume and open interest describe trades that have already happened.
The resting size at the top of the book describes demand that has NOT happened
yet: contracts somebody is willing to buy or sell at this price, right now, and
has not been paid for. Those are different objects measured on different sides
of the same market, and only the first has been read here.

A favourite whose touch carries at least 2,000 contracts of two-sided size is
underpriced by ~4.8 points. A favourite at the same price, on an equally tight
book, whose touch carries less is underpriced by ~2.7, which the ~2-point cost
of crossing very nearly eats. The trigger is how much size is STANDING, not how
much has traded and not which side it leans to.

WHY THIS IS NOT ITS PARENT. unchurned_favourite divides the change in open
interest by the change in volume along the market's own path -- what the tape
left behind. This reads the book instead, and it reads its size rather than its
direction. Split the same band by both axes at once (the parent's gate is
capture >= 0.6 with at least 25 contracts of path flow):

    deep >= 2000, parent gate met      n=1156 ser=231  P&L +0.0562
    deep >= 2000, parent gate not met  n=1759 ser=147  P&L +0.0081 [-0.0109,+0.0260]
    thin <  2000, parent gate met      n=1839 ser=312  P&L +0.0363 [+0.0196,+0.0515]
    thin <  2000, parent gate not met  n=3117 ser=178  P&L -0.0114

Read that honestly, because it cuts both ways. Depth separates inside both
halves of the parent's split (+0.0562 against +0.0363; +0.0081 against -0.0114),
so it is not the parent's statistic wearing a different name. But the parent's
statistic separates inside both halves of mine by about twice as much, and on
this sample the depth gate ALONE is the weaker of the two instruments. What
earns it a forward test anyway is coverage: bid and ask sizes are present on
100% of the 102,545 scoreable entries, while the capture ratio is defined on
58.6% of them, and 404 of the 2,915 markets this candidate trades have no price
history at all -- the parent cannot see them even to abstain on them.

THE MECHANISM, offered as a reading and not as a demonstrated cause. The
favourite-longshot bias is usually explained as demand: buyers want the side
that will probably win and pay up for it, against a maker who is happy to sell.
Demand of that kind has to be visible somewhere, and the place it is visible
before it trades is the book. A market with four contracts at the touch has
nobody standing in it; whatever else is true of its price, there is no demand
pressure to be on the right side of. A market with 8,000 contracts resting has
real participation, and the bias should live there and nowhere else. The
prediction is that the size of the touch, not its imbalance, marks where the
bias is. Book imbalance is a claim about which way the next trade goes;
book SIZE is a claim about whether anyone is there at all.

THE EVIDENCE. Measured 2026-09-10 over all 102,545 scoreable entries, 18
resolution days. Depth is `yes_bid_size + yes_ask_size` at the entry snapshot.
Intervals are bootstrapped over SERIES, not observations, because markets in one
series share an underlying and a day and an i.i.d. interval would be far too
tight. P&L crosses the spread at the ask and pays the 1c fee, per INVARIANT #4.

  depth >= 2000, 0.60 <= mid < 0.95, spread <= 0.04
      n=2915  ser=262  fills=2758
      actual - mid  +0.0480  CI [+0.0329, +0.0656]
      P&L/contract  +0.0272  CI [+0.0113, +0.0462]

The control is the same rule with the depth gate inverted:

  depth < 2000, same band, same spread
      n=4956  ser=356  fills=4304
      actual - mid  +0.0267  CI [+0.0141, +0.0403]
      P&L/contract  +0.0058  CI [-0.0075, +0.0191]

That control is not a null. It is the ordinary favourite bias, which is positive
and which nine scored candidates have already failed to turn into money; its
interval includes zero, which is the whole problem with it. The claim here is
only that the deep half carries about twice as much of it, which is the
difference between an edge the spread eats and one it does not.

THE PROFILE IS A DOSE-RESPONSE, which is the main reason to believe it is not a
cell found by slicing. P&L per contract by depth floor, same band and spread:

    >=   200   n=5693   +0.0181
    >=   500   n=4491   +0.0223
    >=  1000   n=3750   +0.0214
    >=  2000   n=2915   +0.0272
    >=  3000   n=1465   +0.0339
    >=  5000   n= 954   +0.0435
    >= 10000   n= 548   +0.0446

Rising almost monotonically with size and flattening at the top. 2,000 is chosen
near the low end of that curve rather than at its peak: >= 5,000 pays more and
acts on 0.9% of markets, which is below the rate at which a week of capture can
judge it. Depth >= 2,000 is a quarter of all entries; the price band and the
spread cap are what make the action rate 2.8%.

ROBUSTNESS. Nothing is perched on one cell.

  spread cap 0.01/0.02/0.03/0.04/0.06/0.08
      +0.033/+0.034/+0.027/+0.027/+0.021/+0.020 -- decaying with width, as a
      cost story predicts.
  price band 0.60-0.90 / 0.60-0.95 / 0.65-0.95 / 0.55-0.95 / 0.50-0.95 / 0.60-0.99
      +0.032/+0.027/+0.022/+0.024/+0.018/+0.015 -- the usual collapse once the
      ceiling extends toward 1.0, where there is no room left to be underpriced.
  leave-one-series-out over the ten largest contributors
      +0.0229 to +0.0302, every one of them positive.
  crypto series removed entirely (they are 50.7% of the gated set)
      n=1438 ser=248  P&L +0.0395  CI [+0.0129, +0.0650]
  14 of 18 resolution days are P&L-positive. The worst are 2026-09-08 (-0.0431
  on 186 fills) and 2026-08-26 (-0.0275 on 155). 2026-09-10 shows -0.0214 but is
  inside the ~2-day resolution backfill lag and is not a settled number.

IT DOES NOT COLLAPSE ON THE HELD-OUT SERIES, which is the test the generation-3
behavioural-gate family failed:

    selection series     n=2113 ser=180  P&L +0.0259  CI [+0.0050, +0.0481]
    confirmation series  n= 802 ser= 82  P&L +0.0307  CI [+0.0068, +0.0741]
    control, selection                   P&L +0.0055
    control, confirmation                P&L +0.0065

Nor across time. Splitting on resolution date, the first half pays +0.0264
[+0.0051, +0.0502] and the second +0.0282 [+0.0109, +0.0481], against a control
of +0.0055 and +0.0062. Both halves carry it at the same size, which is more
than neglected_leg or unchurned_favourite can say about their own samples.

IT IS NOT THE BEHAVIOURAL-GATE FAMILY (unmoved, untraded, unchurned, unclimbed,
ground), whatever its shape suggests. Every one of those reads the market's own
quote path, so each abstains on markets too young to have one and each ended up
with a few hundred fills. This reads a field that exists on the first snapshot
of a market's life, acts on 2.8% of observations, and would produce roughly
1,100 fills a week at the current capture rate. It can be proven wrong quickly,
which is the property that family lacked.

WHERE IT IS WEAK, stated plainly. The first two items are the ones that would
stop me putting money on it:

  - IT LOSES ON THE FRESHEST ENTRIES. By staleness of the entry quote, the gate
    pays -0.0202 (n=467) at 0-15 minutes, +0.0253 (n=1160) at 15-30, and +0.0464
    (n=1288) at 30-60, against a control of -0.0039, +0.0140 and +0.0109. In the
    freshest bucket the gate is not merely weak, it is WORSE than the control,
    and the whole favourite bias is close to absent there for everyone. The
    documented staleness artifact inflates Brier skill and not P&L, so this is
    not simply that artifact -- but an edge that grows with how old the quote is
    should be assumed to be partly about the quote being old. If forward capture
    gets denser, this candidate should get worse.
  - HALF THE MONEY IS IN THE FAR-CLOSE POPULATION, which I do not trust. Inside
    the gate, markets more than 6 hours from their close_time pay +0.0568
    (n=1054, ser=237) and markets near their close pay +0.0099 (n=1861, ser=25).
    An entry is by construction the last snapshot before resolution, so a distant
    close_time selects markets that settled EARLY, and settling early correlates
    with the settling condition having been met, i.e. with YES. That is a
    property of the observation set rather than of the market, and it is the most
    likely way for this number to be flattering. I did not gate on horizon for
    exactly that reason, but I cannot remove the population it contaminates.
  - Single-leg markets inside the gate lose money: n=447 ser=28, P&L -0.0125,
    against +0.0345 for the 2,468 gated markets that have quoted siblings. If
    resting size measured demand as such, a deep binary market should carry the
    bias too. It does not, which is evidence that what depth marks is partly just
    multi-leg, maker-quoted events. I left them in rather than adding a second
    condition, because a gate that needs two conditions is how the last
    generation produced candidates too narrow to judge.
  - It separates within volume quartiles unevenly. Deep against thin by own
    volume quartile: +0.0414/-0.0007, +0.0309/+0.0223, +0.0043/-0.0180,
    +0.0313/+0.0223. The gap is real in every quartile but is large in only two
    of them, and the third quartile is near zero on both sides.
  - The mechanism is inferred, not shown. "Resting size marks where demand is"
    fits the profile, but "resting size marks which markets a professional maker
    quotes with size" fits it equally well, and those are the same prediction
    from different causes. The second story makes this a taxonomy of venues
    rather than a signal -- the same worry unchurned_favourite records about
    itself, and the series list here (crypto dailies, 15-minute index and metals
    ladders, MLB totals) is the same neighbourhood.
  - Found by scanning, and the scan was wide. Before this I measured and
    discarded: event coherence on series whose own resolution history says
    exactly one leg per event wins (a history-derived exclusivity probe, which
    classifies sharply -- but the overround it validates is worth 1-3 points and
    both sides of it lose money); the complement gap, own mid against 1 minus the
    sum of the rivals, on those verified-exclusive fully-captured events
    (non-monotone, no separation); monotonicity violations inside ordered
    ladders, both local and by isotonic deviation (real arbitrage is rare -- a
    few hundred observations across 5-8 series -- and carries no consistent
    sign); dollar-at-risk book imbalance, bid_size*bid against ask_size*(1-ask),
    which is the natural fix to the contract-count imbalance book_imbalance_tilt
    lost money with, and which is no better than it; rank within the event, the
    leader against the field, on verified-exclusive events; and three
    normalisations of depth -- against the median sibling's depth, against open
    interest, and against volume -- all of which are U-shaped and weaker than
    raw size. The intervals above are not corrected for that search.
  - All in-sample. Every observation above resolved before this file's
    created_at, so INVARIANT #1 excludes all of them from its score. This
    docstring is a hypothesis with arithmetic attached, not a result.

WHAT WOULD FALSIFY IT. A forward P&L interval including zero on this subset.
More sharply, it is falsified as a claim about STANDING DEMAND if the depth
gradient flattens while the parent's capture ratio keeps separating -- that
would mean the tape carries the information and the book merely correlates with
it. It is falsified as anything but a venue taxonomy if the deep and thin halves
stop differing inside the series that carry both, and the single-leg cell above
is already an argument that this is partly what is happening. And it is
falsified as a tradable edge, specifically, if the forward result looks like the
0-15 minute staleness bucket rather than the 30-60 minute one: that is the
bucket where a live trader would actually be filling, and the gate loses money
in it today.

DELIBERATELY ONE-SIDED. The mirror was measured and is not there. Longshots
priced 0.05-0.35 on deep books are overpriced by +0.0246 and on thin books by
+0.0240 -- no gap at all -- and buying NO returns +0.0006 [-0.0128, +0.0098]
deep against -0.0019 thin. Depth says nothing about the longshot tail, so that
half is abstained on rather than traded small. That asymmetry is itself mild
evidence against the demand story, which would predict both tails move together.

Shift is +0.03 against a measured edge of +0.0480, below the low end of the
interval (+0.0329) and well under the point estimate, on the same reasoning as
favourite_longshot and unchurned_favourite: carrying a fitted value into a
forward test is how in-sample fitting sneaks back in. It still clears the
~2-point cost of crossing.

Abstains on 97.2% of observations, so its whole-population Brier skill will look
like nothing beside candidates that nudge every market. That is the intended
shape: acting on 2.8% of markets at three points beats nudging all of them at
one and paying the spread each time.
"""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "deep_book_favourite",
    "generation": 4,
    "parent_id": "unchurned_favourite",
    "created_at": "2026-09-11T05:24:11.858862+00:00",
    "rationale": (
        "Read the size standing at the touch rather than the flow that has "
        "already traded. A favourite whose book carries at least 2,000 "
        "contracts of two-sided resting size is underpriced by ~4.8 points; an "
        "equally priced favourite on an equally tight but shallower book by "
        "~2.7, which the spread eats. The parent reads what the tape left "
        "behind, which is defined on 58.6% of entries; resting size is present "
        "on 100% of them, including markets with no price history at all. "
        "Separates in both halves of the parent's split, holds on the "
        "confirmation series and in both time halves, and loses money on the "
        "freshest entries. Abstains on 97.2%."
    ),
}

EPS = 0.001

MIN_PRICE = 0.60        # below this the favourite bias does not cover the spread
MAX_PRICE = 0.95        # above this there is no room left to be underpriced by
MAX_SPREAD = 0.04       # cost gate; the edge decays as the book widens
MIN_DEPTH = 2000.0      # contracts at the touch, both sides summed
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

    # The sizes are typed optional and are populated on every observation ever
    # captured, but a missing one is not a shallow book -- it is no measurement,
    # and the difference matters here in a way it would not for a field that is
    # merely usually present. Abstain rather than read None as zero.
    bid_size, ask_size = market.yes_bid_size, market.yes_ask_size
    if bid_size is None or ask_size is None:
        return p

    # The whole hypothesis. Not what has traded and not which side the book
    # leans to -- how much size is standing there unfilled. Both sides are
    # summed on purpose: the claim is about participation in the market, not
    # about pressure in a direction, and the direction version of this field is
    # what book_imbalance_tilt already lost money on.
    if bid_size + ask_size < MIN_DEPTH:
        return p

    # `context` is deliberately unread. Needing no price history is the point:
    # 404 of the markets this acts on have none, and every history-gated
    # candidate in the set must abstain on them.
    return min(p + SHIFT, 1.0 - EPS)
