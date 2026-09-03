"""Domain types for kalshi-quant. These do NOT belong in core.

Field names and units VERIFIED against the live API on 2026-08-23 by paginating
the full `status=open` universe (961,804 markets). Findings that drove this file:

- There is no `yes_bid` / `volume` / `open_interest` integer field. Not on any
  market. The API returns only the `_dollars` / `_fp` variants, and every one of
  them is a **decimal string**: `"0.9160"`, `"31452520.19"`. The old int schema
  could never have parsed a live response.
- Prices are **dollars in [0, 1]**, i.e. already probability units. They are not
  cents. Granularity is per-market (`price_level_structure`): `linear_cent`
  (0.01), `deci_cent` (0.0010), `tapered_deci_cent`. So prices are not integers
  under any scaling and must be float.
- Sizes and volumes are fractional (`"772.09"` contracts), so those are float too.
- `series_ticker` is not returned at all. It is derived from the event ticker.
- `liquidity_dollars` is present but is 0.0 on every market ever observed. See
  the note on the field below before writing anything that depends on it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class MarketSnapshot:
    ticker: str
    event_ticker: str
    series_ticker: str
    title: str
    observed_at: datetime
    close_time: datetime
    yes_bid: float          # dollars in [0, 1] == probability units
    yes_ask: float
    last_price: float | None  # None when the market has never traded
    volume: float           # contracts, fractional
    open_interest: float
    yes_bid_size: float | None = None
    yes_ask_size: float | None = None
    # ALWAYS 0.0. The API returns liquidity_dollars on every market and it is
    # zero on every one of them: 610,077 rows across 40 snapshot files on
    # 2026-09-01, not a single non-zero value, while open_interest is populated
    # on ~57% of the same rows. Parsed and stored anyway in case Kalshi starts
    # filling it in, but a candidate that gates on liquidity silently becomes a
    # constant -- it will pass validation, score exactly 0, and look like a
    # failed idea rather than a dead field. Use volume or open_interest.
    liquidity: float = 0.0
    status: str = ""
    price_level_structure: str = ""
    is_mve: bool = False    # auto-generated multivariate (parlay) combo market
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def implied_prob(self) -> float:
        """Midpoint. Divisor is 2.0, not 200.0: the inputs are already dollars."""
        return (self.yes_bid + self.yes_ask) / 2.0

    @property
    def spread(self) -> float:
        """Dollars."""
        return self.yes_ask - self.yes_bid

    # Cents-denominated views. simulate_fill() and the fee schedule are written
    # in cents; these keep that code honest now that storage is in dollars.
    @property
    def yes_bid_cents(self) -> float:
        return self.yes_bid * 100.0

    @property
    def yes_ask_cents(self) -> float:
        return self.yes_ask * 100.0

    @property
    def spread_cents(self) -> float:
        return self.spread * 100.0

    @property
    def has_two_sided_book(self) -> bool:
        """A resting bid and ask on both sides.

        INVARIANT #2 scores against the market's implied probability, so a market
        with no book has nothing to score against — `implied_prob` on a 0.0/0.0
        or 0.0/1.0 book is an artifact, not a market belief.
        """
        return 0.0 < self.yes_bid < self.yes_ask < 1.0


@dataclass(frozen=True)
class Resolution:
    ticker: str
    resolved_at: datetime
    outcome: int  # 1 = yes, 0 = no


@dataclass(frozen=True)
class PricePoint:
    """One earlier observation of the same market.

    Deliberately thinner than MarketSnapshot: a candidate reasoning about the
    path needs the quote and the turnover, not the title and the tick
    structure, and a full snapshot per history point would multiply the memory
    cost of scoring by the history depth for no gain.
    """

    observed_at: datetime
    yes_bid: float
    yes_ask: float
    volume: float
    open_interest: float

    @property
    def implied_prob(self) -> float:
        return (self.yes_bid + self.yes_ask) / 2.0

    @property
    def spread(self) -> float:
        return self.yes_ask - self.yes_bid


@dataclass(frozen=True)
class ForecastContext:
    """Everything a candidate may see besides the market itself.

    Deliberately NO research tool here. A research-enabled candidate is a
    different experiment (kalshi-research), not an extra field — it changes the
    cost model by orders of magnitude.

    Every field is sliced as of `now`. Nothing here may contain information
    that did not exist at the entry instant; that slicing is enforced in
    observations.py and tested, because a leak would be invisible in the score
    and would simply look like a discovery.
    """

    now: datetime

    # Resolutions of OTHER markets in the same series, known before `now`.
    series_history: dict[str, list[Resolution]] = field(default_factory=dict)

    # This market's own earlier quotes, oldest first, strictly before `now`.
    #
    # Added 2026-09-02 (G1). Generation 1 scored a clean null, and every one of
    # its 24 candidates was arithmetic on a single photograph of the book: a
    # candidate saw 0.42 with no way to know whether that had drifted down from
    # 0.70 or up from 0.15. No momentum, no volatility, no trend. The snapshots
    # were already on disk; they were simply never passed in. Before concluding
    # the market is efficient, it is worth testing candidates that are not blind.
    price_history: list[PricePoint] = field(default_factory=list)

    # Other markets in the same EVENT, quoted at about the same instant.
    #
    # Added 2026-09-02 (G1). Kalshi events carry several legs, and mutually
    # exclusive ones should price to about 1.0 in total. When they do not, that
    # is a real signal sitting in data already captured. Note this is
    # within-event structure, not cross-venue arbitrage, which INVARIANT #6
    # rules out for being too easy to find and not the research question.
    siblings: list[MarketSnapshot] = field(default_factory=list)

    @property
    def sibling_sum(self) -> float:
        """Total implied probability across this market's quotable siblings.

        Meaningful only where the event is mutually exclusive and every leg is
        present; a candidate using it should check `len(siblings)` first, since
        a partial set sums low for a boring reason.
        """
        return sum(s.implied_prob for s in self.siblings if s.has_two_sided_book)
