"""Materialised decision paths: the curated half of raw -> curated.

Since 2026-09-28 (ENTRY_POLICY first_signal_within_72h_of_close) a row in
`entries/` is one DECISION QUOTE of a resolved market, `quotes/` its earlier
prices and `events/` its event's prices; together they rebuild each
MarketPath. `observations.load_paths()` rebuilds its answer from the raw snapshot archive on every
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
    DECISION_HORIZON_HOURS,
    ENTRY_POLICY,
    MAX_PRICE_HISTORY,
    SIBLING_TOLERANCE_MIN,
    MarketPath,
    SeriesHistory,
    _concat,
    _files,
    _read_snapshot,
    path_frames,
    paths_from_frames,
    read_resolutions,
)

STORE = DATA_ROOT / "entries"
META = STORE / "meta.json"

# How far back to look for snapshots of a newly resolved market: its decision
# quotes lie up to DECISION_HORIZON_HOURS (3 days) before its close, a close can
# precede settlement by a day or two, and price history reaches up to
# MAX_PRICE_HISTORY quotes further back (a day at the hourly cadence).
LOOKBACK_DAYS = 7.0
# A full rebuild processes resolutions this many days at a time, so it never
# holds more than ~LOOKBACK_DAYS + BATCH_DAYS of snapshots at once.
BATCH_DAYS = 3


def policy() -> dict[str, object]:
    return {
        "entry_policy": ENTRY_POLICY,
        "decision_horizon_hours": DECISION_HORIZON_HOURS,
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
    # Rows are decision quotes; the count that means something is markets.
    if not files:
        return 0
    return int(pd.concat([pd.read_parquet(f, columns=["ticker"]) for f in files])
               ["ticker"].nunique())


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


def _write_batch(root: Path, dec: pd.DataFrame, quotes: pd.DataFrame,
                 ev: pd.DataFrame) -> int:
    """Partition by the market's resolution date. Event rows are written with
    every partition that has a market in that event, so a partition is
    self-contained when read back alone."""
    if dec.empty:
        return 0
    day = pd.to_datetime(dec["resolved_at"], utc=True).dt.strftime("%Y-%m-%d")
    for tag, group in dec.groupby(day):
        tickers, events = set(group["ticker"]), set(group["event_ticker"])
        _write(root, "entries", group, tag)
        if not quotes.empty:
            _write(root, "quotes", quotes[quotes["ticker"].isin(tickers)], tag)
        if not ev.empty:
            _write(root, "events", ev[ev["event_ticker"].isin(events)], tag)
    return int(dec["ticker"].nunique())


def _scan(root: Path, res: pd.DataFrame, snap_files: list[str]) -> int:
    """Build and write the paths for `res`, reading only `snap_files`."""
    if res.empty:
        return 0
    resolved = set(res["ticker"])
    snap = _concat([_read_snapshot(f, keep_tickers=resolved) for f in snap_files])
    if snap.empty:
        return 0
    events = set(snap["event_ticker"].astype(str))
    ev_snap = _concat([_read_snapshot(f, keep_events=events) for f in snap_files])
    for df in (snap, ev_snap):     # categoricals from different files do not merge
        for c in ("ticker", "event_ticker", "series_ticker", "title"):
            if c in df.columns:
                df[c] = df[c].astype(str)
    return _write_batch(root, *path_frames(snap, res, ev_snap))


def _window(snap_files: list[str], when: pd.Series, lookback_days: float) -> list[str]:
    """Snapshot files from `lookback_days` before the earliest resolution to one
    day after the latest -- windowed on RESOLUTIONS, not on the clock, because
    the settle sweep records outcomes days late."""
    lo = (when.min() - timedelta(days=lookback_days)).strftime("date=%Y-%m-%d")
    hi = (when.max() + timedelta(days=1)).strftime("date=%Y-%m-%d")
    return [f for f in snap_files if lo <= Path(f).parent.name <= hi]


def build(data_root: Path | None = None, lookback_days: float = LOOKBACK_DAYS,
          force: bool = False) -> StoreStats:
    """Append paths for markets that have resolved since the last build.

    Rebuilds from scratch when the policy fingerprint has moved, because rows
    built under a different ENTRY_POLICY are not the same objects and silently
    mixing them is the failure this store exists to avoid. A rebuild runs in
    BATCH_DAYS slices of resolution dates so memory stays bounded.
    """
    import shutil

    root = data_root if data_root is not None else DATA_ROOT
    store = root / "entries"
    rebuilt = False
    if force or (store.exists() and not _meta_ok(root)):
        shutil.rmtree(store, ignore_errors=True)
        rebuilt = True

    res = read_resolutions(root)
    if res.empty:
        return StoreStats(0, 0, rebuilt, "no resolutions yet")
    processed = set() if rebuilt else _processed_tickers(root)
    new = res[~res["ticker"].isin(processed)]
    if new.empty:
        return StoreStats(0, _entry_count(root), rebuilt, "up to date")

    snap_files = _files("snapshots", root)
    when = pd.to_datetime(new["resolved_at"], utc=True)
    day = when.dt.floor("D")
    added = 0
    days = sorted(day.unique())
    for i in range(0, len(days), BATCH_DAYS):
        chunk = set(days[i:i + BATCH_DAYS])
        mask = day.isin(chunk)
        added += _scan(root, new[mask], _window(snap_files, when[mask], lookback_days))

    # Record the attempt whatever came of it, so a resolution with no decision
    # quote is not reconsidered forever.
    (store / "processed").mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"ticker": sorted(new["ticker"])}).to_parquet(
        store / "processed" / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S%f}.parquet",
        index=False)
    _write_meta(store)
    note = "" if added else f"{len(new)} resolutions had no decision quote"
    return StoreStats(added, _entry_count(root), rebuilt, note)


def load(data_root: Path | None = None) -> list[MarketPath] | None:
    """Every path in the table, or None if it is absent or built under a
    different policy. None means "fall back to the raw archive", never "there
    are no paths" -- a caller must not confuse a cold cache with an empty set.
    """
    root = data_root if data_root is not None else DATA_ROOT
    if not _meta_ok(root):
        return None
    out = [p for chunk in iter_paths(root) for p in chunk]
    return out or None


def _partitions(root: Path, kind: str) -> dict[str, list[str]]:
    import glob
    out: dict[str, list[str]] = {}
    for f in sorted(glob.glob(str(root / "entries" / kind / "date=*" / "*.parquet"))):
        out.setdefault(Path(f).parent.name, []).append(f)
    return out


def iter_paths(data_root: Path | None = None):
    """Yield paths one resolution-date partition at a time, never holding the
    whole set. Yields nothing if the table is cold or stale."""
    root = data_root if data_root is not None else DATA_ROOT
    if not _meta_ok(root):
        return

    def _read(files: list[str]) -> pd.DataFrame:
        return (pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
                if files else pd.DataFrame())

    dec, quotes, ev = (_partitions(root, k) for k in ("entries", "quotes", "events"))
    for tag in sorted(dec):
        chunk = paths_from_frames(_read(dec[tag]), _read(quotes.get(tag, [])),
                                  _read(ev.get(tag, [])))
        if chunk:
            yield chunk


def series_history(data_root: Path | None = None) -> SeriesHistory | None:
    """Past resolutions for candidates' context, from EVERY recorded resolution
    -- not only markets in the pool (see SeriesHistory.from_resolutions)."""
    root = data_root if data_root is not None else DATA_ROOT
    if not _meta_ok(root):
        return None
    return SeriesHistory.from_resolutions(read_resolutions(root))
