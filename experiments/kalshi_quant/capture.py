"""Phase 0: the only calendar-bound piece. Get this running tonight.

  snapshot — append top-of-book for every quotable market  (cron: every 15 min)
  settle   — sweep settled markets, record outcomes        (cron: hourly)

Parquet under data/kalshi_quant/, partitioned by date. Never committed.

Scope of a snapshot
-------------------
The live `status=open` universe is large and volatile because most of it is
auto-generated multivariate "parlay" combos (`mve_selected_legs`): it grew from
698,892 to 1,319,432 markets over 15 hours on 2026-08-24. The quotable subset
barely moves — 57,844 markets carried a two-sided book in the 04:28 UTC sweep,
61,052 in the 19:09 UTC one. Budget ~7 minutes per sweep at 1.3M markets.

We persist the markets with a two-sided book. That is not a size optimization —
INVARIANT #2 defines fitness relative to the market's implied probability, and a
market quoting 0.0000 / 0.0000 or 0.0000 / 1.0000 has no implied probability to
score against. Its midpoint is an artifact of an empty book, and storing it would
put fabricated "market beliefs" into the training set.

MVE combos are kept but flagged (`is_mve`) rather than dropped, so excluding them
stays a downstream decision on recorded data. In practice the book filter already
removes almost all of them. Fully decomposed, the 04:28 UTC sweep of 2026-08-24
held 604,709 MVE markets of which just 74 carried a two-sided book (0.01%),
against a 61.3% book rate among non-MVE markets (57,770 of 94,183). The 8%
overall book rate is an MVE artifact, not a property of real markets.
"""
from __future__ import annotations

import argparse
import glob
import os
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Self

import pandas as pd

from experiments.kalshi_quant.client import KalshiClient, parse_market
from experiments.kalshi_quant.types import MarketSnapshot

DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "kalshi_quant"

# A full sweep takes ~7 minutes against a 15-minute cron. Overlapping runs would
# double the request rate into a limiter we already pace against.
LOCK_STALE_SECONDS = 40 * 60

# Resolutions are looked up from what we captured, NOT by walking the global
# settled feed. Measured 2026-08-24: that feed is 99.8% MVE parlay combos and is
# not usefully ordered — page 25 still carried settlements six minutes old while
# its oldest entry was 45 minutes behind — so no settlement-time window can
# terminate against it. Series-scoped queries are broadly newest-first and the
# query set is tiny: the 1,288 captured markets past close_time spanned just 60
# series, against 13,377 for a blind walk.
SETTLE_SNAPSHOT_DAYS = 2.0      # how far back to scan snapshots for closed markets
SETTLE_MARGIN_HOURS = 1.0       # slack below the oldest close_time we still need
SETTLE_MAX_PAGES_PER_SERIES = 20

RESOLUTION_COLS = ["ticker", "series_ticker", "resolved_at", "outcome", "settlement_value"]

# A bounded sweep (--max-close-hours) walked 9 pages on 2026-08-25 against
# 2,118 unbounded. If max_close_ts is ever ignored -- and this API silently
# ignores unknown parameters rather than rejecting them -- a bounded run would
# quietly become a full sweep every 15 minutes, roughly tripling our request
# volume. Stop well short of that and say so loudly.
BOUNDED_MAX_PAGES = 200

# The series-scoped full pass. Sweeping /markets globally means fetching the
# whole open universe to keep the sliver that has a book: measured 2026-09-12,
# 8,324,898 market records over 8,325 pages in 83 minutes, of which 8,236,140
# were skipped for having no two-sided book. It had grown past its own hourly
# schedule, so overrunning runs found the lock held and the full pass silently
# degraded to two-hourly.
#
# Bounding by close time does NOT fix it. The auto-generated MVE parlay combos
# cluster in the SAME 1-7 day window as the real game markets -- a 168h bound
# still saw 3,000,000 records -- so close_time cannot separate them. Measured:
# a 48h bound finished in 5 minutes and left ONE researchable game market
# against 30 unbounded.
#
# series_ticker can. It is a real server-side filter (verified 2026-09-12: a
# bogus series returns zero markets, so it is not being silently ignored), and
# settle() has depended on it in production since 2026-08-24. Cost scales with
# the number of series we care about, not with how many parlays Kalshi
# generates -- KXMLBGAME walks in 1 page and 0.4s.
#
# The universe is derived from recent snapshots rather than configured, so it
# maintains itself. The NEAR pass is the discovery mechanism: it is unbounded
# in series and sees everything closing within 24h, so a brand-new series shows
# up there long before it can resolve, and the next full pass picks it up.
SERIES_UNIVERSE_DAYS = 7.0
SERIES_MAX_PAGES = 20


