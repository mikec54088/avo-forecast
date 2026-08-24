"""Phase 0: the only calendar-bound piece. Get this running tonight.

  snapshot — append top-of-book for every open market   (cron: every 15 min)
  settle   — sweep settled markets, record outcomes     (cron: hourly)

Parquet under data/kalshi_quant/, partitioned by date. Never committed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from experiments.kalshi_quant.client import KalshiClient, parse_market

DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "kalshi_quant"


def _write(df: pd.DataFrame, kind: str) -> Path:
    now = datetime.now(timezone.utc)
    out_dir = DATA_ROOT / kind / f"date={now:%Y-%m-%d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{now:%H%M%S}.parquet"
    df.to_parquet(path, index=False)
    return path


def snapshot() -> Path:
    client, now, rows = KalshiClient(), datetime.now(timezone.utc), []
    try:
        for raw in client.iter_markets(status="open"):
            m = parse_market(raw, observed_at=now)
            rows.append({
                "ticker": m.ticker, "event_ticker": m.event_ticker,
                "series_ticker": m.series_ticker, "title": m.title,
                "observed_at": m.observed_at, "close_time": m.close_time,
                "yes_bid": m.yes_bid, "yes_ask": m.yes_ask,
                "last_price": m.last_price, "volume": m.volume,
                "open_interest": m.open_interest,
            })
    finally:
        client.close()
    path = _write(pd.DataFrame(rows), "snapshots")
    print(f"{len(rows)} markets -> {path}")
    return path


def settle() -> Path:
    """TODO(claude-code): confirm the settlement field name. Docs reference
    `result` on settled markets ('yes'|'no'|''), but VERIFY — a wrong mapping
    here silently inverts every score in the project."""
    client, rows = KalshiClient(), []
    try:
        for raw in client.iter_markets(status="settled"):
            if raw.get("result") not in ("yes", "no"):
                continue
            rows.append({
                "ticker": raw["ticker"],
                "series_ticker": raw.get("series_ticker", raw["ticker"].split("-")[0]),
                "resolved_at": raw.get("close_time") or raw.get("expiration_time"),
                "outcome": 1 if raw["result"] == "yes" else 0,
            })
    finally:
        client.close()
    path = _write(pd.DataFrame(rows), "resolutions")
    print(f"{len(rows)} resolutions -> {path}")
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["snapshot", "settle"])
    {"snapshot": snapshot, "settle": settle}[ap.parse_args().mode]()


if __name__ == "__main__":
    main()
