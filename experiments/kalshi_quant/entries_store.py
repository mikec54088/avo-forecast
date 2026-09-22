"""Materialised entries: the curated half of raw -> curated.

`load_entries()` rebuilds its answer from the raw snapshot archive on every
call. That archive is 60x larger than the answer and grows forever, while the
answer does not change: once a market has resolved, the snapshot it entered on,
its outcome and its price path are fixed for good. Measured 2026-09-19:

    scanned to build it    ~51,000,000 rows   (2.0 GB on disk)
    rows in the answer      ~4,349,744 rows   (8.5% of what is read)
    peak RSS                     6.36 GB  ->  ~106 MB materialised

So entries are built ONCE, appended as new resolutions arrive, and read back
directly. The raw snapshots are untouched and remain the source of truth; this
table is derived and can be deleted at any time.

THE DANGER, and why the fingerprint exists. A cache of derived data goes
silently wrong when the logic that derived it changes -- and this project has
been bitten twice by exactly that (a stamped `created_at` that was not stamped,
a fee constant that was a placeholder). Every row here depends on
ENTRY_POLICY, MAX_ENTRY_STALENESS_MINUTES, MAX_PRICE_HISTORY and
SIBLING_TOLERANCE_MIN. The table records the values that produced it and
REFUSES to serve rows built under different ones, so changing a policy
constant forces a rebuild instead of quietly returning stale entries.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from experiments.kalshi_quant.observations import (
    DATA_ROOT,
    ENTRY_POLICY,
    MAX_ENTRY_STALENESS_MINUTES,
    MAX_PRICE_HISTORY,
    SIBLING_TOLERANCE_MIN,
    Entry,
    _files,
    _row_to_snapshot,
    load_entries,
)
from experiments.kalshi_quant.types import PricePoint

STORE = DATA_ROOT / "entries"
META = STORE / "meta.json"

# How far back to look for snapshots of a newly resolved market. Its entry must
# be within MAX_ENTRY_STALENESS_MINUTES of resolution, and MAX_PRICE_HISTORY
# points at the 15-minute near cadence span 6 hours -- but the full pass is
# hourly and a market may be quoted for days, so a generous window costs little
# and a short one would silently truncate history.
LOOKBACK_DAYS = 4.0


def policy() -> dict[str, object]:
    return {
        "entry_policy": ENTRY_POLICY,
        "max_entry_staleness_minutes": MAX_ENTRY_STALENESS_MINUTES,
        "max_price_history": MAX_PRICE_HISTORY,
        "sibling_tolerance_min": SIBLING_TOLERANCE_MIN,
    }


def _write_meta(store: Path) -> None:
    store.mkdir(parents=True, exist_ok=True)
    (store / "meta.json").write_text(json.dumps(
        {"policy": policy(), "built_at": datetime.now(timezone.utc).isoformat()}, indent=1))


def _entry_count(root: Path) -> int:
    import glob
    files = glob.glob(str(root / "entries" / "entries" / "date=*" / "*.parquet"))
    return sum(len(pd.read_parquet(f, columns=["ticker"])) for f in files)


@dataclass(frozen=True)
class StoreStats:
    added: int
    total: int
    rebuilt: bool
    note: str = ""


def _meta_ok(root: Path) -> bool:
    path = root / "entries" / "meta.json"
    if not path.exists():
        return False
    try:
        return json.loads(path.read_text()).get("policy") == policy()
    except (OSError, ValueError):
        return False


def freshness(data_root: Path | None = None) -> tuple[str | None, str | None]:
    """(newest entries partition, newest resolutions partition), as date=YYYY-MM-DD.

    _meta_ok() checks only that the POLICY matches, so a store built weeks ago
    loads happily and every caller scores against frozen data. Measured
    2026-09-22: the table was built 2026-09-20T01:49Z and never rebuilt, while
    capture kept writing resolutions through 09-22. Three candidates written
    after that timestamp had zero eligible observations and would have had zero
    forever -- INVARIANT #1 scores a candidate only on ground truth resolving
    after it was created, and no such ground truth had been ingested. The
    cadence gate meanwhile compared a static dataset against itself across
    gen006, gen007 and gen008: identical 157,108 observations, identical fill
    counts, and three "no verdict has moved" skips that were arithmetic rather
    than evidence.

    Returns None for either side that does not exist yet.
    """
    from datetime import date
    root = data_root if data_root is not None else DATA_ROOT

    def newest(d: Path) -> str | None:
        if not d.is_dir():
            return None
        # Only names that actually parse as a date. Sorting the raw names would
        # let one stray directory become the maximum -- "date=tmp" sorts above
        # every real partition -- and silently disable the staleness guard,
        # which is the same class of quiet failure the guard exists to catch.
        parts = []
        for x in d.iterdir():
            if not x.is_dir() or not x.name.startswith("date="):
                continue
            try:
                date.fromisoformat(x.name.removeprefix("date="))
            except ValueError:
                continue
            parts.append(x.name)
        return max(parts) if parts else None

    return newest(root / "entries" / "entries"), newest(root / "resolutions")


def stale_by_days(data_root: Path | None = None) -> float:
    """How many days of captured resolutions the entries table has not ingested.

    0.0 when current or when there is nothing to compare against. Deliberately
    a number rather than a bool: the caller decides the tolerance, because the
    newest resolution partition is still being written during the day and one
    day behind is normal rather than broken.
    """
    from datetime import date
    ent, res = freshness(data_root)
    if ent is None or res is None:
        return 0.0
    try:
        a = date.fromisoformat(ent.removeprefix("date="))
        b = date.fromisoformat(res.removeprefix("date="))
    except ValueError:
        return 0.0
    return max(0.0, (b - a).days)


def _processed_tickers(root: Path) -> set[str]:
    """Every resolved ticker this store has already CONSIDERED.

    Deliberately not "every ticker that produced an entry". Measured
    2026-09-19: 365,648 resolutions yield 155,348 entries, so 210,300 resolved
    markets never produce one -- their freshest snapshot is over the staleness
    cap, or we never captured them with a two-sided book at all. Keying off
    entries alone would re-scan that growing majority on every build, and the
    "nothing to do" path would never be reached.
    """
    import glob
    files = glob.glob(str(root / "entries" / "processed" / "*.parquet"))
    if not files:
        return set()
    return set(pd.concat([pd.read_parquet(f, columns=["ticker"]) for f in files],
                         ignore_index=True)["ticker"])


def _write(root: Path, kind: str, df: pd.DataFrame, tag: str) -> None:
    if df.empty:
        return
    out = root / "entries" / kind / f"date={tag}"
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / f"{datetime.now(timezone.utc):%H%M%S%f}.parquet", index=False)


_MARKET_COLS = ("ticker", "event_ticker", "series_ticker", "title", "observed_at",
                "close_time", "yes_bid", "yes_ask", "last_price", "volume",
                "open_interest", "yes_bid_size", "yes_ask_size", "liquidity",
                "status", "price_level_structure", "is_mve")


def _market_row(m, **extra) -> dict[str, object]:
    d = {c: getattr(m, c) for c in _MARKET_COLS}
    d.update(extra)
    return d


def _to_frames(entries: list[Entry]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ent, hist, sib = [], [], []
    for e in entries:
        ent.append(_market_row(e.market, resolved_at=e.resolved_at, outcome=e.outcome))
        for h in e.price_history:
            hist.append({"ticker": e.ticker, "observed_at": h.observed_at,
                         "yes_bid": h.yes_bid, "yes_ask": h.yes_ask,
                         "volume": h.volume, "open_interest": h.open_interest})
        for s in e.siblings:
            sib.append(_market_row(s, entry_ticker=e.ticker))
    return pd.DataFrame(ent), pd.DataFrame(hist), pd.DataFrame(sib)


def _from_frames(ent: pd.DataFrame, hist: pd.DataFrame, sib: pd.DataFrame) -> list[Entry]:
    by_hist: dict[str, list] = {}
    if not hist.empty:
        for r in hist.sort_values("observed_at").itertuples(index=False):
            by_hist.setdefault(r.ticker, []).append(
                PricePoint(r.observed_at.to_pydatetime(), float(r.yes_bid),
                           float(r.yes_ask), float(r.volume), float(r.open_interest)))
    by_sib: dict[str, list] = {}
    if not sib.empty:
        for r in sib.itertuples(index=False):
            by_sib.setdefault(r.entry_ticker, []).append(_row_to_snapshot(r))
    out = []
    for r in ent.itertuples(index=False):
        out.append(Entry(
            market=_row_to_snapshot(r),
            resolved_at=r.resolved_at.to_pydatetime(),
            outcome=int(r.outcome),
            price_history=tuple(by_hist.get(r.ticker, ())),
            siblings=tuple(by_sib.get(r.ticker, ())),
        ))
    return out


def build(data_root: Path | None = None, lookback_days: float = LOOKBACK_DAYS,
          force: bool = False) -> StoreStats:
    """Append entries for markets that have resolved since the last build.

    Rebuilds from scratch when the policy fingerprint has moved, because rows
    built under a different ENTRY_POLICY are not the same objects and silently
    mixing them is the failure this store exists to avoid.

    The incremental case scans only snapshot files around the resolutions being
    processed -- `lookback_days` before the earliest, one day after the latest.
    An entry must be within MAX_ENTRY_STALENESS_MINUTES of its resolution, so
    that window cannot cut off a usable entry. The full rebuild reads
    everything.
    """
    import shutil

    root = data_root if data_root is not None else DATA_ROOT
    store = root / "entries"
    rebuilt = False
    if force or (store.exists() and not _meta_ok(root)):
        shutil.rmtree(store, ignore_errors=True)
        rebuilt = True

    res_files = _files("resolutions", root)
    if not res_files:
        return StoreStats(0, 0, rebuilt, "no resolutions yet")
    res = pd.concat([pd.read_parquet(f) for f in res_files], ignore_index=True)
    res = res[res["outcome"].notna()].drop_duplicates("ticker")

    processed = set() if rebuilt else _processed_tickers(root)
    new = set(res["ticker"]) - processed
    if not new:
        return StoreStats(0, _entry_count(root), rebuilt, "up to date")

    snap_files = _files("snapshots", root)
    if not rebuilt and processed:
        # Window the scan around the RESOLUTIONS being processed, not around
        # `now`. Resolutions arrive late -- the settle sweep is still recording
        # outcomes one to two days old -- so a window anchored to the clock
        # would silently miss the snapshots of anything that resolved before
        # it. An entry must be within MAX_ENTRY_STALENESS_MINUTES of its
        # resolution, so this window cannot cut off a usable entry.
        when = pd.to_datetime(res[res["ticker"].isin(new)]["resolved_at"], utc=True)
        lo = (when.min() - timedelta(days=lookback_days)).strftime("date=%Y-%m-%d")
        hi = (when.max() + timedelta(days=1)).strftime("date=%Y-%m-%d")
        snap_files = [f for f in snap_files if lo <= Path(f).parent.name <= hi]

    entries = load_entries(root, only_tickers=new, snapshot_files=snap_files)

    # Record the attempt whatever came of it, so a resolution with no usable
    # entry is not reconsidered forever.
    store.mkdir(parents=True, exist_ok=True)
    (store / "processed").mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"ticker": sorted(new)}).to_parquet(
        store / "processed" / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S%f}.parquet",
        index=False)
    _write_meta(store)

    if not entries:
        return StoreStats(0, _entry_count(root), rebuilt,
                          f"{len(new)} resolutions had no usable entry")

    ent, hist, sib = _to_frames(entries)
    def _slice(df: pd.DataFrame, col: str, tickers: set[str]) -> pd.DataFrame:
        # A batch can legitimately contain no history and no siblings -- a
        # market captured once, with no quotable sibling, is the common case
        # for a small series. An empty frame has no columns to filter on.
        if df.empty or col not in df.columns:
            return pd.DataFrame()
        return df[df[col].isin(tickers)]

    resolved_day = pd.to_datetime(ent["resolved_at"], utc=True).dt.strftime("%Y-%m-%d")
    for tag, group in ent.groupby(resolved_day):
        tickers = set(group["ticker"])
        _write(root, "entries", group, tag)
        _write(root, "history", _slice(hist, "ticker", tickers), tag)
        _write(root, "siblings", _slice(sib, "entry_ticker", tickers), tag)

    _write_meta(store)
    return StoreStats(len(entries), _entry_count(root), rebuilt)


def load(data_root: Path | None = None) -> list[Entry] | None:
    """Entries from the table, or None if it is absent or built under a
    different policy. None means "fall back to the raw archive", never "there
    are no entries" -- those are different answers and the caller must not
    confuse a cold cache with an empty dataset.
    """
    import glob

    root = data_root if data_root is not None else DATA_ROOT
    if not _meta_ok(root):
        return None

    def _read(kind: str) -> pd.DataFrame:
        files = sorted(glob.glob(str(root / "entries" / kind / "date=*" / "*.parquet")))
        if not files:
            return pd.DataFrame()
        return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)

    ent = _read("entries")
    if ent.empty:
        return None
    return _from_frames(ent, _read("history"), _read("siblings"))


def iter_entries(data_root: Path | None = None):
    """Yield entries one date partition at a time, never holding the whole set.

    The table is partitioned by resolution date, ~5,600 entries per partition,
    which is a natural chunk: scoring touches each entry exactly once and never
    looks back. Holding all 157,108 costs 2.8 GB in Python objects -- 997,418
    MarketSnapshots and 1,562,774 PricePoints -- while the statistics scoring
    actually keeps are three floats and a series label per entry, about 25 MB.
    The rest is scaffolding held only because score() took a list.

    Yields nothing at all if the table is cold or stale, for the same reason
    load() returns None: a caller must not mistake "not built" for "empty".
    """
    import glob

    root = data_root if data_root is not None else DATA_ROOT
    if not _meta_ok(root):
        return

    def _by_partition(kind: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for f in sorted(glob.glob(str(root / "entries" / kind / "date=*" / "*.parquet"))):
            out.setdefault(Path(f).parent.name, []).append(f)
        return out

    ent_parts = _by_partition("entries")
    hist_parts = _by_partition("history")
    sib_parts = _by_partition("siblings")

    def _read(files: list[str]) -> pd.DataFrame:
        return (pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
                if files else pd.DataFrame())

    for tag in sorted(ent_parts):
        chunk = _from_frames(_read(ent_parts[tag]),
                             _read(hist_parts.get(tag, [])),
                             _read(sib_parts.get(tag, [])))
        if chunk:
            yield chunk


def series_history(data_root: Path | None = None):
    """SeriesHistory built from entry rows alone.

    Needs only (series, ticker, resolved_at, outcome), so it costs ~30 MB and
    does not require materialising price histories or siblings.
    """
    import glob

    from experiments.kalshi_quant.observations import SeriesHistory

    root = data_root if data_root is not None else DATA_ROOT
    files = sorted(glob.glob(str(root / "entries" / "entries" / "date=*" / "*.parquet")))
    if not files:
        return None
    cols = ["ticker", "series_ticker", "resolved_at", "outcome"]
    df = pd.concat([pd.read_parquet(f, columns=cols) for f in files], ignore_index=True)
    sh = SeriesHistory([])
    for r in df.itertuples(index=False):
        sh._by_series.setdefault(r.series_ticker, []).append(
            (r.resolved_at.to_pydatetime(),
             __import__("experiments.kalshi_quant.types", fromlist=["Resolution"]).Resolution(
                 r.ticker, r.resolved_at.to_pydatetime(), int(r.outcome))))
    for v in sh._by_series.values():
        v.sort(key=lambda pair: pair[0])
    sh._keys = {k: [t for t, _ in v] for k, v in sh._by_series.items()}
    return sh
