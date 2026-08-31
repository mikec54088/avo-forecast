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

## Current phase: 0 + 1 + 2

Do NOT build memory, selection, or the supervisor. They raise
`NotImplementedError` deliberately — building them before real scoring data
exists means tuning heuristics against imagination.

### Priorities, in order

**P0 was "get snapshots flowing today". Met 2026-08-24 — and it stays the
standing priority: if capture ever stops, fixing it outranks everything below.**

Top-of-book at 2pm today is gone forever if nothing captured it. Resolutions
are NOT in this category — Kalshi keeps settled markets queryable via
`status=settled`, so the settle path can be backfilled later. Snapshots cannot.
That asymmetry does not expire: an hour of downtime is an hour of observations
that no later work can recover.

0. **DONE 2026-08-24.** Snapshots are flowing. Field names are verified in
   `types.py` (all prices are decimal strings in dollars, not cents), and the
   scheduler is running.

   **Scheduling: use `scripts/launchd/`, not cron.** On macOS, cron is subject
   to TCC and fails *completely silently* without Full Disk Access — a correct
   crontab line installed on 2026-08-24 (macOS 26.5) never executed once, with
   nothing in `data/capture.log`, no lock file, and no error anywhere.
   `crontab -l` showing the right line proves nothing. `scripts/cron.example`
   is kept only for non-macOS hosts.

   Verify by checking that data lands, never by checking that a job is loaded:

       launchctl list | grep avoforecast      # 2nd column is last exit status
       ls data/kalshi_quant/snapshots/date=*/ # a new file every 15 min

   If snapshots ever stop, that is the highest-priority bug in the project —
   every hour without them is unrecoverable.

1. **DONE 2026-08-24.** The settlement field is verified, not assumed. Over 964
   settled non-MVE markets, every `result='yes'` paid
   `settlement_value_dollars=$1.0000` (349/349) and every `result='no'` paid
   $0.0000 (615/615); median `last_price` at settlement was 0.990 vs 0.010; all
   182 two-leg events had exactly one `'yes'`. **The mapping is not inverted.**
   `settle()` re-checks that agreement on every row it writes, since a silent
   flip here would invert every score in the project.

   Two things learned that are easy to get wrong: `resolved_at` must come from
   `settlement_ts`, not `close_time` (settlement lagged close by 179-189s in
   every sampled market), and settled markets report `status='finalized'`, not
   `'settled'` — filter on `result`.

   `settle()` is driven by our own snapshots, not by the global settled feed.
   That feed is 99.8% MVE parlay combos and is not ordered well enough for any
   settlement-time window to terminate against; the snapshot-driven lookup costs
   ~145 requests instead of thousands of pages, and is idempotent, so a missed
   run costs nothing.

2. **Make the four baselines score end to end** on whatever data has
   accumulated. Three of them — market-implied, base-rate, shrunk-to-0.5 — must
   land near zero skill and be statistically indistinguishable from each other.
   If any of those three shows strong positive skill, the scorer is broken —
   find the bug, do not celebrate.

   The fourth, `baseline_sharpened`, is inverted on purpose and is **expected**
   to score positive while the fitness denominator is biased. It exists because
   the original three were asymmetric: they either sit on the price or pull
   *toward* 0.5, and the bid-ask midpoint is biased *away* from it. Measured
   2026-08-24 on 2,921 observations, all three originals behaved exactly as
   documented while a mild logit sharpen scored +0.0192, CI [+0.0121, +0.0270].
   So "the controls look fine" is necessary but not sufficient.

   Run `uv run python scripts/check_calibration.py` before accepting this step.
   If `baseline_sharpened` scores clearly positive, that is not a discovery —
   it means `market_prob` needs deciding (**G2**), not that anything was found.

3. **Stop and report before Phase 3.** Phase gate G4. Three G2 decisions are
   open and listed in `docs/ROADMAP.md` Phase 2: `ENTRY_POLICY`, `market_prob`,
   and enforcing the P&L gate. Skill and money already disagree —
   `baseline_sharpened` scores +0.0286 while returning -0.0020/contract,
   because the ~2-point cost of crossing the spread exceeds any bias we can
   measure. Do not hand-write candidates against a fitness that is still
   moving; they would all need rescoring. Phase gate G4. Phase 3 (hand-writing
   8-10 real candidates) is the first phase that generates new strategies
   rather than plumbing, and `docs/ROADMAP.md` calls it the highest-value
   phase. Do not start it on the back of a scorer whose denominator question
   is still open.

---

## Conventions

- Python 3.11+, `uv`, ruff, pytest. `mypy --strict` passes on `src/avo/core`.
- Dataclasses over dicts across module boundaries; type hints everywhere.
- No network in tests; fixtures in `tests/fixtures/`.
- Every candidate module exposes `forecast()` and a `MANIFEST` dict — see
  `experiments/kalshi_quant/candidates/baseline_market.py`.
- Domain types live in the experiment, never in `core/`.
