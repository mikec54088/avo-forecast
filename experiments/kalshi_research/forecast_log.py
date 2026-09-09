"""The forecast log: the observation source for kalshi_research.

kalshi_quant scores by REPLAY -- feed a captured snapshot to a candidate and
compare with the outcome recorded later. That is clean only because a book-only
candidate's whole input is the snapshot. A research candidate's input includes
whatever the web says at the moment it runs, and the web today knows how last
month's market resolved. Replay would hand it the answer through the search box.

So this experiment is evaluated FORWARD ONLY. A candidate runs against open
markets as they are, its forecast is appended here with the quote it saw and
the instant it saw it, and scoring later joins these rows to resolutions. The
row's `forecast_at` is the holdout: only outcomes resolving strictly after it
count. `candidate.created_at` is irrelevant here.

Append-only Parquet under data/kalshi_research/forecasts/date=*/. Never
committed (INVARIANT #5). Every candidate's forecast is logged for every market
in a pass -- including abstentions, which log the implied probability -- so
skill and P&L are computed over the same population exactly as in kalshi_quant.
"""
from __future__ import annotations

import glob
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from experiments.kalshi_quant.types import MarketSnapshot

DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "kalshi_research"

COLS = [
    "candidate_id", "ticker", "event_ticker", "series_ticker", "title",
    "forecast", "forecast_at", "observed_at", "close_time",
    "yes_bid", "yes_ask", "yes_bid_size", "yes_ask_size", "last_price",
    "volume", "open_interest", "price_level_structure",
    "research_calls", "research_elapsed_s", "researcher", "error",
]


def row(candidate_id: str, m: MarketSnapshot, forecast: float, forecast_at: datetime,
        research_calls: int, research_elapsed_s: float, researcher: str,
        error: str = "") -> dict[str, object]:
    return {
        "candidate_id": candidate_id, "ticker": m.ticker,
        "event_ticker": m.event_ticker, "series_ticker": m.series_ticker,
        "title": m.title, "forecast": forecast, "forecast_at": forecast_at,
        "observed_at": m.observed_at, "close_time": m.close_time,
        "yes_bid": m.yes_bid, "yes_ask": m.yes_ask,
        "yes_bid_size": m.yes_bid_size, "yes_ask_size": m.yes_ask_size,
        "last_price": m.last_price, "volume": m.volume,
        "open_interest": m.open_interest,
        "price_level_structure": m.price_level_structure,
        "research_calls": research_calls, "research_elapsed_s": research_elapsed_s,
        "researcher": researcher, "error": error,
    }


def append(rows: list[dict[str, object]], root: Path | None = None) -> Path:
    root = root or DATA_ROOT
    now = datetime.now(timezone.utc)
    out = root / "forecasts" / f"date={now:%Y-%m-%d}"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{now:%H%M%S}.parquet"
    pd.DataFrame(rows, columns=COLS).to_parquet(path, index=False)
    return path


def read(root: Path | None = None, days: float | None = None) -> pd.DataFrame:
    root = root or DATA_ROOT
    files = sorted(glob.glob(str(root / "forecasts" / "date=*" / "*.parquet")))
    if days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("date=%Y-%m-%d")
        files = [f for f in files if Path(f).parent.name >= cutoff]
    if not files:
        return pd.DataFrame(columns=COLS)
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def seen(root: Path | None = None, days: float = 3.0) -> set[tuple[str, str]]:
    """(candidate_id, ticker) pairs already forecast. A market is forecast at
    most once per candidate: research is spent once, and the first forecast
    inside the window is the one that is scored."""
    df = read(root, days)
    if df.empty:
        return set()
    return set(zip(df["candidate_id"], df["ticker"], strict=True))
