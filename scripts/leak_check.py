"""Before believing a candidate's edge, look for the shapes a leak leaves.

Written 2026-09-28, after unclimbed_favourite's +4.7c/contract turned out to be
the backtest's entry rule catching early-settling markets just before a sudden
YES. Run it on any candidate whose P&L is outside the 1-3 point range every
measured bias sits in, BEFORE building anything on it:

    uv run python scripts/leak_check.py CANDIDATE_ID

It prints, for the candidate's fills under the current ENTRY_POLICY:

  1. P&L by time from entry to resolution. Money concentrated in entries made
     minutes before the outcome was known is the signature of timing leaking
     information; a real edge on information in the book should not care.
  2. P&L by whether the market settled well before its scheduled close. Markets
     that can settle early on one side (props, totals, "both teams score") are
     where the last leak lived.
  3. Concentration: the share of net P&L from the single largest series. An
     edge that is one series is one bet.
  4. The HINDSIGHT CONTRAST: the same markets entered at the candidate's LAST
     signal instead of its first. Hindsight always helps, but if the edge only
     exists there, the backtest was measuring hindsight.

Each check prints WARN when it trips. Warnings are prompts to look, not
verdicts; the verdict comes from paper trading.
"""
from __future__ import annotations

import argparse
import math
from collections import defaultdict

from avo.core import registry
from avo.core.holdout import eligible
from experiments.kalshi_quant import entries_store
from experiments.kalshi_quant.experiment import accumulate_fills, pnl_stats
from experiments.kalshi_quant.observations import acts, first_signal

LEAD_BANDS = [(0, 1, "<1h"), (1, 6, "1-6h"), (6, 24, "6-24h"), (24, 1e9, ">24h")]
EARLY_SETTLE_H = 6.0       # resolved this long before its scheduled close
WARN_LAST_HOUR_SHARE = 0.5
WARN_SERIES_SHARE = 0.3


def _fmt(by_series: dict[str, list[float]]) -> str:
    n = sum(map(len, by_series.values()))
    if not n:
        return "no fills"
    s = pnl_stats(by_series, n)
    m, se = s["pnl_per_contract"], s["pnl_se_clustered"]
    return (f"{m:+.4f} [{m - 1.96 * se:+.4f},{m + 1.96 * se:+.4f}]  "
            f"{n:,} fills / {int(s['pnl_n_series'])} series")


def _last_signal(path, forecast, history):
    last = None
    for i in range(len(path.decisions)):
        e = path.entry_at(i)
        p = float(forecast(e.market, history.context_for(e)))
        if acts(p, e.market.implied_prob):
            last = (e, p)
    return last


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("candidate")
    args = ap.parse_args()

    exp = registry.load("kalshi_quant")
    cand = next(c for c in exp.seed_candidates() if c.candidate_id == args.candidate)
    mod = exp.load_candidate(cand)
    history = entries_store.series_history()
    if history is None:
        raise SystemExit("entries table cold or stale; run `avo entries build`")

    total: dict[str, list[float]] = {}
    lead: dict[str, dict[str, list[float]]] = defaultdict(dict)
    settle: dict[str, dict[str, list[float]]] = defaultdict(dict)
    first_mkts: dict[str, list[float]] = {}
    last_mkts: dict[str, list[float]] = {}
    for chunk in entries_store.iter_paths():
        for path in eligible(cand, list(chunk), lambda p: p.resolved_at):
            try:
                e, p = first_signal(path, mod.forecast, history)
            except Exception:  # noqa: BLE001, S112 -- a broken candidate is scored elsewhere
                continue
            if not acts(p, e.market.implied_prob):
                continue
            one: dict[str, list[float]] = {}
            accumulate_fills([e], [p], one)
            if not one:
                continue                       # no fill at that quote
            (s, v), = one.items()
            total.setdefault(s, []).extend(v)
            hours = (e.resolved_at - e.market.observed_at).total_seconds() / 3600
            band = next(lab for lo, hi, lab in LEAD_BANDS if lo <= hours < hi)
            lead[band].setdefault(s, []).extend(v)
            early = (e.market.close_time - e.resolved_at).total_seconds() / 3600 > EARLY_SETTLE_H
            settle["settled early" if early else "settled at close"].setdefault(s, []).extend(v)
            first_mkts.setdefault(s, []).extend(v)
            last = _last_signal(path, mod.forecast, history)
            if last is not None:
                accumulate_fills([last[0]], [last[1]], last_mkts)

    print(f"{args.candidate}: {_fmt(total)}\n")
    print("1. by time from entry to resolution")
    net = sum(sum(v) for v in total.values()) or float("nan")
    for _, _, lab in LEAD_BANDS:
        print(f"   {lab:<6} {_fmt(lead.get(lab, {}))}")
    near = sum(sum(v) for v in lead.get("<1h", {}).values())
    if net > 0 and near / net > WARN_LAST_HOUR_SHARE:
        print(f"   WARN {near / net:.0%} of net P&L comes from entries <1h before the "
              "outcome was known")
    print("\n2. by how the market settled")
    for lab in ("settled early", "settled at close"):
        print(f"   {lab:<17} {_fmt(settle.get(lab, {}))}")
    print("\n3. concentration")
    by = sorted(((sum(v), s) for s, v in total.items()), reverse=True)
    for amt, s in by[:5]:
        print(f"   {s:<28} {amt:+9.2f}")
    if net > 0 and by and by[0][0] / net > WARN_SERIES_SHARE:
        print(f"   WARN the largest series is {by[0][0] / net:.0%} of net P&L")
    print("\n4. hindsight contrast (same candidate, same markets)")
    print(f"   first signal (the policy)  {_fmt(first_mkts)}")
    print(f"   last signal  (hindsight)   {_fmt(last_mkts)}")
    fs = pnl_stats(first_mkts, 1)["pnl_per_contract"] if first_mkts else float("nan")
    ls = pnl_stats(last_mkts, 1)["pnl_per_contract"] if last_mkts else float("nan")
    if not (math.isnan(fs) or math.isnan(ls)) and ls - fs > 0.03:
        print(f"   WARN entering later is worth {ls - fs:+.4f}/contract: the edge "
              "leans on WHEN the market settles, which no trader knows")


if __name__ == "__main__":
    main()