def _write(df: pd.DataFrame, kind: str, label: str = "") -> Path:
    now = datetime.now(timezone.utc)
    out_dir = DATA_ROOT / kind / f"date={now:%Y-%m-%d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{now:%H%M%S}{label}.parquet"
    df.to_parquet(path, index=False)
    return path


class _Lock:
    def __init__(self, name: str) -> None:
        self.path = DATA_ROOT / f".{name}.lock"

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            age = datetime.now(timezone.utc).timestamp() - self.path.stat().st_mtime
            if age < LOCK_STALE_SECONDS:
                raise SystemExit(f"another run holds {self.path.name} (age {age:.0f}s); skipping")
            print(f"clearing stale lock ({age:.0f}s old)")
        self.path.write_text(str(os.getpid()))
        return self

    def __exit__(self, *exc: object) -> None:
        self.path.unlink(missing_ok=True)


def _resolved_at(raw: dict[str, Any]) -> datetime | None:
    """The instant ground truth became known.

    INVARIANT #1 keys off this, so it must be the settlement, not the close.
    They differ: KXMLBGAME-26AUG231910ATLMIL closed 02:19:33Z and settled
    02:22:37Z. `close_time` is when trading stopped; `settlement_ts` is when the
    outcome was published. Using the close would date a resolution earlier than
    it was known and could admit an observation the holdout should exclude.
    """
    for key in ("settlement_ts", "close_time", "expiration_time"):
        ts = raw.get(key)
        if ts:
            return datetime.fromisoformat(ts)
    return None


def _row(m: MarketSnapshot) -> dict[str, object]:
    return {
        "ticker": m.ticker,
        "event_ticker": m.event_ticker,
        "series_ticker": m.series_ticker,
        "title": m.title,
        "observed_at": m.observed_at,
        "close_time": m.close_time,
        "yes_bid": m.yes_bid,
        "yes_ask": m.yes_ask,
        "last_price": m.last_price,
        "volume": m.volume,
        "open_interest": m.open_interest,
        "yes_bid_size": m.yes_bid_size,
        "yes_ask_size": m.yes_ask_size,
        "liquidity": m.liquidity,
        "status": m.status,
        "price_level_structure": m.price_level_structure,
        "is_mve": m.is_mve,
    }


def series_universe(days: float = SERIES_UNIVERSE_DAYS, root: Path | None = None) -> list[str]:
    """Every series seen in snapshots from the last `days`.

    Reads only the series_ticker column, so it is cheap even over a week of
    files. Falls back to an empty list when nothing has been captured yet,
    which callers must treat as "sweep globally" rather than "sweep nothing".
    """
    root = root or DATA_ROOT
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("date=%Y-%m-%d")
    files = [f for f in sorted(glob.glob(str(root / "snapshots" / "date=*" / "*.parquet")))
             if Path(f).parent.name >= cutoff]
    seen: set[str] = set()
    for f in files:
        try:
            seen.update(pd.read_parquet(f, columns=["series_ticker"])["series_ticker"].unique())
        except (OSError, ValueError, KeyError):
            continue
    return sorted(x for x in seen if isinstance(x, str) and x)


def _iter_series_pages(
    client: KalshiClient, series: Sequence[str], max_pages_per_series: int
) -> Iterator[tuple[list[dict[str, Any]], datetime]]:
    """(markets, fetched_at) per page, series by series.

    Same shape as client.iter_market_pages so the caller does not care which
    walk it got. fetched_at is per page because a sweep spans minutes and
    INVARIANT #1 keys off observation time.
    """
    for st in series:
        cursor: str | None = None
        for _ in range(max_pages_per_series):
            page = client._get("/markets", status="open", limit=1000,
                               series_ticker=st, cursor=cursor)
            fetched_at = datetime.now(timezone.utc)
            markets = page.get("markets", [])
            if markets:
                yield markets, fetched_at
            cursor = page.get("cursor")
            if not cursor or not markets:
                break


