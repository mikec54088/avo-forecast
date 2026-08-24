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
class ForecastContext:
    """Everything a candidate may see besides the market itself.

    Deliberately NO research tool here. A research-enabled candidate is a
    different experiment (kalshi-research), not an extra field — it changes the
    cost model by orders of magnitude.
    """

    now: datetime
    series_history: dict[str, list[Resolution]] = field(default_factory=dict)
