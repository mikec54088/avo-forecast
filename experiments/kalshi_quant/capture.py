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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Self

import pandas as pd

from experiments.kalshi_quant.client import KalshiClient, parse_market
from experiments.kalshi_quant.types import MarketSnapshot

DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "kalshi_quant"

# A full sweep takes ~8 minutes against a 15-minute cron. Overlapping runs would
# double the request rate into a limiter we already pace against.
LOCK_STALE_SECONDS = 40 * 60


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


def settle() -> Path:
    """Sweep settled markets and record outcomes.

    The `result` -> outcome mapping is VERIFIED against the live API on
    2026-08-24 over 964 settled non-MVE markets: every result='yes' market paid
    settlement_value_dollars=$1.0000 (349/349) and every result='no' paid
    $0.0000 (615/615); median last_price at settlement was 0.990 vs 0.010; and
    all 182 two-leg events had exactly one 'yes'. The mapping is NOT inverted.

    Note settled markets report status='finalized', not 'settled' — filter on
    `result`, not on `status`.

    STILL BROKEN: this walks the entire settled universe with no date bound. The
    non-MVE portion alone is ~1.34M markets per 30 days, so a sweep cannot
    finish inside the hourly cron slot. Worse, LOCK_STALE_SECONDS is 40 min
    against an hourly tick, so an overrunning run has its own lock cleared by
    the next tick and a second concurrent sweep starts. Bound this by
    settlement time before installing the settle cron line.
    """
    with _Lock("settle"):
        rows = []
        undated = 0
        with KalshiClient() as client:
            for raw in client.iter_markets(status="settled"):
                if raw.get("result") not in ("yes", "no"):
                    continue
                resolved_at = _resolved_at(raw)
                if resolved_at is None:
                    # No usable timestamp means the holdout cannot place it.
                    # Dropping is the only safe option: a guessed resolution
                    # time is how in-sample data leaks past INVARIANT #1.
                    undated += 1
                    continue
                rows.append({
                    "ticker": raw["ticker"],
                    "series_ticker": raw["event_ticker"].split("-")[0],
                    "resolved_at": resolved_at,
                    "outcome": 1 if raw["result"] == "yes" else 0,
                })
        path = _write(pd.DataFrame(rows), "resolutions")
        print(f"{len(rows)} resolutions ({undated} dropped, no timestamp) -> {path}")
        return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["snapshot", "settle"])
    {"snapshot": snapshot, "settle": settle}[ap.parse_args().mode]()


if __name__ == "__main__":
    main()
