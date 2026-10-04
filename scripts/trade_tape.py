"""Download Kalshi's public trade tape, to measure resting-order (maker) fills.

Run:  uv run python scripts/trade_tape.py --since 2026-09-20     # backfill, resumable
      uv run python scripts/trade_tape.py                         # catch up to ~now (launchd, hourly)
Out:  data/kalshi_quant/trades/date=YYYY-MM-DD/HH.parquet, log data/trade-tape.log

Why (2026-10-04, human-approved in principle): every candidate pays the spread
plus a taker fee, a ~2c hurdle. A resting order pays no spread and a smaller
fee, but whether it fills, and against whom, cannot be known from snapshots.
The tape says which trades actually happened at which price. A pessimistic
passive-fill model (fill only when trades go THROUGH our price, behind the queue
ahead of us) needs it. MEASUREMENT ONLY; INVARIANT #4 stays in force for every
candidate until that model exists and has been checked.

The endpoint is public and global (GET /markets/trades, min_ts/max_ts, cursor):
~24,000 trades per 5 minutes at midday, ~6 s to page through. Parlay
(KXMVE...) trades, ~8%, are dropped, as in the snapshots. A backfill hour is
written only when its window was walked to the end, so a re-run resumes and a
half-fetched hour is never mistaken for a quiet one. The tape is not
snapshot-dependent: unlike top-of-book it stays queryable (history goes back
months), so this is a backfill, not a race -- but forward hours are still
captured hourly so nothing depends on that staying true.

Columns: ticker, ts (UTC, created_time), yes_cents (int, 1-99), count (float,
fractional contracts exist), taker_yes (True if the taker BOUGHT yes -- that is,
the trade hit a resting yes-ask or a resting no-bid), block (block trades).
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/kalshi_quant/trades"
URL = "https://api.elections.kalshi.com/trade-api/v2/markets/trades"
SAFETY_LAG = timedelta(minutes=10)     # never fetch an hour still filling


def part_path(hour: datetime) -> Path:
    return OUT / f"date={hour:%Y-%m-%d}" / f"{hour:%H}.parquet"


def fetch_hour(client: httpx.Client, hour: datetime) -> pd.DataFrame:
    start, end = int(hour.timestamp()), int((hour + timedelta(hours=1)).timestamp()) - 1
    rows: list[dict] = []
    cursor = None
    while True:
        params = {"limit": 1000, "min_ts": start, "max_ts": end}
        if cursor:
            params["cursor"] = cursor
        for attempt in range(6):
            r = client.get(URL, params=params)
            if r.status_code == 200:
                break
            time.sleep(1 + 2 * attempt)      # 429/5xx: back off, then retry
        r.raise_for_status()
        j = r.json()
        for t in j.get("trades", []):
            if t["ticker"].startswith("KXMVE"):
                continue
            rows.append((t["ticker"], t["created_time"],
                         round(float(t["yes_price_dollars"]) * 100),
                         float(t["count_fp"]), t["taker_side"] == "yes",
                         bool(t.get("is_block_trade"))))
        cursor = j.get("cursor")
        if not cursor or not j.get("trades"):
            break
    df = pd.DataFrame(rows, columns=["ticker", "ts", "yes_cents", "count", "taker_yes", "block"])
    df["ts"] = pd.to_datetime(df.ts, utc=True, format="ISO8601")
    df["yes_cents"] = df.yes_cents.astype("int8")
    df["count"] = df["count"].astype("float32")
    df["ticker"] = df.ticker.astype("category")
    return df.sort_values("ts", kind="stable").drop_duplicates(
        ["ticker", "ts", "yes_cents", "count", "taker_yes"], keep="first")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="UTC date to backfill from (default: last 6 hours)")
    ap.add_argument("--until", help="UTC date (exclusive); default now")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    end = (datetime.fromisoformat(a.until).replace(tzinfo=timezone.utc) if a.until
           else (now - SAFETY_LAG).replace(minute=0, second=0, microsecond=0))
    h = (datetime.fromisoformat(a.since).replace(tzinfo=timezone.utc) if a.since
         else end - timedelta(hours=6))
    done = n_rows = 0
    t0 = time.time()
    with httpx.Client(timeout=30) as client:
        while h < end:
            p = part_path(h)
            if not p.exists():
                df = fetch_hour(client, h)
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_suffix(".tmp")
                df.to_parquet(tmp, index=False)
                tmp.rename(p)                # atomic: a file means a COMPLETE hour
                n_rows += len(df)
                done += 1
                print(f"[tape] {h:%Y-%m-%d %HZ} {len(df):,} trades "
                      f"({p.stat().st_size / 1e6:.1f} MB)", flush=True)
            h += timedelta(hours=1)
    print(f"[tape] {done} hours, {n_rows:,} trades in {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
