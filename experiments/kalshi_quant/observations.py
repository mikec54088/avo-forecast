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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cached_property
from pathlib import Path

import pandas as pd

from experiments.kalshi_quant.types import (
    ForecastContext,
    MarketSnapshot,
    PricePoint,
    Resolution,
)

DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "kalshi_quant"

# DECIDED 2026-09-28 (G2, human-approved): the entry is the FIRST quote a
# candidate acts on, chosen walking forward through time. It replaces
# "last_before_resolution_within_60min" (2026-08-31), which picked each market's
# last quote before the outcome was known -- a moment you can only identify in
# hindsight. That leaked the answer through timing: a market that settles
# EARLY on YES (a prop, a total, "both teams score") was caught at a moderate
# price just before its sudden YES. unclimbed_favourite read +0.0472/contract
# under it and +0.0017 on the same 1,443 markets entered at its first signal;
# paper trading, which cannot see the future, read -0.0026. See CLAUDE.md;
# scripts/leak_check.py runs the same checks on any candidate.
#
# The rule, which is the paper runner's rule replayed over the archive:
#
#   * A DECISION QUOTE is any captured quote with a two-sided book, not a
#     parlay, observed before the market resolved, and at most
#     DECISION_HORIZON_HOURS before its scheduled close. Everything in that
#     test was knowable at the instant of the quote. (Before resolution is
#     knowable too: a live trader sees the market still open.)
#   * The POOL is every resolved market with at least one decision quote.
#     Nothing about what happened later -- whether the book stayed two-sided,
#     how close to the end it was quoted -- decides membership. The old pool
#     required a quote within 60 min of resolution, and winning favourites'
#     books often go one-sided before that, so the pool itself was skewed.
#   * A candidate is asked at each decision quote in time order. The first
#     quote where its forecast differs from the market's is its entry, scored
#     and filled there, and nothing later counts. A candidate that never
#     differs is scored at the market's first decision quote.
#
# Staleness is no longer capped. It was capped because a stale quote inflated
# skill; under a forward walk the quote is simply the one available when the
# candidate decided, which is what trading it would have faced.
#
# Changing this rescores everything, so it is G2.
ENTRY_POLICY = "first_signal_within_72h_of_close"
DECISION_HORIZON_HOURS = 72.0     # the paper runner trades up to 72h from close


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


# A forecast this close to the market is agreement, not a position. One
# threshold for the replay, the fill rule and the paper runner.
ACT_EPS = 1e-12


def acts(p: float, mid: float) -> bool:
    """Does this forecast take a position? The ONE definition."""
    return abs(p - mid) > ACT_EPS


def is_decision_quote(m: MarketSnapshot) -> bool:
    """Could a live trader have acted on this quote? The ONE definition, used
    by the replay here and by the paper runner, so the two cannot drift.
    Resolution is checked by the caller: live, an open market has not resolved.
    """
    hours = (m.close_time - m.observed_at).total_seconds() / 3600.0
    return m.has_two_sided_book and not m.is_mve and 0.0 < hours <= DECISION_HORIZON_HOURS


@dataclass(frozen=True)
class MarketPath:
    """One resolved market as a live trader would have met it: every decision
    quote in time order, plus what is needed to build each one's context.

    `quotes` is every captured quote of this market before it resolved (the
    source of price_history). `event_quotes` is every captured quote in its
    event, sorted by time and shared by all markets of the event (the source
    of siblings). Contexts are built per decision, on demand, because holding
    ~20 decisions x 24 history points per market would not fit in memory.
    """

    resolved_at: datetime
    outcome: int
    decisions: tuple[MarketSnapshot, ...]
    quotes: tuple[PricePoint, ...] = ()
    event_quotes: tuple[MarketSnapshot, ...] = ()

    @property
    def ticker(self) -> str:
        return self.decisions[0].ticker

    @property
    def market(self) -> MarketSnapshot:
        """The first decision quote: where a market is described or scored when
        no candidate acted on it. Fixed in advance, never chosen by outcome."""
        return self.decisions[0]

    @cached_property
    def _quote_times(self) -> list[datetime]:
        return [q.observed_at for q in self.quotes]

    @cached_property
    def _event_times(self) -> list[datetime]:
        return [q.observed_at for q in self.event_quotes]

    def entry_at(self, i: int) -> Entry:
        """Decision i with the context a live trader had at that instant:
        history strictly before it, siblings at or before it (never after --
        an in-event quote from after the decision can carry the outcome)."""
        m = self.decisions[i]
        history, siblings = context_parts(m, self.quotes, self._quote_times,
                                          self.event_quotes, self._event_times)
        return Entry(m, self.resolved_at, self.outcome, history, siblings)


