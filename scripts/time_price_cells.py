"""Are prices wrong at particular (time-to-close, price, category) spots? (2026-10-05)

Run:  uv run python scripts/time_price_cells.py   -> data/time-price-cells-2026-10-05.log

MEASUREMENT ONLY. The human-approved "extra idea" after crypto, sports news and
resting orders all failed: not a model of anything, just a map of where, if
anywhere, Kalshi's executable prices are off -- e.g. very near settlement, where
outcomes are almost known and the book may be lazy, or at the extremes.

For every cell of
    time to close  x  price paid for the side bought  x  side  x  category
the entry is the FIRST quote inside the cell for each market -- decidable at the
instant (INVARIANT #8), never the best one -- bought by CROSSING the spread
(buy YES at the ask, buy NO at 1 - bid), taker fee on 100 contracts, spread
<= 8c. P&L is the settled outcome, per contract. Intervals resample whole
(series, close-day) clusters.

MULTIPLE COMPARISONS ARE THE DANGER. There are ~500 cells, so ~12 clear a
one-sided 95% bound by luck alone. The only cells reported as candidates are
those whose bound is above zero overall AND in both halves (split by close
date) AND which hold >= 200 fills in >= 30 clusters. The count of cells tested
and the count passing are printed so the base rate stays visible. A cell that
passes is a lead to be re-measured on later data, never a result.

Resolutions from the last 3 days are dropped (backfill lag skews them).
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experiments.kalshi_quant.scoring import trading_fee_dollars

ROOT = Path(__file__).resolve().parents[1]
SNAPS = ROOT / "data/kalshi_quant/snapshots"
RESOLUTIONS = ROOT / "data/kalshi_quant/resolutions"

TAU_EDGES_MIN = [0, 5, 15, 60, 360, 1440, 4320]
TAU_LABELS = ["<5m", "5-15m", "15-60m", "1-6h", "6-24h", "1-3d"]
PRICE_EDGES = [0.0, 0.10, 0.25, 0.40, 0.60, 0.75, 0.90, 1.0]
PRICE_LABELS = ["<10c", "10-25c", "25-40c", "40-60c", "60-75c", "75-90c", ">=90c"]

FEE_PER = np.array([trading_fee_dollars(p / 100, 100) / 100 for p in range(101)])


def fee(price: np.ndarray) -> np.ndarray:
    return FEE_PER[np.clip(np.round(price * 100).astype(int), 0, 100)]


def category(series: str) -> str:
    s = series[2:]
    if s.startswith(("BTC", "ETH", "SOL", "XRP", "DOGE")):
        return "crypto"
    if s.startswith(("TEMP", "HIGH", "LOWT", "RAIN")):
        return "weather"
    if s.startswith(("NASDAQ", "INX", "WTI", "GOLD", "SILVER", "NATGAS", "EURUSD", "USDJPY")):
        return "indices/commod/fx"
    if s.endswith(("GAME", "MATCH")) or "MATCH" in s or s.startswith(
            ("NFL", "NBA", "MLB", "NHL", "UFC", "ATP", "WTA", "ITF", "CS2", "LOL", "NPB", "KBO")):
        return "sports"
    return "other"


def clustered_ci(x: np.ndarray, cl: np.ndarray, n: int = 1000) -> tuple[float, float]:
    codes, inv = np.unique(cl, return_inverse=True)
    if len(codes) < 2:
        return (np.nan, np.nan)
    sums, cnts = np.bincount(inv, weights=x), np.bincount(inv)
    idx = np.random.default_rng(0).integers(0, len(codes), size=(n, len(codes)))
    m = sums[idx].sum(1) / cnts[idx].sum(1)
    return float(np.quantile(m, 0.05)), float(np.quantile(m, 0.95))   # 90% two-sided = 95% one-sided


def main() -> None:
    parts = []
    for f in RESOLUTIONS.rglob("*.parquet"):
        r = pd.read_parquet(f)
        if "outcome" in r:
            parts.append(r[["ticker", "outcome"]])
    outcome = pd.concat(parts).dropna().drop_duplicates("ticker").set_index("ticker").outcome

    cols = ["ticker", "series_ticker", "observed_at", "close_time", "yes_bid", "yes_ask",
            "is_mve"]
    q = ds.dataset(SNAPS, format="parquet", partitioning="hive").to_table(columns=cols).to_pandas()
    q = q[~q.is_mve.fillna(False).astype(bool) & q.ticker.isin(outcome.index)]
    for c in ("observed_at", "close_time"):
        q[c] = pd.to_datetime(q[c], utc=True)
    q = q[q.close_time < pd.Timestamp.now(tz="UTC") - timedelta(days=3)]
    q["tau"] = (q.close_time - q.observed_at).dt.total_seconds() / 60
    q = q[(q.tau > 0) & (q.tau <= TAU_EDGES_MIN[-1]) & (q.yes_ask - q.yes_bid <= 0.08 + 1e-9)
          & (q.yes_bid > 0) & (q.yes_ask < 1)]
    q["tau_b"] = pd.cut(q.tau, TAU_EDGES_MIN, labels=TAU_LABELS, right=True)
    q["cat"] = q.series_ticker.map(category)
    q["y"] = q.ticker.map(outcome).astype(float).values
    print(f"{len(q):,} quotes, {q.ticker.nunique():,} resolved markets, "
          f"{q.close_time.min():%Y-%m-%d}..{q.close_time.max():%Y-%m-%d}")

    sides = []
    for side, px, win in (("yes", q.yes_ask, q.y), ("no", 1 - q.yes_bid, 1 - q.y)):
        s = q.assign(side=side, px=px, win=win)
        s["price_b"] = pd.cut(s.px, PRICE_EDGES, labels=PRICE_LABELS, right=False)
        sides.append(s)
    s = pd.concat(sides)
    # First quote per (market, cell): decidable at the instant, never the best.
    s = s.sort_values("observed_at")
    s = s.drop_duplicates(["ticker", "side", "tau_b", "price_b"], keep="first")
    s["pnl"] = s.win - s.px - fee(s.px.values)
    s["cluster"] = s.series_ticker + s.close_time.dt.strftime("%Y%m%d")
    split = s.close_time.quantile(0.5)
    s["half"] = np.where(s.close_time < split, 0, 1)

    print(f"\nALL cells pooled: {s.pnl.mean():+.4f} per contract over {len(s):,} entries "
          f"(the average cost of crossing)")
    rows = []
    for key, g in s.groupby(["cat", "side", "tau_b", "price_b"], observed=True):
        if len(g) < 200 or g.cluster.nunique() < 30:
            continue
        lo, hi = clustered_ci(g.pnl.values, g.cluster.values)
        h = [clustered_ci(x.pnl.values, x.cluster.values) if len(x) >= 50 else (np.nan, np.nan)
             for _, x in g.groupby("half")]
        rows.append((*key, len(g), g.cluster.nunique(), g.pnl.mean(), lo, hi,
                     h[0][0] if len(h) > 0 else np.nan, h[1][0] if len(h) > 1 else np.nan))
    t = pd.DataFrame(rows, columns=["cat", "side", "tau", "price", "n", "clusters", "mean",
                                    "lb", "ub", "lb_half1", "lb_half2"])
    both = t[(t.lb > 0) & (t.lb_half1 > 0) & (t.lb_half2 > 0)]
    print(f"{len(t)} cells with >=200 fills in >=30 clusters; "
          f"{(t.lb > 0).sum()} have an overall 95% lower bound > 0 "
          f"(~{0.05 * len(t):.0f} expected by luck alone); "
          f"{len(both)} also positive in BOTH halves")
    pd.set_option("display.width", 200)
    print("\nPASSING BOTH HALVES (leads, not results):")
    print(both.sort_values("lb", ascending=False).round(4).to_string(index=False)
          if len(both) else "  none")
    print("\nBEST 12 BY POINT ESTIMATE (for scale):")
    print(t.sort_values("mean", ascending=False).head(12).round(4).to_string(index=False))
    print("\nBY TIME TO CLOSE, all categories/prices pooled:")
    for k, g in s.groupby("tau_b", observed=True):
        print(f"  {k:7s} {len(g):9,d} entries {g.pnl.mean():+.4f}")
    print("\nBY PRICE PAID, pooled:")
    for k, g in s.groupby("price_b", observed=True):
        print(f"  {k:7s} {len(g):9,d} entries {g.pnl.mean():+.4f}")


if __name__ == "__main__":
    main()
