"""Buy the leg of an event that the event's own order flow has ignored.

THE EDGE. Volume tells you where a market's attention went, but the ABSOLUTE
level of it is not readable: 5,000 contracts is heavy for a WNBA total and
nothing for a Bitcoin daily. Within one event the comparison is scale-free.
Every leg of an event shares an underlying, a close time and usually a market
maker, so the share of the event's volume that a leg has attracted says which
legs the flow actually looked at. The legs it did not look at are priced worse.
Conditioned on holding under 10% of its event's volume, a favourite is
underpriced by ~6.9 points; the same band with a normal share of the flow is
underpriced by ~3.9 and does not clear the cost of crossing by much.

The trigger is the leg's share of its EVENT's volume, not its own volume, not
its price level, and not its quote history.

That distinction is the entire reason this is not its parent. volume_weighted
compares a market's volume to a fixed constant (BUSY = 10_000) and concluded,
correctly, that quiet markets are mispriced and untradeable -- quiet markets are
wide, so INVARIANT #4 refuses them. A leg that is quiet RELATIVE TO ITS SIBLINGS
is a different population: the maker is quoting the whole event, so the neglected
leg inherits a tight book from its busy siblings while missing the flow that
would have corrected it. Cheap to trade and badly priced at the same time, which
is the combination the parent could not find.

THE EVIDENCE. Measured 2026-09-05 over all 65,225 scoreable entries. Share is
own volume over own-plus-siblings' volume, using only siblings with a two-sided
book. Intervals are bootstrapped over SERIES, not observations, because legs of
one event resolve together and i.i.d. intervals would be far too tight. P&L
crosses the spread at the ask and pays the 1c fee, per INVARIANT #4.

  share < 0.10, mid 0.60-0.95, spread <= 0.04   n=850  ser=89
      actual - mid  +0.0691  CI [+0.0486, +0.0931]
      P&L/contract  +0.0498  CI [+0.0277, +0.0732]

The control is the same rule with the share gate inverted:

  share >= 0.10, same band and spread   n=2151 ser=284
      actual - mid  +0.0387  CI [+0.0209, +0.0591]
      P&L/contract  +0.0194  CI [+0.0015, +0.0395]

Note honestly what that control is NOT: it is not a null. It is the ordinary
favourite bias, which is positive and which nine candidates have already failed
to turn into money. The claim here is only that the neglected leg carries about
2.6x as much of it, which is the difference between a 2-point edge the spread
eats and a 5-point one it does not.

THE CONTROL THAT MATTERS, since the parent is a volume candidate: split the same
band by OWN volume as well as by share.

    own volume below median, share < 0.10    n= 499  P&L +0.0526 [+0.0264,+0.0868]
    own volume below median, share >= 0.10   n=1001  P&L +0.0177 [-0.0024,+0.0411]
    own volume above median, share < 0.10    n= 351  P&L +0.0459 [-0.0190,+0.0845]
    own volume above median, share >= 0.10   n=1150  P&L +0.0208 [-0.0053,+0.0470]

The share gate separates inside both halves of the own-volume distribution, and
own volume separates nothing inside either share group. Heavily traded legs that
are nonetheless a small part of a much busier event pay as well as quiet ones.
It is the relative measure doing the work, not the absolute one wearing it.

Nor is it simply "events with many legs", where a small share is mechanical.
Within each leg-count bucket, share < 0.10 against share >= 0.10:

    2-4 siblings    +0.0719 vs +0.0157
    5-9 siblings    +0.0485 vs +0.0269
    10+ siblings    +0.0451 vs +0.0007

Robustness. The share cut is not a fitted threshold: 0.03/0.05/0.08/0.10/0.15/
0.20/0.25 gives P&L +0.042/+0.038/+0.044/+0.050/+0.039/+0.036/+0.035, positive
across the whole range with a broad plateau. Spread cap 0.02/0.03/0.04/0.06/0.08
gives +0.071/+0.058/+0.050/+0.035/+0.035, monotone in tightness as a cost story
predicts. Price floor 0.50/0.55/0.60/0.65/0.70 gives +0.047/+0.052/+0.050/
+0.049/+0.045. The ceiling is the one real cliff: extending it from 0.95 to 0.99
collapses P&L to +0.0102, because there is no room left to be underpriced by.
Leave-one-series-out over the ten largest series ranges +0.0456 to +0.0554, every
interval clear of zero.

WHAT IT ADDS to the candidates that already trade favourites. Its instrument is
cross-sectional, so it reaches markets the history-gated candidates cannot see at
all -- a market that has only just opened has no price path but does have
siblings. On the observations with no quote at least 60 minutes old, which
unmarked_favourite must abstain on entirely:

    no 60-min reference, share < 0.10    n=466  P&L +0.0424 [-0.0066, +0.0668]
    no 60-min reference, share >= 0.10   n=923  P&L -0.0030 [-0.0252, +0.0176]

Same for persistent_quote_favourite's gate: under 8 history points, share < 0.10
pays +0.0424 and share >= 0.10 pays +0.0035. And where they do overlap the two
axes stack rather than duplicate -- unrallied and share < 0.10 is +0.1170
[+0.0878, +0.1466] on n=145, against +0.0573 for unrallied alone.

WHERE IT IS WEAK, stated plainly. This is the part that matters most:

  - THE EDGE IS NOT STABLE ACROSS THE SAMPLE. Splitting by resolution date, the
    first half gives P&L +0.0204 against a control of +0.0248 -- no gap at all,
    the rule is worthless there -- and the second half gives +0.0803 against
    +0.0141. The whole pooled result lives in the back half. Part of that is our
    instrument: sibling capture coverage rose from 29% of entries on 2026-08-24
    to ~75% by 2026-08-26, and a share computed from half the legs is mostly
    noise. Dropping the two worst-coverage days improves the first half to
    +0.0401 against a +0.0321 control, so coverage explains some of it -- but the
    gap is still overwhelmingly a back-half phenomenon, and I cannot rule out
    that the effect is a few good weeks. 9 of 11 days are P&L-positive, which is
    reassuring about sign and says nothing about size. Treat the interval above
    as optimistic by more than the usual amount.
  - The subset leans on crypto and index ladders: KXBTCD 30%, KXETHD 13%, KXSOLD
    8%, two index series 14%. Excluding every crypto series leaves n=387 at P&L
    +0.0588 [+0.0259, +0.1003] against a non-crypto control of +0.0327, so it is
    not only crypto -- but "flow ignores a leg" is measured mostly on daily
    threshold ladders, and if those are one maker with one quoting rule this is
    fewer independent observations than 89 series suggests.
  - 798 of the 850 trades are in events whose legs sum above 1.2, i.e.
    overlapping rather than mutually exclusive. The near-exclusive cell is n=52
    and settles nothing. So this is largely a claim about multi-leg ladder-shaped
    events, the same terrain as ladder_upper_body. It is not that candidate's
    rule -- removing every observation its ladder test would classify leaves
    n=746 at +0.0454 [+0.0248, +0.0726] -- but that test is a ticker-string
    heuristic, and failing it does not prove an event is structurally different.
    If both score, they should be checked for redundancy before either is bred
    from.
  - The mechanism is inferred, not shown. "Flow corrects the legs it touches" fits
    the profile, but so does "the neglected leg is the one whose quote the maker
    refreshes last", and those are the same prediction from different causes. A
    third story is simple staleness of the sibling volumes themselves.
  - Volume share is measured against siblings quoted up to 20 minutes earlier
    (SIBLING_TOLERANCE_MIN), and against only the legs our sweep captured. A
    missed leg understates the denominator and inflates the share, which pushes
    an observation OUT of the gate. It fails toward abstention, which is the safe
    direction, but it does mean the population depends on capture coverage.
  - Found by scanning. I tested open-interest flow composition (what fraction of
    recent trading created new positions), spread-path dynamics, own OI/volume
    ratio and event breadth first; the first three separated nothing once the
    ordinary favourite bias was accounted for. The intervals here are not
    corrected for that search.
  - All in-sample. Every observation above resolved before this file's
    created_at, so INVARIANT #1 excludes all of them from its score. This
    docstring is a hypothesis with arithmetic attached, not a result.

WHAT WOULD FALSIFY IT. A forward P&L interval including zero on this subset. More
sharply than usual, it is falsified if the forward result looks like the FIRST
half of this sample rather than the second -- that is a live possibility and not
a remote one, and it would mean the back-half gap was a regime and not a
mechanism. It is falsified differently if the share >= 0.10 control pays as well
forward, which would make this the ordinary favourite bias with a decorative
gate. And it is falsified as a claim about attention, specifically, if own
absolute volume starts separating as well as share does: the parent's whole
finding was that absolute volume marks the untradeable markets, and if the two
measures converge forward then nothing was gained by making it relative.

DELIBERATELY ONE-SIDED. The mirror does not exist. Neglected longshots (0.02-0.20,
share < 0.05) are more overpriced than busy ones -- edge -0.0265 against -0.0113
-- which is the same shape pointing the other way, and buying NO returns +0.0046
per contract. Right about direction, does not clear the spread, which is the exact
failure the brief warns about. So that half is abstained on rather than traded
small.

Shift is +0.03 against a measured +0.0691, deliberately below the low end of the
interval and below the first-half subsample estimate too, on the same reasoning
as favourite_longshot and ladder_leader: carrying a fitted value into a forward
test is how in-sample fitting sneaks back in, and here the sample instability
argues for taking even less than usual. It still clears the ~2-point cost of
crossing.

Abstains on 98.7% of observations by construction, so its whole-population Brier
skill will look like nothing next to candidates that nudge every market. That is
the intended shape: acting on 1.3% of markets at five points beats nudging all of
them at one and paying the spread each time."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "neglected_leg",
    "generation": 3,
    "parent_id": "volume_weighted",
    "created_at": "2026-09-05T06:11:11.068779+00:00",
    "rationale": (
        "Within an event, the leg holding under 10% of the event's volume is "
        "underpriced by ~6.9 points as a favourite, against ~3.9 for legs with "
        "a normal share. Relative attention, not absolute volume: the split "
        "holds in both halves of the own-volume distribution, and own volume "
        "separates nothing inside either share group. Reaches markets too young "
        "for the history-gated candidates. Abstains on 98.7%."
    ),
}

EPS = 0.001

MIN_SIBLINGS = 1      # one quotable sibling is enough to form a share
MAX_SHARE = 0.10      # under this the event's flow has gone elsewhere
MIN_PRICE = 0.60      # below this the bias does not cover the spread
MAX_PRICE = 0.95      # above this there is no room left to be underpriced by
MAX_SPREAD = 0.04     # cost gate; the edge is monotone in how tight the book is
SHIFT = 0.03


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob

    # Cost gate first. Nothing below matters on a book we cannot cross cheaply,
    # and simulate_fill rejects a spread above 0.08 outright.
    if market.spread > MAX_SPREAD or not (MIN_PRICE <= p < MAX_PRICE):
        return p

    # Only siblings with a real book count. A leg quoted 0.0/1.0 has no market
    # belief behind it, and its volume is not evidence of where flow went.
    quotable = [s for s in context.siblings if s.has_two_sided_book]
    if len(quotable) < MIN_SIBLINGS:
        return p

    total = market.volume + sum(s.volume for s in quotable)
    if total <= 0.0:
        return p

    # The whole hypothesis. Volume is compared only WITHIN the event, never to a
    # constant: the same 5,000 contracts is heavy in one series and negligible in
    # another, and that is what made the parent's fixed threshold unreadable.
    # Note the direction of the instrument's error -- an uncaptured sibling
    # shrinks the denominator and raises the share, pushing us out of the gate
    # rather than into it.
    if market.volume / total >= MAX_SHARE:
        return p

    return min(p + SHIFT, 1.0 - EPS)
