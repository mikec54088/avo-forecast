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
    print(f"{'candidate':<{w}}  {'skill':>8}  {'95% CI':>20}  {'brier':>7}  {'n':>6}")
    for name, s in rows:
        ci = f"[{s.primary_ci[0]:+.4f},{s.primary_ci[1]:+.4f}]"
        cb = s.secondary.get("candidate_brier", float("nan"))
        print(f"{name:<{w}}  {s.primary:+8.4f}  {ci:>20}  {cb:7.4f}  {s.n_observations:>6,}")

    print(f"\nmarket brier (the denominator): "
          f"{rows[0][1].secondary.get('market_brier', float('nan')):.4f}")
    print(f"fillable fraction: {rows[0][1].secondary.get('fillable_fraction', 0):.1%} "
          f"(simulate_fill accepts; the rest are scoreable but not tradeable)")
    print("\nexpected:")
    for name, note in EXPECTED.items():
        print(f"  {name:<{w}}  {note}")


if __name__ == "__main__":
    main()
