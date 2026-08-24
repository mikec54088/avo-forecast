# avo-forecast — working brief for Claude Code

An AVO-style long-horizon autonomous agent framework. A stable core drives an
LLM agent that writes and refines *candidates*; each **experiment** is a domain
adapter. `kalshi_quant` (forecasting prediction markets) is experiment #1;
others will follow, and the core is expected to transfer unchanged.

Read `docs/DESIGN.md` for reasoning, `docs/ROADMAP.md` for build order,
`docs/EXPERIMENTS.md` for the experiment model.

---

## STOP-AND-ASK gates

You resolve most ambiguity yourself. **Not these.** Each one invalidates prior
work if guessed wrong, so stop and ask the human rather than picking a default:

- **G1.** Any change to the candidate contract signature
  (`forecast(market, context) -> float`). Adding a field to `ForecastContext`
  counts. Every previously generated candidate becomes incomparable.
- **G2.** Any change to the fitness definition in
  `experiments/kalshi_quant/scoring.py`. Rescoring changes every ranking.
- **G3.** Anything that would relax an invariant below, including "just for
  debugging" or "temporarily".
- **G4.** Starting a new phase from `docs/ROADMAP.md`. Report at each phase gate.
- **G5.** Creating a new experiment directory, or promoting `kalshi_research`
  from `status = "planned"`.

If a task appears to require one of these, say so and stop. Do not work around it.

---

## Non-negotiable invariants

1. **Strict temporal holdout.** A candidate is scored ONLY on observations whose
   ground truth resolved strictly after `candidate.created_at`. Enforced in
   `core/holdout.py`, tested in `tests/test_holdout.py`. No bypass flag, ever.
2. **Score relative to the market.** Fitness is Brier skill vs the market's
   implied probability at entry — not raw Brier, not accuracy, not P&L. Beating
   50/50 is meaningless.
3. **`core/` never imports from `experiments/`.** No `if experiment == "..."`
   in core. If core needs something domain-specific, widen the protocol in
   `core/interfaces.py` — do not special-case.
4. **Pessimistic fills.** Cross the spread, cap at a fraction of visible depth,
   apply fees. Never fill at midpoint.
5. **Run artifacts are never committed.** `data/`, `runs/` — both gitignored.
6. **One venue.** Kalshi only. Do not add Polymarket. Cross-venue arbitrage is
   the easiest thing for the agent to find and is not the research question.
7. **One backend per run**, recorded in `RunState.backend`. Never mix models
   mid-run; it destroys comparability.

---

## Current phase: 0 + 1

Do NOT build memory, selection, or the supervisor. They raise
`NotImplementedError` deliberately — building them before real scoring data
exists means tuning heuristics against imagination.

### Priorities, in order

**P0 — get snapshots flowing today. Nothing else is calendar-bound.**

Top-of-book at 2pm today is gone forever if nothing captured it. Resolutions
are NOT in this category — Kalshi keeps settled markets queryable via
`status=settled`, so the settle path can be backfilled later. Snapshots cannot.
Optimize P0 for "running tonight, imperfect" over "correct next week".

0. **Run `python -m experiments.kalshi_quant.capture snapshot` and make it
   work.** `client.py` was written from public docs and never executed. Market
   data is public — no auth, no RSA signing (that is only for orders, which
   this project never places). Base URL
   `https://external-api.kalshi.com/trade-api/v2`. Confirm response field
   names; docs show both `yes_bid`/`volume` and `yes_bid_dollars`/`volume_fp`
   variants. Fix `types.py` to match reality.
   Then install `scripts/cron.example` (the snapshot line at minimum) and
   confirm two consecutive runs land Parquet under `data/kalshi_quant/`.
   **Do not proceed to P1 until cron is verified running.**

1. **Verify the settlement field.** In `capture.py`, `settle()` assumes a
   `result` field of `'yes'|'no'`. This is the single most important field in
   the project — a wrong mapping silently inverts every score. Verify against a
   real settled market. Backfillable, so it does not block P0.

2. **Make the three baselines score end to end** on whatever data has
   accumulated. They must land near zero skill and be statistically
   indistinguishable from each other. If any shows strong positive skill, the
   scorer is broken — find the bug, do not celebrate.

3. **Stop and report.** Phase gate G4.

---

## Conventions

- Python 3.11+, `uv`, ruff, pytest. `mypy --strict` passes on `src/avo/core`.
- Dataclasses over dicts across module boundaries; type hints everywhere.
- No network in tests; fixtures in `tests/fixtures/`.
- Every candidate module exposes `forecast()` and a `MANIFEST` dict — see
  `experiments/kalshi_quant/candidates/baseline_market.py`.
- Domain types live in the experiment, never in `core/`.