def snapshot(
    max_close_hours: float | None = None,
    pass_name: str | None = None,
    max_pages: int | None = None,
    by_series: bool = False,
    series: Sequence[str] | None = None,
) -> Path:
    """Append top-of-book for every quotable market.

    With `max_close_hours` the sweep is bounded to markets closing inside that
    window. That is not a sampling shortcut: every market passes through the
    window before it closes, so a bounded pass run often enough still captures
    an entry price for everything that becomes scoreable.

    `pass_name` ("full" or "near") picks the lock and the filename suffix, and
    is DELIBERATELY independent of whether the sweep is bounded. They used to be
    the same switch, which was fine only while "bounded" and "near" meant the
    same thing. They stopped meaning the same thing on 2026-09-12, when the
    unbounded full pass had to be bounded too: the open universe had grown to
    6.5M market records over 6,530 pages and the sweep took 53-82 minutes
    against an hourly schedule. Overrunning runs found the lock held and exited,
    so the full pass silently degraded to roughly two-hourly. Almost all of that
    growth is auto-generated MVE parlay combos -- 99.8% of the universe, of
    which 0.01% carry a book -- so the cost bought nothing.

    A bounded full pass keeps everything that becomes scoreable: p90 time to
    close among resolved observations is 2.2 days, so a week's window has ample
    headroom, and MAX_PRICE_HISTORY caps history at 24 points regardless. What
    it gives up is price history on markets more than the window from closing,
    which nothing has ever used.
    """
    bounded = max_close_hours is not None
    if pass_name is None:
        pass_name = "near" if bounded else "full"
    if pass_name not in ("full", "near"):
        raise SystemExit(f"pass_name must be 'full' or 'near', not {pass_name!r}")
    lock_name = "snapshot_near" if pass_name == "near" else "snapshot"
    label = "-near" if pass_name == "near" else ""
    page_cap = max_pages if max_pages is not None else BOUNDED_MAX_PAGES
    max_close_ts = (
        int((datetime.now(timezone.utc) + timedelta(hours=max_close_hours)).timestamp())
        if bounded else None
    )
    universe: list[str] = []
    if by_series:
        universe = list(series) if series is not None else series_universe()
        if not universe:
            print("no series universe yet (nothing captured); sweeping globally")
            by_series = False

    with _Lock(lock_name):
        rows: list[dict[str, object]] = []
        seen = pages = no_book = unparsed = 0
        unparsed_samples: list[str] = []
        overran = False
        started = datetime.now(timezone.utc)
        with KalshiClient() as client:
            walk = (
                _iter_series_pages(client, universe, SERIES_MAX_PAGES) if by_series
                else client.iter_market_pages(status="open", max_close_ts=max_close_ts)
            )
            for markets, fetched_at in walk:
                pages += 1
                if bounded and not by_series and pages > page_cap:
                    overran = True
                    break
                seen += len(markets)
                for raw in markets:
                    # A single malformed market must not cost the whole sweep.
                    # rows are only written after the full walk, so an
                    # uncaught parse error means zero rows on disk, not a
                    # partial file -- and top-of-book at this instant is gone
                    # forever. Count and sample loudly instead of dying.
                    try:
                        m = parse_market(raw, observed_at=fetched_at)
                    except (KeyError, ValueError, TypeError) as exc:
                        unparsed += 1
                        if len(unparsed_samples) < 5:
                            unparsed_samples.append(f"{raw.get('ticker', '?')}: {exc!r}")
                        continue
                    if not m.has_two_sided_book:
                        no_book += 1
                        continue
                    rows.append(_row(m))

        df = pd.DataFrame(rows)
        path = _write(df, "snapshots", label)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        mve = int(df["is_mve"].sum()) if not df.empty else 0
        scope = (f"{len(universe)} series" if by_series
                 else (f"<={max_close_hours:g}h" if bounded else "all"))
        print(
            f"[{scope}] {len(rows)} quotable markets ({mve} mve) from {seen} seen "
            f"over {pages} pages in {elapsed:.0f}s; {no_book} skipped for no "
            f"two-sided book; {unparsed} unparsed; {client.rate_limited} rate-limited "
            f"of {client.requests} requests -> {path}"
        )
        if overran:
            print(
                f"  !!! bounded sweep exceeded {page_cap} pages -- max_close_ts "
                f"may be being ignored. Check before this runs again; an ignored "
                f"filter turns this into a full sweep every tick."
            )
        for line in unparsed_samples:
            print(f"  unparsed: {line}")
        return path