def context_parts(m: MarketSnapshot, quotes: Sequence[PricePoint], quote_times: list[datetime],
                  event_quotes: Sequence[MarketSnapshot], event_times: list[datetime],
                  ) -> tuple[tuple[PricePoint, ...], tuple[MarketSnapshot, ...]]:
    """(price_history, siblings) for a decision at `m`, from time-sorted quotes.

    THE one implementation, shared by the replay and the paper runner, so a
    candidate sees the same context in both. History is strictly before the
    decision and capped at MAX_PRICE_HISTORY. Siblings are the latest quote
    per other ticker in [decision - SIBLING_TOLERANCE_MIN, decision] -- never
    after it: an in-event quote from after the decision can carry the outcome.
    """
    at = m.observed_at
    cut = bisect.bisect_left(quote_times, at)
    history = tuple(quotes[max(0, cut - MAX_PRICE_HISTORY):cut])
    tol = timedelta(minutes=SIBLING_TOLERANCE_MIN)
    lo = bisect.bisect_left(event_times, at - tol)
    hi = bisect.bisect_right(event_times, at)
    nearest: dict[str, MarketSnapshot] = {}
    for q in event_quotes[lo:hi]:
        if q.ticker != m.ticker:
            nearest[q.ticker] = q          # time-sorted: the last one wins
    return history, tuple(nearest.values())


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
    # A sweep that found nothing quotable writes a COLUMN-LESS parquet: capture
    # builds its frame with pd.DataFrame(rows), and an empty rows list has no
    # columns to infer. Two such files exist from 2026-09-13. The old loader
    # never noticed, because it concatenated everything first and filtered the
    # combined frame; filtering per file walks straight into the missing column.
    if df.empty or "ticker" not in df.columns:
        return pd.DataFrame()
    if keep_tickers is not None:
        df = df[df["ticker"].isin(keep_tickers)]
    elif keep_events is not None and "event_ticker" in df.columns:
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


def _decision_mask(df: pd.DataFrame) -> pd.Series:
    """is_decision_quote, vectorised over snapshot rows joined to resolved_at.
    Kept in step with is_decision_quote by test_the_two_decision_rules_agree."""
    obs = pd.to_datetime(df["observed_at"], utc=True)
    hours = (pd.to_datetime(df["close_time"], utc=True) - obs).dt.total_seconds() / 3600.0
    return ((df["yes_bid"] > 0) & (df["yes_ask"] > df["yes_bid"]) & (df["yes_ask"] < 1)
            & ~df["is_mve"].astype(bool) & (hours > 0) & (hours <= DECISION_HORIZON_HOURS)
            & (obs < pd.to_datetime(df["resolved_at"], utc=True)))


QUOTE_COLS = ["ticker", "observed_at", "yes_bid", "yes_ask", "volume", "open_interest"]


