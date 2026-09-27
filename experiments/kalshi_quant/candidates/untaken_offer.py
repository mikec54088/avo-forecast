"""Buy the favourite whose resting offer is sized like its siblings' but has
not attracted its share of the event's trading.

THE EDGE. Two candidates already read a leg's share of its event's activity --
crowded_field_favourite counts siblings, neglected_leg reads volume share -- and
both read only what has ALREADY happened: a static count, or a record of trades
already executed. Neither reads what the market maker is offering RIGHT NOW.
`yes_ask_size` is the maker's current, live commitment to sell this leg at this
price; comparing it across an event's siblings says whether a leg is being
quoted in normal size or is structurally thin, which a volume history cannot
distinguish from "quoted fine, just unwanted". Conditioned on that own resting
offer holding at least 5% of the event's total resting offer size -- i.e. the
maker is willing to quote it in a normal amount -- a favourite that has
nonetheless captured under 10% of the event's trading volume is underpriced by
~6.7 points. The same volume-share condition on a leg that IS thin on depth
(under 5% of the event's resting size) is underpriced by only ~2.4, indistin-
guishable from the leg that is ordinarily busy on volume but merely thin on
depth (~2.2). A tradeable leg nobody has bought pays about twice what a
possibly-illiquid one that also nobody has bought pays.

The trigger is TWO fields together: this leg's share of the event's resting
ask size, and this leg's share of the event's traded volume. Neither alone
separates -- see the two-way table below -- and this candidate reads no
leg's price level, no price history, no sibling count and no ladder order.

WHY THIS IS NOT ITS PARENT. crowded_field_favourite's whole instrument is a
COUNT of quotable siblings -- it never reads a number off any leg's book, only
how many rivals exist. This candidate reads two live book quantities (depth
and volume, both compared within the event) and never counts siblings as
such; a two-sibling event and a twenty-sibling event enter this gate identically
provided the two ratios hold. Of the 2,171 entries this candidate trades, only
13.8% also satisfy crowded_field_favourite's gate (breadth >= 3 quotable
siblings, not a ladder) -- the two are built from different objects and
mostly disjoint populations, unlike the near-total overlap below.

RELATION TO NEGLECTED_LEG, stated as plainly as the overlap requires. Every
entry this candidate trades also satisfies neglected_leg's own volume-share
gate (share < 0.10) -- by construction, since this candidate uses the
identical volume-share formula as one of its two conditions. This is NOT an
independent instrument the way it is against crowded_field_favourite; it is a
depth-qualified SUBSET of neglected_leg's population, in the same relationship
patient_crowded_field has to crowded_field_favourite. The finding is not "a
new signal exists" but "neglected_leg's own edge is not uniform across its
population, and resting depth is what separates the real part from the part
that is mechanically explained." Within neglected_leg's raw volume-share<0.10
population on this band and spread, P&L is +0.0324 [+0.0216,+0.0457] pooled;
splitting it by this leg's depth share gives +0.0445 [+0.0304,+0.0660] for
depth >= 5% against +0.0240 [+0.0098,+0.0347] for depth < 5%. Both halves
clear zero -- depth is not the difference between signal and noise here -- but
they are not the same number, and the half this candidate keeps is the
stronger one. Read this candidate as evidence about WHY neglected_leg works
(a genuinely tradeable leg being passed over reads differently from a thin one
being passed over) rather than as a claim of a new, orthogonal mechanism.

THE EVIDENCE. Measured 2026-09-26 over all 201,859 entries in the current
store. `depth_share` and `vol_share` are both computed over the same set of
quotable siblings (two-sided book, resting ask size present and positive),
matching neglected_leg's own denominator convention. P&L crosses the spread
at the ask and pays the same variable taker fee `scoring.py` uses, per
INVARIANT #4; this is a per-contract figure at size 1 and is not run through
`simulate_fill`'s depth cap, so it does not account for the maker withdrawing
size between observation and a real order -- a caveat shared with every
candidate's headline number in this project, and see below for how thin the
tail of this population's own absolute depth is.

  depth_share >= 0.05, vol_share < 0.10, 0.55 <= mid < 0.97, spread <= 0.04
      n=2,171  ser=194   (1.08% of the store; 1.50% of the digest's
                          144,751-observation frame)
      actual - mid   +0.0670  CI [+0.0522, +0.0900]
      P&L/contract   +0.0445  CI [+0.0304, +0.0660]

THE CONTROL is the same band and spread with the gate inverted -- thin depth,
OR a normal volume share, or both:

  n=12,434  ser=485
      actual - mid   +0.0381  CI [+0.0313, +0.0465]
      P&L/contract   +0.0154  CI [+0.0082, +0.0235]

Read that control the same way every prior candidate's has been read: it is
positive too, and it is the ordinary favourite bias nine candidates have
already failed to monetise. The claim is that this compound condition carries
about 2.9x as much of it.

THE TWO-WAY TABLE is the whole argument, same band and spread throughout:

    depth <  5%, vol <  10%   n=3,123  P&L +0.0240 [+0.0098,+0.0347]
    depth >= 5%, vol <  10%   n=2,171  P&L +0.0445 [+0.0304,+0.0660]  <- shipped
    depth <  5%, vol >= 10%   n=3,404  P&L +0.0223 [+0.0099,+0.0332]
    depth >= 5%, vol >= 10%   n=5,907  P&L +0.0069 [-0.0035,+0.0188]

Neither axis alone separates: thin depth with low volume share pays about the
same as thin depth with normal volume share (0.0240 vs 0.0223), and normal
depth with normal volume -- the cell with no story attached to it -- is the
only one whose interval spans zero. The edge needs both.

ROBUSTNESS. Depth threshold, vol_share fixed at < 0.10: 0.00/0.05/0.10/0.15/
0.20/0.30 gives P&L +0.0324/+0.0445/+0.0527/+0.0548/+0.0501/+0.0288 -- a
gradient that rises and then fades as the sample thins, not a cliff. Vol_share
threshold, depth fixed at >= 0.05: 0.05/0.08/0.10/0.15/0.20/0.30 gives
+0.0462/+0.0406/+0.0445/+0.0345/+0.0313/+0.0225, monotone-ish decay as the cut
loosens. Spread cap 0.02/0.03/0.04/0.06/0.08 gives +0.0518/+0.0472/+0.0445/
+0.0379/+0.0343, monotone in tightness the way a cost story predicts. Price
ceiling matters the way it did for neglected_leg: 0.90/0.95/0.97/0.99 gives
+0.0796/+0.0573/+0.0445/+0.0202 -- extending toward 1.0 leaves no room to be
underpriced by, so 0.97 is a deliberately conservative stopping point, not the
best-looking one.

No single series drives it: KXMLBTOTAL is the largest pooled-P&L contributor
at 10.5%, and leaving it out gives +0.0417 [+0.0293,+0.0616] on the remaining
2,072; dropping KXBTCD (9.3%) or KXETHD (9.1%) instead leaves +0.0508 and
+0.0459 respectively, both intervals clear of zero.

DATE-HALF STABILITY: first half by resolution date +0.0463 [+0.0284,+0.0746]
on n=1,085, second half +0.0427 [+0.0206,+0.0666] on n=1,086. Unlike
neglected_leg's own back-half-only result, this one replicates across the
split.

LADDER AND CRYPTO CONCENTRATION, disclosed the way neglected_leg disclosed
its own: 83.6% of this population is ladder-classified by the same
ticker-suffix concordance test crowded_field_favourite uses, and the four
largest series are all crypto daily-range ladders (KXBTCD, KXETHD, KXSOLD,
KXHYPED, 45% of the population between them). Excluding every one of those
four leaves n=1,192 at P&L +0.0542 [+0.0314,+0.0794] -- the estimate holds and
does not depend on crypto specifically. Excluding every ladder-classified
observation leaves only n=357 at P&L +0.0385, and the interval widens to
[-0.0118,+0.0875] -- it still points the right way but no longer clears zero
on its own at that size. This candidate's populated terrain is overwhelmingly
threshold-ladder shaped, the same terrain neglected_leg and ladder_upper_body
already occupy, and that should be weighed against the "not its parent"
argument above: it is a different instrument from ladder_leader's leg-order
test, but it is largely the same MARKETS.

WHERE IT IS WEAK, stated plainly:

  - It is a depth-qualified subset of neglected_leg, not an independent
    discovery -- see the relation section above. If neglected_leg is ever
    retired or its formula changes, this candidate's population changes with
    it, and the two should not both be counted as separate evidence for the
    same portfolio.
  - Excluding ladders drops the interval's lower bound below zero (n=357).
    The non-ladder evidence is suggestive, not standing on its own.
  - The mechanism is inferred, not shown. "The maker is willing to quote size
    but flow has not arrived" fits the numbers, but so does "these are the
    legs whose true probability the maker has not yet had a reason to move
    off a stale reference price", which is a staleness story rather than an
    attention one and would predict the same population.
  - Own absolute depth has real spread: p10 is 54 contracts against a p90 of
    20,000, so a small tail of this population is "sized like its siblings"
    only in the relative sense the gate checks, not in absolute liquidity
    that guarantees an order actually fills.
  - Found by slicing the same population neglected_leg already reports on; the
    two-way table above is not corrected for that search, and depth-share and
    volume-share are correlated in general even if the effect does not
    collapse to either alone (see the two-way table).
  - All in-sample. Every observation above resolved before this file's
    created_at, so INVARIANT #1 excludes every one of them from this
    candidate's own score. This docstring is a hypothesis with arithmetic
    attached, not a result.

WHAT WOULD FALSIFY IT. A forward P&L interval including zero on the shipped
subset. More specifically: if the depth >= 5% and depth < 5% cells stop
differing forward (both landing near +0.03), the depth axis was noise riding
on neglected_leg's own edge, not a real refinement of it. And if the non-
ladder slice's forward interval sits clearly below the pooled one rather than
around it, the ladder concentration disclosed above was load-bearing rather
than incidental, and this candidate should be read as ladder-territory only.

The mirror was not tested, for the same reason crowded_field_favourite's was
not: lack of session time. Absence of a check should not be read as evidence
either way, unlike neglected_leg's longshot mirror, which was tested and
failed.

Shift is +0.03 against a measured actual-mid gap of +0.0670 and a P&L lower
bound of +0.0304 -- under half the point estimate and below both interval
lower bounds, on the same reasoning as every shipped candidate in this
project: carrying a fitted value into a forward test reintroduces the fitting
it is meant to avoid. It still clears the ~2-point cost of crossing with
margin to spare even if the forward edge comes in well under this one.

Abstains on roughly 99% of observations by construction. Acting on ~1% of
markets at 4-6 points beats nudging all of them at one and paying the spread
each time -- the same shape crowded_field_favourite and neglected_leg both
argue for.

Parent is crowded_field_favourite: this candidate keeps its premise that
event STRUCTURE, not any single leg's own price, can carry an edge large
enough to survive the spread, and changes the structural signal from a count
of rivals to a comparison of two live book quantities against those rivals."""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "untaken_offer",
    "generation": 4,
    "parent_id": "crowded_field_favourite",
    "created_at": "2026-09-26T12:01:58.719325+00:00",
    "rationale": (
        "A favourite whose resting ask size holds >=5% of its event's total "
        "resting size, but whose volume holds <10% of the event's traded "
        "volume, is underpriced by ~6.7 points -- about double either "
        "condition alone. Reads live book depth against siblings, not a "
        "sibling count or a volume history alone; a depth-qualified subset "
        "of neglected_leg's own population, not an independent instrument. "
        "Abstains on ~99%."
    ),
}

