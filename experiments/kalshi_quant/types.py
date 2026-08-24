"""Domain types for kalshi-quant. These do NOT belong in core.

NOTE: field names were written against public Kalshi docs and have NOT been
verified against a live response. Confirm before trusting.
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
    yes_bid: int            # cents, 1-99
    yes_ask: int
    last_price: int | None
    volume: int
    open_interest: int
    yes_bid_size: int | None = None
    yes_ask_size: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def implied_prob(self) -> float:
        return (self.yes_bid + self.yes_ask) / 200.0

    @property
    def spread_cents(self) -> int:
        return self.yes_ask - self.yes_bid


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