def _parquet_files(kind: str, since: datetime | None = None) -> list[Path]:
    """Files under DATA_ROOT/<kind>/date=YYYY-MM-DD/, optionally date-filtered."""
    out = []
    for d in sorted(DATA_ROOT.glob(f"{kind}/date=*")):
        if since is not None:
            try:
                day = datetime.strptime(d.name[5:], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if day < since - timedelta(days=1):
                continue
        out.extend(sorted(d.glob("*.parquet")))
    return out


def _resolved_tickers() -> set[str]:
    seen: set[str] = set()
    for f in _parquet_files("resolutions"):
        try:
            seen.update(pd.read_parquet(f, columns=["ticker"])["ticker"])
        except (OSError, ValueError) as exc:  # truncated file must not block the run
            print(f"  warning: unreadable {f.name}: {exc!r}")
    return seen


def _pending(snapshot_days: float) -> pd.DataFrame:
    """Non-MVE markets we captured that are past close_time and unresolved.

    Only recent snapshots are scanned. That is sufficient rather than a
    shortcut: a market appears in every snapshot right up until it closes, so
    anything that closed inside the window is present in one of these files.
    """
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=snapshot_days)
    frames = []
    for f in _parquet_files("snapshots", since):
        try:
            frames.append(pd.read_parquet(
                f, columns=["ticker", "series_ticker", "close_time", "is_mve"]
            ))
        except (OSError, ValueError) as exc:
            print(f"  warning: unreadable {f.name}: {exc!r}")
    if not frames:
        return pd.DataFrame(columns=["ticker", "series_ticker", "close_time", "is_mve"])
    df = pd.concat(frames, ignore_index=True).drop_duplicates("ticker")
    df = df[(~df["is_mve"]) & (df["close_time"] <= now)]
    already = _resolved_tickers()
    if already:
        df = df[~df["ticker"].isin(already)]
    return df


