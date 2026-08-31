"""Build scoreable observations by joining captured snapshots to resolutions.

Domain-specific on purpose: `core/` must never learn what a snapshot is.

An observation pairs one entry snapshot (the price a candidate forecasts
against) with the outcome that market later settled to. Two things here are easy
to get subtly wrong, and both would inflate every score:

1. **Entry selection.** See ENTRY_POLICY below. Which snapshot counts as the
   entry decides how much information a candidate is handed.
2. **Context leakage.** `ForecastContext.series_history` must contain only
   resolutions that were already known at the entry instant. Handing a candidate
   the outcome it is being asked to predict -- or any outcome from after its
   entry -- is lookahead, and `baseline_base_rate` would quietly exploit it.
"""
from __future__ import annotations

import bisect
import glob
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot, Resolution

DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "kalshi_quant"

# DECIDED 2026-08-31 (G2, approved). The entry is the last snapshot strictly
# before resolution, and it must be no older than MAX_ENTRY_STALENESS_MINUTES.
#
# Without the cap the observation set silently mixed a 20-minute-old price with
# a two-day-old one: median staleness 77 min, p90 603, p99 1,913. That is not a
# capture defect and no cadence change fixes it -- for 100% of stale entries
# sampled, the entry IS the last time that market had a two-sided book. Books go
# one-sided approaching close and do not come back.
#
# It had to be capped because staleness inflates Brier skill without inflating
# P&L, so the primary fitness was the contaminated one. A stale price has not
# absorbed the drift toward the eventual outcome, and any transform sharpening
# away from 0.5 recovers part of that drift and is paid for it. Split by
# staleness, a mild logit sharpen scored +0.0011 (CI includes zero) on fresh
# tight books against +0.0762 on stale wide ones -- and made money in neither.
#
# 60 rather than 20 minutes: the <=20 band is the genuinely uncertain markets
# (market Brier 0.154 against 0.089 for stale ones) and is where that sharpen
# actually LOSES money (-0.0075/contract, CI excludes zero). Capping there would
# select for the hardest markets while cutting the sample 77%. At 60 min the set
# retains 35,201 of 79,113 observations (44.5%).
#
# Changing this rescores everything, so it is G2. Revisit only with the
# market_prob question, since both reshape the observation set.
ENTRY_POLICY = "last_before_resolution_within_60min"
MAX_ENTRY_STALENESS_MINUTES = 60.0


@dataclass(frozen=True)
class Entry:
    """One scoreable observation: a snapshot, and what happened afterwards."""

    market: MarketSnapshot
    resolved_at: datetime
    outcome: int

    @property
    def ticker(self) -> str:
        return self.market.ticker

    @property
    def staleness_minutes(self) -> float:
        """Minutes between the entry snapshot and the outcome becoming known.

        Measured 2026-08-31 over 77,966 observations: median 77 min, p90 603,
        p99 1,913. It is NOT a capture defect -- for 100% of stale entries
        sampled, the entry IS the last time that market had a two-sided book,
        so no cadence change produces a fresher price. Books go one-sided near
        close and never come back.

        It matters because it inflates Brier skill without inflating P&L. A
        stale price has not yet absorbed the drift toward the eventual outcome,
        so any transform that sharpens away from 0.5 recovers part of that
        drift and scores well for it. Split by staleness, a mild logit sharpen
        scored +0.0011 (CI includes zero) on fresh tight books and +0.0762 on
        stale wide ones -- and made no money in either.
        """
        return (self.resolved_at - self.market.observed_at).total_seconds() / 60.0


def _files(kind: str, root: Path) -> list[str]:
    return sorted(glob.glob(str(root / kind / "date=*" / "*.parquet")))


def _row_to_snapshot(r) -> MarketSnapshot:
    return MarketSnapshot(
        ticker=r.ticker,
        event_ticker=r.event_ticker,
        series_ticker=r.series_ticker,
        title=r.title,
        observed_at=r.observed_at.to_pydatetime(),
        close_time=r.close_time.to_pydatetime(),
        yes_bid=float(r.yes_bid),
        yes_ask=float(r.yes_ask),
        last_price=None if pd.isna(r.last_price) else float(r.last_price),
        volume=float(r.volume),
        open_interest=float(r.open_interest),
        yes_bid_size=None if pd.isna(r.yes_bid_size) else float(r.yes_bid_size),
        yes_ask_size=None if pd.isna(r.yes_ask_size) else float(r.yes_ask_size),
        liquidity=float(r.liquidity),
        status=r.status,
        price_level_structure=r.price_level_structure,
        is_mve=bool(r.is_mve),
    )


def load_entries(data_root: Path | None = None) -> list[Entry]:
    """Every market we captured with a two-sided book and later saw resolve."""
    root = data_root if data_root is not None else DATA_ROOT
    res_files, snap_files = _files("resolutions", root), _files("snapshots", root)
    if not res_files or not snap_files:
        return []

    res = pd.concat([pd.read_parquet(f) for f in res_files], ignore_index=True)
    res = res[res["outcome"].notna()].drop_duplicates("ticker")
    if res.empty:
        return []

    snap = pd.concat([pd.read_parquet(f) for f in snap_files], ignore_index=True)
    snap = snap[snap["ticker"].isin(set(res["ticker"]))]
    if snap.empty:
        return []

    j = snap.merge(res[["ticker", "resolved_at", "outcome"]], on="ticker", how="inner")
    # ENTRY_POLICY: last observation strictly before the outcome was known.
    j = j[j["observed_at"] < j["resolved_at"]]
    if j.empty:
        return []
    j = j.sort_values("observed_at").groupby("ticker", as_index=False).last()

    # ENTRY_POLICY staleness cap. Applied after picking the last snapshot, not
    # before: an entry is only meaningful if it is BOTH the freshest price we
    # hold and fresh in absolute terms.
    resolved = pd.to_datetime(j["resolved_at"], utc=True)
    observed = pd.to_datetime(j["observed_at"], utc=True)
    stale_min = (resolved - observed).dt.total_seconds() / 60.0
    j = j[stale_min <= MAX_ENTRY_STALENESS_MINUTES]
    if j.empty:
        return []

    return [
        Entry(
            market=_row_to_snapshot(r),
            resolved_at=r.resolved_at.to_pydatetime(),
            outcome=int(r.outcome),
        )
        for r in j.itertuples(index=False)
    ]


class SeriesHistory:
    """Resolutions per series, queryable as of an instant.

    Built once and sliced per observation. The slice is the point: a candidate
    may see only what had already resolved when it was looking at the market.
    """

    def __init__(self, entries: list[Entry]) -> None:
        self._by_series: dict[str, list[tuple[datetime, Resolution]]] = {}
        for e in entries:
            self._by_series.setdefault(e.market.series_ticker, []).append(
                (e.resolved_at, Resolution(e.ticker, e.resolved_at, e.outcome))
            )
        for v in self._by_series.values():
            v.sort(key=lambda pair: pair[0])
        self._keys = {k: [t for t, _ in v] for k, v in self._by_series.items()}

    def as_of(self, series: str, when: datetime) -> list[Resolution]:
        """Resolutions in `series` known strictly before `when`."""
        pairs = self._by_series.get(series)
        if not pairs:
            return []
        cut = bisect.bisect_left(self._keys[series], when)
        return [r for _, r in pairs[:cut]]

    def context_for(self, entry: Entry) -> ForecastContext:
        return ForecastContext(
            now=entry.market.observed_at,
            series_history={
                entry.market.series_ticker: self.as_of(
                    entry.market.series_ticker, entry.market.observed_at
                )
            },
        )
