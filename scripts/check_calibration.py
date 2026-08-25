"""Calibration of the market's implied probability at entry.

INVARIANT #2 makes the market's own probability the fitness denominator, so a
bias in that estimator is not cosmetic: a candidate could post positive skill by
correcting the bias rather than by forecasting anything.

Recorded 2026-08-24 on 1,154 observations from a single afternoon across 58
series, the bid-ask midpoint was shaded systematically toward 0.5 -- low bins
resolved lower than quoted, high bins higher, monotonically across all six.
Re-run this once several days have accumulated to see whether it survives.

    uv run python scripts/check_calibration.py [--min-obs N]

Reports per-bin predicted vs actual for the midpoint and, for comparison, for
the bid and the ask. If the midpoint's bias is a spread artifact, the bid should
be biased low and the ask high, bracketing the outcome -- which would argue for
a spread-aware market_prob rather than the raw midpoint (a G2 decision).
"""
from __future__ import annotations

import argparse
import glob
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1] / "data" / "kalshi_quant"
BINS = [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0]


def load() -> pd.DataFrame:
    res_files = sorted(glob.glob(str(ROOT / "resolutions" / "date=*" / "*.parquet")))
    snap_files = sorted(glob.glob(str(ROOT / "snapshots" / "date=*" / "*.parquet")))
    if not res_files or not snap_files:
        raise SystemExit("need both snapshots and resolutions on disk")
    res = pd.concat([pd.read_parquet(f) for f in res_files], ignore_index=True)
    res = res[res["outcome"].notna()].drop_duplicates("ticker")
    snap = pd.concat(
        [pd.read_parquet(f, columns=["ticker", "observed_at", "yes_bid", "yes_ask"])
         for f in snap_files],
        ignore_index=True,
    )
    j = snap.merge(res[["ticker", "resolved_at", "outcome"]], on="ticker", how="inner")
    # Entry is the last observation strictly before resolution -- the same
    # ordering INVARIANT #1 enforces for candidates.
    j = j[j["observed_at"] < j["resolved_at"]]
    j = j.sort_values("observed_at").groupby("ticker", as_index=False).last()
    j["mid"] = (j["yes_bid"] + j["yes_ask"]) / 2.0
    j["spread"] = j["yes_ask"] - j["yes_bid"]
    return j


def table(j: pd.DataFrame, col: str) -> pd.DataFrame:
    b = j.assign(bin=pd.cut(j[col], BINS))
    g = b.groupby("bin", observed=True).agg(
        n=("outcome", "size"), predicted=(col, "mean"), actual=("outcome", "mean")
    )
    g["gap"] = g["actual"] - g["predicted"]
    return g


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-obs", type=int, default=200)
    args = ap.parse_args()

    j = load()
    print(f"observations: {len(j):,}   series: {j['ticker'].str.split('-').str[0].nunique()}")
    print(f"median spread: {j['spread'].median():.4f}   base rate: {j['outcome'].mean():.3f}")
    print(f"market Brier (mid): {((j['mid'] - j['outcome']) ** 2).mean():.4f}"
          f"   always-0.5: {((0.5 - j['outcome']) ** 2).mean():.4f}")
    if len(j) < args.min_obs:
        print(f"\nWARNING: fewer than {args.min_obs} observations; treat as noise.")

    for col in ("mid", "yes_bid", "yes_ask"):
        print(f"\n--- {col} ---")
        print(table(j, col).to_string())

    g = table(j, "mid")
    low = g[g.index.map(lambda i: i.right <= 0.5)]["gap"]
    high = g[g.index.map(lambda i: i.left >= 0.5)]["gap"]
    if len(low) and len(high):
        print(f"\nmean gap below 0.5: {low.mean():+.4f}   above 0.5: {high.mean():+.4f}")
        if low.mean() < 0 < high.mean():
            print("Shading toward 0.5 PERSISTS -- decide whether market_prob stays "
                  "the midpoint (G2).")
        else:
            print("No consistent shading in this sample.")


if __name__ == "__main__":
    main()
