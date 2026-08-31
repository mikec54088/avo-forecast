"""Calibration of the market's implied probability at entry, and whether that
bias is exploitable by a candidate.

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

Then probes whether the bias is worth skill. THE CONTROL SET IS ASYMMETRIC:
baseline_market sits on the price and baseline_shrunk pulls TOWARD 0.5, so
nothing in it moves away from 0.5 -- which is the direction the bias lies in.
Measured 2026-08-24 on 2,921 observations, a two-line logit sharpen scored
+0.0195 skill, CI [+0.0121, +0.0273], while all three baselines behaved exactly
as documented. ROADMAP Phase 2's "three baselines score ~0 skill" is therefore
necessary but NOT sufficient: it can pass with an exploitable denominator.

The skill was concentrated where the book is wide (+0.0209, CI excludes zero)
and was not distinguishable from zero on fillable markets (+0.0152, CI
[-0.0005, +0.0320]) -- i.e. it lives exactly where simulate_fill refuses to
trade. Note the CIs here use scoring.py's i.i.d. bootstrap; observations are
clustered by series, so the true intervals are wider than printed.
"""
from __future__ import annotations

import argparse
import glob
import math
from pathlib import Path

import pandas as pd

from experiments.kalshi_quant.observations import MAX_ENTRY_STALENESS_MINUTES
from experiments.kalshi_quant.scoring import Observation, bootstrap_ci, skill_score

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
    # Same staleness cap the scorer applies, so this report and score_baselines
    # describe the same observation set rather than quietly diverging.
    stale = (
        pd.to_datetime(j["resolved_at"], utc=True)
        - pd.to_datetime(j["observed_at"], utc=True)
    ).dt.total_seconds() / 60.0
    j = j[stale <= MAX_ENTRY_STALENESS_MINUTES]
    j["mid"] = (j["yes_bid"] + j["yes_ask"]) / 2.0
    j["spread"] = j["yes_ask"] - j["yes_bid"]
    j["stale_min"] = (
        pd.to_datetime(j["resolved_at"], utc=True)
        - pd.to_datetime(j["observed_at"], utc=True)
    ).dt.total_seconds() / 60.0
    return j


def table(j: pd.DataFrame, col: str) -> pd.DataFrame:
    b = j.assign(bin=pd.cut(j[col], BINS))
    g = b.groupby("bin", observed=True).agg(
        n=("outcome", "size"), predicted=(col, "mean"), actual=("outcome", "mean")
    )
    g["gap"] = g["actual"] - g["predicted"]
    return g


def sharpen(p: float, k: float) -> float:
    """Push a probability away from 0.5 in logit space. k>1 sharpens."""
    p = min(max(p, 1e-6), 1.0 - 1e-6)
    return 1.0 / (1.0 + math.exp(-math.log(p / (1.0 - p)) * k))


def probe(j: pd.DataFrame, label: str) -> None:
    """Score simple transforms against the market, using the project's scorer.

    These are diagnostics of the fitness function, not candidates. If a transform
    this trivial earns skill, the denominator is exploitable and a generated
    candidate will find it.
    """
    print(f"\n{label}  n={len(j):,}")
    transforms = [
        ("baseline_market", lambda p: p),
        ("baseline_shrunk (->0.5)", lambda p: p + 0.2 * (0.5 - p)),
        ("sharpen k=1.25 (away)", lambda p: sharpen(p, 1.25)),
        ("sharpen k=1.5  (away)", lambda p: sharpen(p, 1.5)),
        ("sharpen k=2.0  (away)", lambda p: sharpen(p, 2.0)),
    ]
    for name, f in transforms:
        obs = [Observation(f(p), p, int(o))
               for p, o in zip(j["mid"], j["outcome"], strict=True)]
        _, _, sk = skill_score(obs)
        lo, hi = bootstrap_ci(obs, n=400)
        flag = "  <-- EXPLOITABLE" if lo > 0 else ""
        print(f"  {name:<26} skill={sk:+.4f}  95% CI [{lo:+.4f},{hi:+.4f}]{flag}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-obs", type=int, default=200)
    args = ap.parse_args()

    j = load()
    print(f"observations: {len(j):,}   series: {j['ticker'].str.split('-').str[0].nunique()}")
    print(f"entry staleness: median {j['stale_min'].median():.0f} min   "
          f"p90 {j['stale_min'].quantile(0.9):.0f} min   max {j['stale_min'].max():.0f} min")
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
            print("Shading toward 0.5 is present in this sample.")
        else:
            print("No consistent shading in this sample.")
    print(
        "NOTE: shading alone does NOT mean market_prob needs changing. Split by\n"
        "staleness below -- on fresh, tight books the midpoint measured unbiased\n"
        "(+0.0011, CI [-0.002,+0.004], n=13,255 on 2026-08-31), and the apparent\n"
        "bias was concentrated in stale, wide ones. ENTRY_POLICY now caps entry\n"
        "staleness, so this script run against capped data should show far less."
    )

    print("\n" + "=" * 72)
    print("EXPLOITABILITY PROBE - diagnostics of the fitness, not candidates.")
    print("CIs use scoring.py's i.i.d. bootstrap; observations cluster by series,")
    print("so the true intervals are WIDER than shown.")
    probe(j, "ALL")
    tight, wide = j[j["spread"] <= 0.08], j[j["spread"] > 0.08]
    if len(tight) >= args.min_obs:
        probe(tight, "FILLABLE (spread <= 0.08, simulate_fill's cap)")
    if len(wide) >= args.min_obs:
        probe(wide, "WIDE (spread > 0.08, not fillable)")

    # Staleness x spread. This is the split that showed the 2026-08-24
    # "midpoint is biased" finding to be mostly a staleness artifact: an old
    # price has not absorbed the drift toward the outcome, so sharpening
    # recovers drift rather than forecasting anything.
    print("\n" + "=" * 72)
    print("STALENESS x SPREAD -- where apparent skill actually comes from")
    print("(entries are capped by ENTRY_POLICY, so the stale rows may be empty)")
    for lo, hi, lbl in ((0, 20, "fresh <=20m"), (20, 90, "20-90m"),
                        (90, 10 ** 9, "stale >90m")):
        band = j[(j["stale_min"] >= lo) & (j["stale_min"] < hi)]
        for tight_side in (True, False):
            x = band[(band["spread"] <= 0.08) == tight_side]
            if len(x) < args.min_obs:
                continue
            probe(x, f"{lbl} / {'TIGHT' if tight_side else 'WIDE'}")


if __name__ == "__main__":
    main()
