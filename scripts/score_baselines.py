"""Score every seed candidate against the captured data.

    uv run python scripts/score_baselines.py

Phase 2 plumbing check. Reads what capture has accumulated, runs each control
through avo.core.holdout and experiments.kalshi_quant.scoring, and prints the
result. Three controls should sit at or below zero; baseline_sharpened is a
tripwire and is expected POSITIVE while the fitness denominator is biased --
see docs/ROADMAP.md Phase 2.
"""
from __future__ import annotations

from avo.core import registry
from experiments.kalshi_quant.experiment import passes_pnl_gate
from experiments.kalshi_quant.observations import (
    ENTRY_POLICY,
    SeriesHistory,
    load_entries,
)

EXPECTED = {
    "baseline_market": "exactly 0.0000 by construction",
    "baseline_base_rate": "clearly negative",
    "baseline_shrunk": "slightly negative",
    "baseline_sharpened": "POSITIVE while the denominator is biased (tripwire)",
}


def main() -> None:
    exp = registry.load("kalshi_quant")
    entries = load_entries()
    if not entries:
        raise SystemExit("no observations yet; need both snapshots and resolutions")
    history = SeriesHistory(entries)
    print(f"{len(entries):,} observations   entry policy: {ENTRY_POLICY}")
    print(f"resolved {min(e.resolved_at for e in entries):%Y-%m-%d %H:%M} .. "
          f"{max(e.resolved_at for e in entries):%Y-%m-%d %H:%M} UTC")
    print(f"series: {len({e.market.series_ticker for e in entries})}\n")

    rows = []
    for c in sorted(exp.seed_candidates(), key=lambda c: c.candidate_id):
        s = exp.score(c, exp.load_candidate(c), entries=entries, history=history)
        rows.append((c.candidate_id, s))

    w = max(len(n) for n, _ in rows)
    print(f"{'candidate':<{w}}  {'skill':>8}  {'95% CI (clustered)':>22}"
          f"  {'95% CI (i.i.d.)':>22}  {'x':>4}  {'n':>7}")
    for name, s in rows:
        ci = f"[{s.primary_ci[0]:+.4f},{s.primary_ci[1]:+.4f}]"
        ii = (f"[{s.secondary.get('skill_ci_iid_lo', float('nan')):+.4f},"
              f"{s.secondary.get('skill_ci_iid_hi', float('nan')):+.4f}]")
        r = s.secondary.get("skill_ci_width_ratio", float("nan"))
        print(f"{name:<{w}}  {s.primary:+8.4f}  {ci:>22}  {ii:>22}  {r:>4.1f}"
              f"  {s.n_observations:>7,}")
    print("  clustered is the honest interval; 'x' is how many times wider it is "
          "than i.i.d.\n  a large ratio means the result rests on a few series.")

    # Brier skill and money can point in opposite directions. Print them side
    # by side so a positive skill number is never read as an edge on its own.
    print(f"\n{'candidate':<{w}}  {'P&L/contract':>13}  {'95% CI (series-clustered)':>28}  {'fills':>7}")
    for name, s in rows:
        m = s.secondary.get("pnl_per_contract", float("nan"))
        se = s.secondary.get("pnl_se_clustered", float("nan"))
        n = int(s.secondary.get("pnl_n_fills", 0))
        if not n:
            # A candidate that never disagrees with the market takes no
            # position. That is not a missing number, it is a flat book.
            print(f"{name:<{w}}  {'--':>13}  {'never disagrees with market':>28}  {0:>7}")
            continue
        ci = f"[{m - 1.96 * se:+.4f},{m + 1.96 * se:+.4f}]" if se == se else "n/a"
        verdict = ""
        if se == se:
            verdict = " PROFITABLE" if m - 1.96 * se > 0 else (
                " losing" if m + 1.96 * se < 0 else " ~zero")
        print(f"{name:<{w}}  {m:>+13.4f}  {ci:>28}  {n:>7,}{verdict}")

    # The gate. Brier skill is the search signal; this is the objective.
    print(f"\n{'candidate':<{w}}  P&L gate")
    for name, s in rows:
        ok, why = passes_pnl_gate(s)
        print(f"{name:<{w}}  {'PASS' if ok else 'FAIL'}  {why}")

    print(f"\nmarket brier (the denominator): "
          f"{rows[0][1].secondary.get('market_brier', float('nan')):.4f}")
    print(f"fill rate: {rows[0][1].secondary.get('pnl_fill_rate', 0):.1%} "
          f"(simulate_fill accepts; the rest are scoreable but not tradeable)")
    print(f"entry staleness: median "
          f"{rows[0][1].secondary.get('staleness_median_min', float('nan')):.0f} min, "
          f"p90 {rows[0][1].secondary.get('staleness_p90_min', float('nan')):.0f} min "
          f"(inflates skill, not P&L -- see observations.Entry.staleness_minutes)")
    print("\nexpected:")
    for name, note in EXPECTED.items():
        print(f"  {name:<{w}}  {note}")


if __name__ == "__main__":
    main()
