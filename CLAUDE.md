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

## Current phase: 5 — the generational loop

Phases 0-4 are done; see `docs/ROADMAP.md` for what each concluded. The loop
runs via `uv run python -m avo.cli evolve kalshi_quant`, and
`uv run python -m avo.cli rank kalshi_quant` scores and confirms without
generating.

Phase 6 (the supervisor) is still deliberately unbuilt, and so is `PriorsStore`
— cross-run priors tuned against two runs would be heuristics fitted to
imagination.

### The standing priority

**If capture stops, fixing it outranks everything else.** Top-of-book at 2pm
today is gone forever if nothing captured it. Resolutions are NOT in this
category — Kalshi keeps settled markets queryable, so `settle()` is
backfillable. Snapshots are not, and that asymmetry never expires.

Capture is three launchd agents, NOT cron — on macOS cron is TCC-blocked and
fails completely silently. See `scripts/launchd/README.md`. Verify by checking
that data lands, never that a job is loaded:

    launchctl list | grep avoforecast          # 2nd column is last exit status
    ls data/kalshi_quant/snapshots/date=*/     # near pass every 15 min

### What has actually been learned

Read this before proposing anything; most obvious ideas are already dead.

- **Generation 1 was a clean null.** 26 candidates on ~56,000 resolved markets,
  every one failing the P&L gate. The favourite-longshot bias measured 3-4.5
  points in-sample and did not replicate forward.
- **The cost hurdle is ~2 probability points** (1c half-spread + 1c fee) even on
  the tightest books, while every bias measured is 1-3 points. "Beat the
  midpoint" is not the target; "beat it by >2 points on a subset identifiable in
  advance" is.
- **Skill and money disagree.** `microprice_fair_value` had the best skill in
  the set and significantly lost money. Always read `avo rank` and the P&L gate
  together.
- **Intervals must be series-clustered.** The i.i.d. bootstrap ran 2.3x too
  narrow; `primary_ci` is clustered and the i.i.d. one is kept only as a
  diagnostic ratio.
- **Entry staleness inflates skill but not P&L**, which is why `ENTRY_POLICY`
  caps it at 60 minutes.
- **`liquidity` is always 0.0** — 610,077 rows, no exceptions. A candidate
  gating on it silently becomes a constant.
- **Not every event is mutually exclusive.** Nested ladders ("over 1.5 / 2.5 /
  3.5 goals") correctly sum above 1.0; `sibling_coherence` assumes exclusivity
  and is documented as flawed because of it.

### Open decisions

- **G2** — enforce the P&L gate in selection. It is measured and reported but
  nothing acts on it; `SelectionPolicy` still sorts on skill alone.
- **G5** — `kalshi_research`, still `status = "planned"`. Note research
  candidates CANNOT be backtested: replaying a historical market while searching
  today's web returns the answer. They can only be evaluated forward, which is
  why the cost model differs and why starting their holdout clock early matters.

---

## Conventions

- Python 3.11+, `uv`, ruff, pytest. `mypy --strict` passes on `src/avo/core`.
- Dataclasses over dicts across module boundaries; type hints everywhere.
- No network in tests; fixtures in `tests/fixtures/`.
- Every candidate module exposes `forecast()` and a `MANIFEST` dict — see
  `experiments/kalshi_quant/candidates/baseline_market.py`.
- Domain types live in the experiment, never in `core/`.
