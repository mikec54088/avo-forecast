"""Live paper trading: decisions recorded at the instant, not replayed.

WHY THIS EXISTS. Every number in this project comes from REPLAY -- take a
snapshot up to 60 minutes old, ask a candidate what it thinks, compare with the
outcome recorded later. That is a legitimate forward test of the CANDIDATE
(INVARIANT #1 guarantees the outcome was unknown when it was written), and it
is not a test of the STRATEGY. Replay silently assumes three things that real
trading does not give you:

  1. that you were there at the entry instant. The scored entry is the last
     snapshot before resolution within 60 minutes, chosen with hindsight over
     the market's whole life. Live, you must decide at a moment you pick in
     advance, without knowing which quote turns out to be the last one.
  2. that the quote you were scored against is the quote you could have traded.
     Measured 2026-09-17, unclimbed_favourite's filled entries sit at a median
     46 minutes' staleness -- so the book it was scored on was, on average, 46
     minutes stale by the time the market resolved.
  3. that size was available. simulate_fill caps at 25% of the visible top of
     book, which is the right shape, but it has never been checked against what
     a resting order would actually have got.

This log closes all three. On every pass it asks the candidates about markets
that are OPEN RIGHT NOW, writes down the decision with the book exactly as it
stood, and stops. Resolution is joined later. Nothing here is reversible after
the fact, which is the point: a row is a claim made before the answer existed.

It deliberately mirrors kalshi_research/forecast_log.py rather than inventing a
second pattern -- same append-only Parquet shape, same "the timestamp IS the
holdout" rule. The difference is only which candidates write to it.

Run artifact, never committed (INVARIANT #5).
"""
from __future__ import annotations

import glob
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from experiments.kalshi_quant.scoring import simulate_fill
from experiments.kalshi_quant.types import MarketSnapshot

DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "kalshi_quant"

COLS = [
    "candidate_id", "ticker", "event_ticker", "series_ticker", "title",
    "decided_at", "observed_at", "close_time", "quote_age_s",
    "forecast", "market_prob", "yes_bid", "yes_ask", "yes_bid_size",
    "yes_ask_size", "last_price", "volume", "open_interest",
    "acted", "side", "contracts", "fill_price_cents", "fill_reason", "error",
    # Which capture pass the quote came from: "near" (<=24h to close, at most
    # ~15 min old) or "full" (the hourly sweep, up to ~85 min old). Recorded
    # per decision so the cost of the older quote can be measured instead of
    # argued about -- see paper_runner.MAX_FULL_QUOTE_AGE_MIN. Rows written
    # before 2026-09-24 have no value here and are all "near" by construction.
    "quote_source",
]


def row(candidate_id: str, m: MarketSnapshot, forecast: float,
        decided_at: datetime, desired_contracts: int = 100,
        error: str = "", quote_source: str = "near") -> dict[str, object]:
    """One decision, with the book as it stood and the fill it would have got.

    `acted` is recorded rather than inferred later: whether a candidate took a
    position is a property of the moment, and a later change to the fill model
    must not silently rewrite what it decided.
    """
    mid = m.implied_prob
    acted = abs(forecast - mid) > 1e-12 and not error
    side = ("yes" if forecast > mid else "no") if acted else ""
    fill = simulate_fill(m, side, desired_contracts) if acted else None
    return {
        "candidate_id": candidate_id, "ticker": m.ticker,
        "event_ticker": m.event_ticker, "series_ticker": m.series_ticker,
        "title": m.title, "decided_at": decided_at,
        "observed_at": m.observed_at, "close_time": m.close_time,
        "quote_age_s": (decided_at - m.observed_at).total_seconds(),
        "forecast": forecast, "market_prob": mid,
        "yes_bid": m.yes_bid, "yes_ask": m.yes_ask,
        "yes_bid_size": m.yes_bid_size, "yes_ask_size": m.yes_ask_size,
        "last_price": m.last_price, "volume": m.volume,
        "open_interest": m.open_interest,
        "acted": acted, "side": side,
        "contracts": (fill.contracts if fill and fill.filled else 0),
        "fill_price_cents": (fill.price_cents if fill and fill.filled else float("nan")),
        "fill_reason": (fill.reason if fill else ""),
        "error": error,
        "quote_source": quote_source,
    }


def append(rows: list[dict[str, object]], root: Path | None = None) -> Path | None:
    if not rows:
        return None
    root = root or DATA_ROOT
    now = datetime.now(timezone.utc)
    out = root / "paper" / f"date={now:%Y-%m-%d}"
    out.mkdir(parents=True, exist_ok=True)
    # Microseconds, not seconds: two appends inside the same second
    # silently OVERWROTE each other. Caught 2026-09-20 by a test that
    # ran two passes back to back and lost the first.
    path = out / f"{now:%H%M%S%f}.parquet"
    pd.DataFrame(rows, columns=COLS).to_parquet(path, index=False)
    return path