EPS = 0.001

MIN_SIBLINGS = 1        # one quotable, sized sibling is enough to form a share
MIN_DEPTH_SHARE = 0.05  # the maker is willing to quote this leg in normal size
MAX_VOL_SHARE = 0.10    # yet trading has gone elsewhere
MIN_PRICE = 0.55
MAX_PRICE = 0.97        # above this there is no room left to be underpriced by
MAX_SPREAD = 0.04       # cost gate; the edge is monotone in how tight the book is
SHIFT = 0.03


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob

    # Cost gate first. Nothing below matters on a book we cannot cross
    # cheaply, and simulate_fill rejects a spread above 0.08 outright.
    if market.spread > MAX_SPREAD or not (MIN_PRICE <= p < MAX_PRICE):
        return p

    if market.yes_ask_size is None or market.yes_ask_size <= 0:
        return p

    # Quotable AND sized: a sibling with no resting ask tells us nothing about
    # how the maker has allocated depth across the event.
    quotable = [
        s for s in context.siblings
        if s.has_two_sided_book and s.yes_ask_size is not None and s.yes_ask_size > 0
    ]
    if len(quotable) < MIN_SIBLINGS:
        return p

    total_depth = market.yes_ask_size + sum(s.yes_ask_size for s in quotable)  # type: ignore[misc]
    if total_depth <= 0.0:
        return p
    depth_share = market.yes_ask_size / total_depth
    if depth_share < MIN_DEPTH_SHARE:
        return p

    total_volume = market.volume + sum(s.volume for s in quotable)
    if total_volume <= 0.0:
        return p
    vol_share = market.volume / total_volume

    # The whole hypothesis: sized like its siblings (so it is not merely
    # illiquid), yet the trading itself has gone elsewhere.
    if vol_share >= MAX_VOL_SHARE:
        return p

    return min(p + SHIFT, 1.0 - EPS)
