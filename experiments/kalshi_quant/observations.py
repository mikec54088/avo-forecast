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

# UNDECIDED, and it changes every score. Currently the last snapshot strictly
# before resolution, which is what every number reported on 2026-08-24 used.
#
# It is also the most generous choice available: a candidate forecasts with the
# maximum information the capture ever held, moments before the outcome is
# known. A fixed lead time before close_time (say 1h) would be more honest about
# what "forecasting" means and more comparable across markets, at the cost of
# discarding markets whose capture does not span that lead.
#
# Left as-is so this run stays comparable with what has already been reported.
# Revisit alongside the market_prob question -- both shape the observation set.
ENTRY_POLICY = "last_before_resolution"


@dataclass(frozen=True)
class Entry:
    """One scoreable observation: a snapshot, and what happened afterwards."""

    market: MarketSnapshot
    resolved_at: datetime
    outcome: int

    @property
    def ticker(self) -> str:
        return self.market.ticker


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
