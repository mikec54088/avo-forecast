"""Take the event's overround out of a leg: sell the leg of an over-full field.

THE EDGE. If the legs of an event are mutually exclusive and exhaustive, their
fair prices sum to exactly 1.0. A market maker quoting the whole field at once
keeps a margin by letting the quoted midpoints sum a little ABOVE 1.0 (the
overround). That margin is not information about any one leg: it is a tax spread
across all of them. The honest price of a leg is its midpoint divided by the
event's sum, so a 0.60 leg in an event summing to 1.12 is worth about 0.536 --
5 points below its quote. This is arithmetic on the field, not a prediction
about the outcome, and it reads an input (`context.sibling_sum`) that no
candidate has used as an aggregate except sibling_coherence.

Why this is not sibling_coherence. That candidate fired on ANY sum away from 1.0
and was documented as flawed: nested ladders (over 1.5 / 2.5 / 3.5) correctly
sum above 1.0 and the rule treated them as overpriced. Here the trigger is a
NARROW band of sums, 1.06-1.25, with 3-12 legs. A ladder of that many legs
sums far higher (the dataset median over multi-leg events is 1.04 but the p90
is 6.68), so a modest overround across a handful of legs is what an exclusive
field looks like and not what a ladder looks like. It is a proxy, not proof, and
that is the main weakness below.

It is also one-directional on purpose. A sum BELOW 0.95 usually means legs we
failed to capture, not underpriced legs, so that side abstains.

WHERE THE MONEY HAS TO COME FROM. The fade is a purchase of NO at the bid side,
so the cost hurdle is again ~2 points. The leg-wise correction is
p * (1 - 1/S), which exceeds 2 points only where p is not small and S is at
least ~1.06; hence the price floor and the sum floor. Legs below 0.15 would need
an overround of >10% to clear it and are skipped.

EVIDENCE. NONE MEASURED. I deliberately did not load the entries store: the
brief forbids a full scan and a sized check would have cost the session. The
only dataset facts used are the shared ones: 63,109 multi-leg events, 82% of
observations have >=2 quoted siblings, 49% of events sum above 1.05 (mostly
ladders, which this candidate tries to exclude via the band). Everything here is
a hypothesis; the number I would most want first is P&L/contract inside the
band against the same band restricted to ladder-shaped tickers.

ACTION RATE. Not measured. Guess: a few percent of observations (the sum band is
narrow, the price and spread gates cut more), enough for ~200 fills inside a
week or two but I cannot promise it. If it acts on well under 1% it is
unfalsifiable and should be retired, not tuned.

THE CONTROL. The same rule with the band inverted -- events summing 0.95-1.05,
where there is no overround to remove -- should pay nothing; the correction
there is under 2 points by construction. If that control makes money forward the
gain is not the overround but a general fade of favourites in multi-leg events.

WHAT WOULD FALSIFY IT. A forward P&L interval including zero or below on the
band. Specifically falsified if (a) the gain is concentrated in one or two
ladder-shaped series, meaning the band did not exclude nested legs, or (b) the
realised edge scales with p rather than with p*(1-1/S), meaning it is a
favourite-longshot effect wearing arithmetic as a costume.

WHERE IT IS WEAK.
  - Exclusivity is inferred from the sum alone. A 4-leg overlapping group
    summing to 1.1 passes and is simply mispriced by this rule.
  - Siblings are quoted up to 20 minutes before the market, and an uncaptured
    leg LOWERS the sum, so incomplete capture fails toward abstention. A
    staleness-inflated sum, though, pushes INTO the gate.
  - Makers may skew the overround toward longshots (the classic pattern), in
    which case the favourite is not overpriced at all and the fade loses.
  - Full-proportional removal is an assumption; the true allocation of the
    margin across legs is not known. The shift is therefore HALF the
    proportional correction, which still clears the cost only for the larger
    overrounds.
  - In-sample hypothesis, no measurements; INVARIANT #1 judges it forward.
"""
from __future__ import annotations

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "overround_leg",
    "generation": 15,
    "parent_id": "fielded_favourite",
    "created_at": "2026-10-03T10:59:19.600145+00:00",
    "rationale": (
        "In a small multi-leg event whose quoted midpoints sum to 1.06-1.25, "
        "the excess is a maker's margin spread over every leg, not a view. "
        "Remove half of the proportional share from a mid-priced leg: "
        "arithmetic on the field, not a forecast of the outcome. Narrow sum "
        "band is the guard against nested ladders, which sibling_coherence "
        "failed on."
    ),
}

EPS = 0.001

MIN_LEGS = 3          # self plus at least two quotable siblings
MAX_LEGS = 12         # larger fields are almost always ladders
MIN_SUM = 1.06        # below this the correction cannot clear the ~2pt cost
MAX_SUM = 1.25        # above this it is a ladder, not a margin
MIN_PRICE = 0.15      # a small leg's share of the margin is under a point
MAX_PRICE = 0.85
MAX_SPREAD = 0.04     # cost gate; the book must be crossable
FRACTION = 0.5        # take half the proportional correction, not all of it


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob

    if market.spread > MAX_SPREAD or not (MIN_PRICE <= p <= MAX_PRICE):
        return p

    quotable = [s for s in context.siblings if s.has_two_sided_book]
    legs = len(quotable) + 1
    if not (MIN_LEGS <= legs <= MAX_LEGS):
        return p

    total = p + sum(s.implied_prob for s in quotable)
    if not (MIN_SUM <= total <= MAX_SUM):
        return p

    fair = p / total
    return max(p - FRACTION * (p - fair), EPS)
