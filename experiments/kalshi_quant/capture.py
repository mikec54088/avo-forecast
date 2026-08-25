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
import os
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


def _write(df: pd.DataFrame, kind: str) -> Path:
    now = datetime.now(timezone.utc)
    out_dir = DATA_ROOT / kind / f"date={now:%Y-%m-%d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{now:%H%M%S}.parquet"
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


def snapshot() -> Path:
    with _Lock("snapshot"):
        rows: list[dict[str, object]] = []
        seen = pages = no_book = unparsed = 0
        unparsed_samples: list[str] = []
        started = datetime.now(timezone.utc)
        with KalshiClient() as client:
            for markets, fetched_at in client.iter_market_pages(status="open"):
                pages += 1
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
        path = _write(df, "snapshots")
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        mve = int(df["is_mve"].sum()) if not df.empty else 0
        print(
            f"{len(rows)} quotable markets ({mve} mve) from {seen} seen over {pages} pages "
            f"in {elapsed:.0f}s; {no_book} skipped for no two-sided book; "
            f"{unparsed} unparsed -> {path}"
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
            path = _write(pd.DataFrame(
                columns=["ticker", "series_ticker", "resolved_at", "outcome",
                         "settlement_value"]), "resolutions")
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

        df = pd.DataFrame(rows)
        path = _write(df, "resolutions")
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        unresolved = sum(len(w) for w in by_series.values())
        print(
            f"{len(rows)} resolutions over {pages} requests in {elapsed:.0f}s; "
            f"{unresolved} still pending (closed but not yet settled) -> {path}"
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
    args = ap.parse_args()
    if args.mode == "snapshot":
        snapshot()
    else:
        settle(snapshot_days=args.snapshot_days)


if __name__ == "__main__":
    main()