def settle(
    snapshot_days: float = SETTLE_SNAPSHOT_DAYS,
    max_pages_per_series: int = SETTLE_MAX_PAGES_PER_SERIES,
) -> Path:
    """Record outcomes for captured markets that have closed but are unresolved.

    Driven by our own snapshots, not by the global settled feed. See
    SETTLE_SNAPSHOT_DAYS above for why: that feed is 99.8% MVE and is not
    ordered well enough for any settlement-time window to terminate against.

    Because the work list is "captured, closed, and not yet resolved", the job
    is idempotent and self-healing. A market that has closed but not yet settled
    (settlement lags close by ~3 minutes) simply stays pending and is picked up
    next run, and a gap of any length repairs itself rather than falling out of
    a fixed window.

    The `result` -> outcome mapping is VERIFIED against the live API on
    2026-08-24 over 964 settled non-MVE markets: every result='yes' market paid
    settlement_value_dollars=$1.0000 (349/349) and every result='no' paid
    $0.0000 (615/615); median last_price at settlement was 0.990 vs 0.010; and
    all 182 two-leg events had exactly one 'yes'. It is NOT inverted. This run
    re-checks that agreement on every market and reports any disagreement --
    a silent flip here would invert every score in the project.

    Note settled markets report status='finalized', not 'settled'.
    """
    with _Lock("settle"):
        started = datetime.now(timezone.utc)
        pend = _pending(snapshot_days)
        if pend.empty:
            path = _write(pd.DataFrame(columns=RESOLUTION_COLS), "resolutions")
            print(f"nothing pending -> {path}")
            return path

        by_series = {
            st: set(g["ticker"])
            for st, g in pend.groupby("series_ticker", sort=False)
        }
        earliest = pend.groupby("series_ticker")["close_time"].min()
        print(f"{len(pend)} closed unresolved markets across {len(by_series)} series")

        rows: list[dict[str, object]] = []
        mismatches: list[str] = []
        pages = 0
        with KalshiClient() as client:
            for st, want in by_series.items():
                floor = earliest[st].to_pydatetime() - timedelta(hours=SETTLE_MARGIN_HOURS)
                cursor: str | None = None
                for _ in range(max_pages_per_series):
                    page = client._get(
                        "/markets", status="settled", limit=1000,
                        series_ticker=st, cursor=cursor,
                    )
                    pages += 1
                    markets = page.get("markets", [])
                    if not markets:
                        break
                    newest: datetime | None = None
                    for raw in markets:
                        ts = _resolved_at(raw)
                        if ts is not None and (newest is None or ts > newest):
                            newest = ts
                        if raw.get("ticker") not in want:
                            continue
                        if raw.get("result") not in ("yes", "no") or ts is None:
                            continue
                        outcome = 1 if raw["result"] == "yes" else 0
                        sv = raw.get("settlement_value_dollars")
                        try:
                            if sv is not None and (float(sv) >= 0.5) != bool(outcome):
                                mismatches.append(f"{raw['ticker']}: result={raw['result']} value={sv}")
                        except (TypeError, ValueError):
                            pass
                        rows.append({
                            "ticker": raw["ticker"],
                            "series_ticker": st,
                            "resolved_at": ts,
                            "outcome": outcome,
                            "settlement_value": float(sv) if sv is not None else None,
                        })
                        want.discard(raw["ticker"])
                    cursor = page.get("cursor")
                    # Stop as soon as this series is satisfied; otherwise once the
                    # page is entirely older than anything we still need. Order
                    # within a series is broadly, not strictly, newest-first, so
                    # this leans on the ticker set rather than on the ordering.
                    if not want or not cursor or (newest is not None and newest < floor):
                        break

        # An empty result still needs the schema: pd.DataFrame([]) writes a
        # Parquet with no columns at all, which _resolved_tickers() then cannot
        # read, so every later run logs a warning over a file that holds
        # nothing. Give it the columns explicitly.
        df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=RESOLUTION_COLS)
        path = _write(df, "resolutions")
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        unresolved = sum(len(w) for w in by_series.values())
        print(
            f"{len(rows)} resolutions over {pages} requests in {elapsed:.0f}s; "
            f"{unresolved} still pending (closed but not yet settled); "
            f"{client.rate_limited} rate-limited -> {path}"
        )
        if mismatches:
            print(f"  !!! {len(mismatches)} result/settlement_value disagreements:")
            for line in mismatches[:5]:
                print(f"    {line}")
        return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["snapshot", "settle"])
    ap.add_argument(
        "--snapshot-days", type=float, default=SETTLE_SNAPSHOT_DAYS,
        help="settle only: how far back to scan snapshots (default %(default)s)",
    )
    ap.add_argument(
        "--max-close-hours", type=float, default=None,
        help="snapshot only: bound the sweep to markets closing within N hours",
    )
    ap.add_argument(
        "--pass-name", choices=["full", "near"], default=None,
        help="snapshot only: which pass this is, deciding the lock and the "
             "filename suffix. Independent of --max-close-hours: the full pass "
             "is bounded too since 2026-09-12. Defaults to near when bounded, "
             "full when not, which is how it behaved before the two were split.",
    )
    ap.add_argument(
        "--by-series", action="store_true",
        help="snapshot only: sweep series-by-series instead of walking the whole "
             "open universe. Cost then scales with the series we care about "
             "rather than with how many parlay combos Kalshi generates. The "
             "series list is derived from recent snapshots, so it maintains "
             "itself; the near pass is what discovers new ones.",
    )
    ap.add_argument(
        "--max-pages", type=int, default=None,
        help="snapshot only: page guard for a bounded sweep (default "
             f"{BOUNDED_MAX_PAGES}). Exists to catch max_close_ts silently "
             "ceasing to filter, so set it a few times the observed page count.",
    )
    args = ap.parse_args()
    if args.mode == "snapshot":
        snapshot(max_close_hours=args.max_close_hours, pass_name=args.pass_name,
                 max_pages=args.max_pages, by_series=args.by_series)
    else:
        settle(snapshot_days=args.snapshot_days)


if __name__ == "__main__":
    main()
