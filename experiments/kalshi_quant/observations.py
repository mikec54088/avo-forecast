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
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from experiments.kalshi_quant.types import (
    ForecastContext,
    MarketSnapshot,
    PricePoint,
    Resolution,
)

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


MAX_PRICE_HISTORY = 24     # ~6h at the 15-minute near-pass cadence
SIBLING_TOLERANCE_MIN = 20.0


@dataclass(frozen=True)
class Entry:
    """One scoreable observation: a snapshot, and what happened afterwards."""

    market: MarketSnapshot
    resolved_at: datetime
    outcome: int
    price_history: tuple[PricePoint, ...] = ()
    siblings: tuple[MarketSnapshot, ...] = ()

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


# Columns whose values repeat across millions of rows. Read as categoricals,
# the difference is not marginal: profiled 2026-09-19 over 50,979,616 snapshot
# rows, `title` alone was 3.6 GB as object dtype and the five string columns
# together were ~9 GB of a 15.1 GB frame.
_CATEGORICAL = ("ticker", "event_ticker", "series_ticker", "title",
                "price_level_structure", "status")


def _read_snapshot(path: str, keep_tickers: set[str] | None = None,
                   keep_events: set[str] | None = None) -> pd.DataFrame:
    """One snapshot file, filtered BEFORE it joins anything else.

    Filtering per file is the whole trick. The old loader concatenated every
    snapshot ever written into one frame and then threw 72% of it away: 51.0M
    rows in, 14.2M kept. Discarding per file means the discarded rows never
    coexist, and peak memory stops tracking the size of the archive.
    """
    df = pd.read_parquet(path)
    if keep_tickers is not None:
        df = df[df["ticker"].isin(keep_tickers)]
    elif keep_events is not None:
        df = df[df["event_ticker"].isin(keep_events)]
    if df.empty:
        return df
    for c in _CATEGORICAL:
        if c in df.columns:
            df[c] = df[c].astype("category")
    # PRICES STAY float64. float32 renders 0.30 as 0.30000001192092896, and
    # every candidate's cost gate is an inequality against a price -- this
    # project already has a documented trap where `spread <= 0.04` fails on a
    # nominal four-cent book (see SPREAD_TOL in the candidates). Saving a few
    # hundred MB is not worth moving where a gate fires.
    #
    # The real bloat here was never precision, it was dtype: last_price carries
    # None for never-traded markets, so pandas stored it as OBJECT -- 1.6 GB
    # across the archive. float64 with NaN costs 8 bytes and is exact.
    for c in ("last_price", "yes_bid", "yes_ask"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    # Sizes and turnover are counts, never compared against a knife-edge
    # threshold, and float32 holds them to seven significant figures.
    for c in ("yes_bid_size", "yes_ask_size", "volume", "open_interest", "liquidity"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    return df


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    # concat of categoricals with different categories falls back to object;
    # restoring it here keeps the saving through the join.
    for c in _CATEGORICAL:
        if c in out.columns and out[c].dtype == object:
            out[c] = out[c].astype("category")
    return out


def load_entries(data_root: Path | None = None) -> list[Entry]:
    """Every market we captured with a two-sided book and later saw resolve.

    Streams the snapshot archive in two passes rather than loading it whole.
    Pass one keeps only rows for tickers that have RESOLVED -- those become the
    entries. Pass two keeps only rows in those entries' EVENTS, which is all
    `_attach_context` needs for siblings. Neither pass ever holds the archive.

    Two reads of the same files cost less than they look: the second is served
    from the page cache, and the alternative was a 15 GB frame.
    """
    root = data_root if data_root is not None else DATA_ROOT
    res_files, snap_files = _files("resolutions", root), _files("snapshots", root)
    if not res_files or not snap_files:
        return []

    res = pd.concat([pd.read_parquet(f) for f in res_files], ignore_index=True)
    res = res[res["outcome"].notna()].drop_duplicates("ticker")
    if res.empty:
        return []

    resolved = set(res["ticker"])
    snap = _concat([_read_snapshot(f, keep_tickers=resolved) for f in snap_files])
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

    entries = [
        Entry(
            market=_row_to_snapshot(r),
            resolved_at=r.resolved_at.to_pydatetime(),
            outcome=int(r.outcome),
        )
        for r in j.itertuples(index=False)
    ]
    # Siblings are read in a SECOND pass, keeping only rows in these entries'
    # events. A sibling does not need to have resolved for its quote to be
    # informative -- an unresolved leg of a mutually exclusive event is exactly
    # the leg whose price says what the market thinks of ours -- so it cannot
    # come from `snap`. It does not need the whole archive either.
    events = {e.market.event_ticker for e in entries}
    sib_snap = _concat([_read_snapshot(f, keep_events=events) for f in snap_files])
    return _attach_context(entries, snap, sib_snap)


def _attach_context(
    entries: list[Entry], snap: pd.DataFrame, all_snap: pd.DataFrame
) -> list[Entry]:
    """Attach each entry's own price path and its event siblings.

    Both are sliced as of the entry instant, and the window looks BACKWARD
    only. Price history is strictly before it -- an observation at the same
    instant is the entry itself, not history. Siblings are the nearest quote
    per ticker at or before it, within SIBLING_TOLERANCE_MIN.

    The tolerance exists because observed_at is stamped per PAGE of a sweep, so
    two legs of one event can be minutes apart despite coming from the same
    pass. An earlier version allowed the window to look forward too, for the
    same reason -- and let one sibling through that was quoted AFTER its
    market had resolved. In a mutually exclusive event that is the outcome
    itself: when one leg settles yes, the others collapse to zero. One leak in
    54,611 is still a leak, and this is the class of defect that does not
    announce itself in a score, it just looks like a discovery. Backward-only
    costs some same-sweep siblings and is worth it.

    History is capped at MAX_PRICE_HISTORY points. A candidate reasoning about
    momentum needs the recent path, not every quote since the market opened,
    and uncapped history would make the memory cost of scoring scale with how
    long capture has been running.
    """
    if not entries:
        return entries
    tickers = {e.ticker for e in entries}
    events = {e.market.event_ticker for e in entries}

    hist_src = snap[snap["ticker"].isin(tickers)][
        ["ticker", "observed_at", "yes_bid", "yes_ask", "volume", "open_interest"]
    ].sort_values("observed_at")
    by_ticker: dict[str, list] = {}
    for r in hist_src.itertuples(index=False):
        by_ticker.setdefault(r.ticker, []).append(r)

    sib_src = all_snap[all_snap["event_ticker"].isin(events)]
    by_event: dict[str, list] = {}
    for r in sib_src.itertuples(index=False):
        by_event.setdefault(r.event_ticker, []).append(r)

    tol = timedelta(minutes=SIBLING_TOLERANCE_MIN)
    out: list[Entry] = []
    for e in entries:
        at = e.market.observed_at

        past = [r for r in by_ticker.get(e.ticker, []) if r.observed_at < at]
        history = tuple(
            PricePoint(
                observed_at=r.observed_at.to_pydatetime(),
                yes_bid=float(r.yes_bid), yes_ask=float(r.yes_ask),
                volume=float(r.volume), open_interest=float(r.open_interest),
            )
            for r in past[-MAX_PRICE_HISTORY:]
        )

        nearest: dict[str, object] = {}
        for r in by_event.get(e.market.event_ticker, []):
            # Backward-only: never a quote from after the entry instant.
            if r.ticker == e.ticker or not (at - tol <= r.observed_at <= at):
                continue
            prev = nearest.get(r.ticker)
            if prev is None or r.observed_at > prev.observed_at:
                nearest[r.ticker] = r
        siblings = tuple(_row_to_snapshot(r) for r in nearest.values())

        out.append(Entry(e.market, e.resolved_at, e.outcome, history, siblings))
    return out


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
            price_history=list(entry.price_history),
            siblings=list(entry.siblings),
        )
