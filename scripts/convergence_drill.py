"""Drill into the one family time_price_cells.py found (2026-10-05): buying the
>=90c YES side of crypto / weather markets in the last hour before close.

Run:  uv run python scripts/convergence_drill.py
MEASUREMENT ONLY. Same entry rule as time_price_cells.py (first quote in the
cell, crossing the spread, taker fee), plus what that scan ignored: visible
depth, which ask price the gain sits at, how concentrated it is by series and
by day, and the size of the losing tail. A lead is only worth chasing if it
survives these.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from time_price_cells import RESOLUTIONS, SNAPS, category, clustered_ci, fee  # noqa: E402


def main() -> None:
    parts = [pd.read_parquet(f)[["ticker", "outcome"]] for f in RESOLUTIONS.rglob("*.parquet")
             if "outcome" in pd.read_parquet(f).columns]
    outcome = pd.concat(parts).dropna().drop_duplicates("ticker").set_index("ticker").outcome
    cols = ["ticker", "series_ticker", "observed_at", "close_time", "yes_bid", "yes_ask",
            "yes_ask_size", "is_mve"]
    q = ds.dataset(SNAPS, format="parquet", partitioning="hive").to_table(columns=cols).to_pandas()
    q = q[~q.is_mve.fillna(False).astype(bool) & q.ticker.isin(outcome.index)]
    for c in ("observed_at", "close_time"):
        q[c] = pd.to_datetime(q[c], utc=True)
    q = q[q.close_time < pd.Timestamp.now(tz="UTC") - timedelta(days=3)]
    q["tau"] = (q.close_time - q.observed_at).dt.total_seconds() / 60
    q["cat"] = q.series_ticker.map(category)
    q = q[(q.tau > 0) & (q.tau <= 60) & q.cat.isin(["crypto", "weather"])
          & (q.yes_ask >= 0.90) & (q.yes_ask < 1) & (q.yes_ask - q.yes_bid <= 0.08 + 1e-9)
          & (q.yes_bid > 0)]
    q["win"] = q.ticker.map(outcome).astype(float).values
    q = q.sort_values("observed_at").drop_duplicates("ticker", keep="first")   # first signal
    q["px"] = q.yes_ask
    q["pnl"] = q.win - q.px - fee(q.px.values)
    q["cluster"] = q.series_ticker + q.close_time.dt.strftime("%Y%m%d")
    q["ticks"] = np.round(q.px * 100).astype(int)
    print(f"{len(q):,} markets (one entry each), mean {q.pnl.mean():+.4f}, win rate "
          f"{q.win.mean():.2%}, average price {q.px.mean():.3f}")
    lo, hi = clustered_ci(q.pnl.values, q.cluster.values)
    print(f"90% clustered interval [{lo:+.4f},{hi:+.4f}] over {q.cluster.nunique()} clusters")

    print("\nDEPTH: contracts fillable at 25% of visible ask size (simulate_fill's cap)")
    cap = (q.yes_ask_size.fillna(0) * 0.25).astype(int)
    for k in (1, 10, 50, 100):
        g = q[cap >= k]
        print(f"  cap >= {k:3d}: {len(g):6,d} entries ({len(g) / len(q):5.1%})  {g.pnl.mean():+.4f}")

    print("\nBY ASK PRICE (cents)")
    for lo_, hi_ in ((90, 93), (94, 95), (96, 96), (97, 97), (98, 98), (99, 99)):
        g = q[(q.ticks >= lo_) & (q.ticks <= hi_)]
        if len(g):
            print(f"  {lo_}-{hi_}c: {len(g):6,d} win {g.win.mean():.2%} vs implied "
                  f"{g.px.mean():.2%}  pnl {g.pnl.mean():+.4f}")

    print("\nBY CATEGORY AND SERIES (top 8 by entries)")
    for c, g in q.groupby("cat"):
        print(f"  {c}: {len(g):,} entries {g.pnl.mean():+.4f}")
    top = q.groupby("series_ticker").agg(n=("pnl", "size"), pnl=("pnl", "mean"),
                                         tot=("pnl", "sum")).sort_values("n", ascending=False)
    print(top.head(8).round(4).to_string())
    print(f"  largest single series share of total net P&L: "
          f"{top.tot.max() / q.pnl.sum():.0%}")

    print("\nBY CLOSE DATE (weekly)")
    q["wk"] = q.close_time.dt.strftime("%m-%d").str[:5]
    wk = q.groupby(q.close_time.dt.to_period("W").astype(str)).pnl.agg(["size", "mean"])
    print(wk.round(4).to_string())

    losses = q[q.win == 0]
    print(f"\nTAIL: {len(losses):,} losing entries ({len(losses) / len(q):.2%}); each loses "
          f"~{losses.px.mean() + 0.0:.2f}; they cost {losses.pnl.sum() / len(q):+.4f} per entry, "
          f"the {q.win.sum():,.0f} wins add {q[q.win == 1].pnl.sum() / len(q):+.4f}")
    worst_day = q.groupby(q.close_time.dt.date).pnl.sum().min()
    print(f"worst single close-day, per contract summed over entries: {worst_day:+.2f}")


if __name__ == "__main__":
    main()