def path_frames(snap: pd.DataFrame, res: pd.DataFrame, ev_snap: pd.DataFrame
                ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(decisions, quotes, event rows) for every market in the pool.

    `snap` holds rows of resolved tickers, `ev_snap` rows of their events.
    Event rows are trimmed to the span a sibling lookup can reach -- from
    SIBLING_TOLERANCE_MIN before an event's first decision to its last.
    """
    empty = pd.DataFrame()
    if snap.empty or res.empty:
        return empty, empty, empty
    j = snap.merge(res[["ticker", "resolved_at", "outcome"]], on="ticker", how="inner")
    j = j[pd.to_datetime(j["observed_at"], utc=True) < pd.to_datetime(j["resolved_at"], utc=True)]
    dec = j[_decision_mask(j)] if not j.empty else j
    if dec.empty:
        return empty, empty, empty
    pool = set(dec["ticker"])
    quotes = j[j["ticker"].isin(pool)][QUOTE_COLS]
    if ev_snap.empty:
        return dec, quotes, empty
    ev = ev_snap[ev_snap["event_ticker"].isin(set(dec["event_ticker"]))]
    span = dec.groupby("event_ticker", observed=True)["observed_at"].agg(["min", "max"])
    lo = ev["event_ticker"].map(span["min"]) - timedelta(minutes=SIBLING_TOLERANCE_MIN)
    hi = ev["event_ticker"].map(span["max"])
    obs = ev["observed_at"]
    ev = ev[(obs >= lo) & (obs <= hi)]
    return dec, quotes, ev


def paths_from_frames(dec: pd.DataFrame, quotes: pd.DataFrame,
                      ev: pd.DataFrame) -> list[MarketPath]:
    """MarketPaths from the three frames path_frames returns (or the store
    holds). Event quote lists are built once per event and shared."""
    if dec.empty:
        return []
    by_q: dict[str, list[PricePoint]] = {}
    if not quotes.empty:
        for r in quotes.sort_values("observed_at").itertuples(index=False):
            by_q.setdefault(str(r.ticker), []).append(PricePoint(
                r.observed_at.to_pydatetime(), float(r.yes_bid), float(r.yes_ask),
                float(r.volume), float(r.open_interest)))
    # One pass per frame: itertuples per groupby group costs ~50x more.
    ev_lists: dict[str, list[MarketSnapshot]] = {}
    if not ev.empty:
        for r in ev.sort_values("observed_at").itertuples(index=False):
            ev_lists.setdefault(str(r.event_ticker), []).append(_row_to_snapshot(r))
    by_ev = {k: tuple(v) for k, v in ev_lists.items()}
    decs: dict[str, list[MarketSnapshot]] = {}
    meta: dict[str, tuple[datetime, int]] = {}
    for r in dec.sort_values("observed_at").itertuples(index=False):
        t = str(r.ticker)
        if t not in decs:
            decs[t] = []
            meta[t] = (pd.Timestamp(r.resolved_at).to_pydatetime(), int(r.outcome))
        decs[t].append(_row_to_snapshot(r))
    return [MarketPath(resolved_at=meta[t][0], outcome=meta[t][1], decisions=tuple(d),
                       quotes=tuple(by_q.get(t, ())),
                       event_quotes=by_ev.get(d[0].event_ticker, ()))
            for t, d in decs.items()]


def read_resolutions(root: Path) -> pd.DataFrame:
    files = _files("resolutions", root)
    if not files:
        return pd.DataFrame(columns=["ticker", "series_ticker", "resolved_at", "outcome"])
    res = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    return res[res["outcome"].notna()].drop_duplicates("ticker")


def load_paths(data_root: Path | None = None,
               only_tickers: set[str] | None = None,
               snapshot_files: list[str] | None = None) -> list[MarketPath]:
    """Every resolved market in the pool, from the raw archive (ENTRY_POLICY).

    Two streaming passes, as before: rows of resolved tickers (decisions and
    price history), then rows of their events (siblings). Neither pass holds
    the archive.
    """
    root = data_root if data_root is not None else DATA_ROOT
    res = read_resolutions(root)
    snap_files = snapshot_files if snapshot_files is not None else _files("snapshots", root)
    if only_tickers is not None:
        res = res[res["ticker"].isin(only_tickers)]
    if res.empty or not snap_files:
        return []
    resolved = set(res["ticker"])
    snap = _concat([_read_snapshot(f, keep_tickers=resolved) for f in snap_files])
    if snap.empty:
        return []
    events = set(snap["event_ticker"].astype(str))
    ev_snap = _concat([_read_snapshot(f, keep_events=events) for f in snap_files])
    return paths_from_frames(*path_frames(snap, res, ev_snap))


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
        self._index()

    def _index(self) -> None:
        for v in self._by_series.values():
            v.sort(key=lambda pair: pair[0])
        self._keys = {k: [t for t, _ in v] for k, v in self._by_series.items()}
        self._cache: dict[tuple[str, int], list[Resolution]] = {}

    @classmethod
    def from_resolutions(cls, res: pd.DataFrame) -> SeriesHistory:
        """From EVERY recorded resolution, not only markets in the scoring
        pool. The pool is a scoring choice; what had resolved in a series by
        a given instant is a fact, and a candidate is entitled to all of it."""
        sh = cls([])
        for r in res.itertuples(index=False):
            t = pd.Timestamp(r.resolved_at).to_pydatetime()
            sh._by_series.setdefault(str(r.series_ticker), []).append(
                (t, Resolution(str(r.ticker), t, int(r.outcome))))
        sh._index()
        return sh

    def as_of(self, series: str, when: datetime) -> list[Resolution]:
        """Resolutions in `series` known strictly before `when`."""
        pairs = self._by_series.get(series)
        if not pairs:
            return []
        cut = bisect.bisect_left(self._keys[series], when)
        # A forward walk asks for the same slice many times; share one list.
        key = (series, cut)
        hit = self._cache.get(key)
        if hit is None:
            if len(self._cache) > 4096:
                self._cache.clear()
            hit = self._cache[key] = [r for _, r in pairs[:cut]]
        return hit

    def context_for(self, entry: Entry) -> ForecastContext:
        return self.context(entry.market, entry.price_history, entry.siblings)

    def context(self, m: MarketSnapshot, price_history: Sequence[PricePoint],
                siblings: Sequence[MarketSnapshot]) -> ForecastContext:
        """The ForecastContext for a decision at `m` -- used by the replay AND
        the paper runner, so a candidate sees the same thing in both."""
        return ForecastContext(
            now=m.observed_at,
            series_history={m.series_ticker: self.as_of(m.series_ticker, m.observed_at)},
            price_history=list(price_history),
            siblings=list(siblings),
        )


def first_signal(path: MarketPath, forecast: Callable[[MarketSnapshot, ForecastContext], float],
                 history: SeriesHistory) -> tuple[Entry, float]:
    """ENTRY_POLICY: walk the decision quotes forward and stop at the first one
    where the candidate disagrees with the market. Returns that entry and the
    forecast; if it never disagrees, the first decision and its forecast.

    Raises whatever the candidate raises, and ValueError for a forecast outside
    [0, 1] (including NaN), so the caller counts the market as an error rather
    than scoring a partial walk.
    """
    first: tuple[Entry, float] | None = None
    for i in range(len(path.decisions)):
        e = path.entry_at(i)
        p = float(forecast(e.market, history.context_for(e)))
        if not (0.0 <= p <= 1.0):
            raise ValueError(f"forecast {p!r} outside [0,1]")
        if first is None:
            first = (e, p)
        if acts(p, e.market.implied_prob):
            return e, p
    assert first is not None      # a path has at least one decision
    return first