def read(root: Path | None = None, days: float | None = None) -> pd.DataFrame:
    root = root or DATA_ROOT
    files = sorted(glob.glob(str(root / "paper" / "date=*" / "*.parquet")))
    if days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("date=%Y-%m-%d")
        files = [f for f in files if Path(f).parent.name >= cutoff]
    if not files:
        return pd.DataFrame(columns=COLS)
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def decided(root: Path | None = None, days: float = 7.0) -> set[tuple[str, str]]:
    """(candidate_id, ticker) already decided. A market is traded at most once
    per candidate: the first decision is the one that stands, exactly as it
    would if real money had gone in."""
    df = read(root, days)
    if df.empty:
        return set()
    return set(zip(df["candidate_id"], df["ticker"], strict=True))


def settled(root: Path | None = None) -> pd.DataFrame:
    """Paper decisions joined to outcomes that resolved AFTER they were made.

    The holdout here is per DECISION, not per candidate: `decided_at` is the
    instant the claim was made, and only outcomes later than it may judge it.
    """
    log = read(root)
    if log.empty:
        return log
    res_files = sorted(glob.glob(str((root or DATA_ROOT) / "resolutions" / "date=*" / "*.parquet")))
    if not res_files:
        return log.iloc[0:0]
    res = pd.concat([pd.read_parquet(f) for f in res_files], ignore_index=True)
    res = res[res["outcome"].notna()].drop_duplicates("ticker")
    j = log.merge(res[["ticker", "resolved_at", "outcome"]], on="ticker", how="inner")
    j["decided_at"] = pd.to_datetime(j["decided_at"], utc=True)
    j["resolved_at"] = pd.to_datetime(j["resolved_at"], utc=True)
    return j[j["resolved_at"] > j["decided_at"]].copy()


def pnl(df: pd.DataFrame) -> pd.DataFrame:
    """Realised P&L per contract for settled, acted-on paper trades."""
    t = df[df["acted"] & (df["contracts"] > 0)].copy()
    if t.empty:
        return t
    price = t["fill_price_cents"] / 100.0
    won = ((t["side"] == "yes") & (t["outcome"] == 1)) | \
          ((t["side"] == "no") & (t["outcome"] == 0))
    t["pnl_per_contract"] = won * (1.0 - price) + (~won) * (-price)
    t["pnl_dollars"] = t["pnl_per_contract"] * t["contracts"]
    return t


def pnl_summary(df: pd.DataFrame, cluster: str = "series_ticker") -> dict[str, float]:
    """Mean P&L per contract with a CLUSTERED interval.

    A bare mean over paper rows is wrong here and the error is not small.
    Measured 2026-09-20 over 296 settled trades:

        unclustered   +0.0860 [+0.0488, +0.1233]
        by series     +0.0860 [-0.0020, +0.1741]   <- spans zero
        by event      +0.0860 [+0.0110, +0.1610]

    The cause is LADDERS. 80 of those trades were rungs of five Nasdaq-100
    events -- "above 29409.99", "above 29399.99", "above 29389.99" ... sixteen
    strikes ten points apart on one index at one instant. One close settles all
    sixteen, so they are one observation wearing sixteen hats, and an i.i.d.
    interval treats them as sixteen. That is the same 2.3x under-statement the
    replay bootstrap was fixed for in August; this module reproduced it by
    reporting a raw mean.

    Series is the default because it is the project's convention and the more
    conservative of the two -- events nest inside series, so series clustering
    absorbs ladder correlation AND any shared drift across an underlying.
    """
    t = df[df["acted"] & (df["contracts"] > 0)] if "acted" in df else df
    if t.empty or "pnl_per_contract" not in t:
        return {"pnl_per_contract": float("nan"), "n_trades": 0.0, "n_clusters": 0.0,
                "ci_lo": float("nan"), "ci_hi": float("nan")}
    groups: dict[str, list[float]] = {}
    for key, val in zip(t[cluster], t["pnl_per_contract"], strict=True):
        groups.setdefault(key, []).append(float(val))
    pnl = [x for v in groups.values() for x in v]
    n, k = len(pnl), len(groups)
    mean = sum(pnl) / n
    if k < 2:
        return {"pnl_per_contract": mean, "n_trades": float(n), "n_clusters": float(k),
                "ci_lo": float("nan"), "ci_hi": float("nan")}
    ss = sum((sum(v) - len(v) * mean) ** 2 for v in groups.values())
    se = (ss * k / (k - 1)) ** 0.5 / n
    return {"pnl_per_contract": mean, "n_trades": float(n), "n_clusters": float(k),
            "ci_lo": mean - 1.96 * se, "ci_hi": mean + 1.96 * se,
            "pnl_dollars": float(t["pnl_dollars"].sum())}
