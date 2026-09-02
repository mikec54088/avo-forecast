"""Average the two quotes in odds space, weighted by the size behind each.

The edge: `implied_prob` is a plain arithmetic midpoint, and that is wrong in
two independent ways.

  1. It ignores depth. A 1-contract ask and a 900-contract bid are treated as
     equally informative. Microstructure says the side with the queue is the
     side that has to wait, so fair value sits nearer the THIN quote.
  2. It averages in price space. Probabilities are bounded at 0 and 1 while
     odds are not, so on a wide book near a boundary the arithmetic mean is
     mechanically pushed centre-ward: a 0.01/0.09 book has a mid of 0.050,
     against an odds-space mean of 0.031.

Both corrections are the same operation once written in log-odds:

    fair = sigmoid( w_bid * logit(bid) + w_ask * logit(ask) )
    w_bid = ask_size / (bid_size + ask_size)     # crossed, as microprice is

The crossed weighting is the point: heavy resting bid size pulls the estimate
UP, toward the ask. That direction is not assumed -- it is the sign
`book_imbalance_tilt` documents measuring on this data, where a heavy bid
preceded outcomes above the mid. With balanced or missing sizes this degrades
to the geometric-odds mid, i.e. correction (2) alone, which is the case that
does most of the work.

Why this is not `baseline_sharpened` renamed. A fixed-k sharpen moves every
market away from 0.5 by the same rule, including a 1-cent-wide book where the
mid is already unbiased -- and on fresh, tight books that is measured to LOSE
money. Here the move is bounded between the bid and the ask by construction, so
it vanishes as the spread goes to zero and is large only where the quotes
genuinely disagree. Correlation with the sharpen family is expected; the
informative comparison is the fresh-and-tight subset, where this predicts
roughly zero and a fixed-k sharpen predicts a loss. If the two are
indistinguishable there, this idea is dead and the effect is just the
denominator.

Expected weak on P&L for the usual reason: the deviation is bounded by the
half-spread, which is the same quantity the fill model charges to cross. If it
posts strong skill, check the wide-book subset before believing it."""
from __future__ import annotations

import math

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

MANIFEST = {
    "candidate_id": "microprice_fair_value",
    "generation": 1,
    "parent_id": None,
    "created_at": "2026-09-01T07:00:40.847879+00:00",
    "rationale": (
        "Depth-weighted microprice computed in log-odds space instead of the "
        "50/50 arithmetic midpoint. Heavy resting bid means fair value sits "
        "nearer the ask; bounded probabilities mean the price-space average is "
        "pushed centre-ward on wide books. The result always stays inside the "
        "book, so the correction vanishes as the spread does."
    ),
}

EPS = 0.001


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def _weights(market: MarketSnapshot) -> tuple[float, float]:
    """Crossed size weights (bid, ask), falling back to 50/50 without depth."""
    bid_size = market.yes_bid_size
    ask_size = market.yes_ask_size
    if bid_size is None or ask_size is None:
        return 0.5, 0.5
    total = bid_size + ask_size
    if total <= 0.0:
        return 0.5, 0.5
    return ask_size / total, bid_size / total


def forecast(market: MarketSnapshot, context: ForecastContext) -> float:
    p = market.implied_prob
    if not market.has_two_sided_book:
        return p                      # no real book to average

    bid = min(max(market.yes_bid, EPS), 1.0 - EPS)
    ask = min(max(market.yes_ask, EPS), 1.0 - EPS)
    if bid >= ask:                    # both quotes inside the same clamp
        return p

    w_bid, w_ask = _weights(market)
    fair = 1.0 / (1.0 + math.exp(-(w_bid * _logit(bid) + w_ask * _logit(ask))))
    return min(max(fair, EPS), 1.0 - EPS)
