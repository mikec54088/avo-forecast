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
- **Use Sonnet 5 to GENERATE, not just to research.** Compared 2026-09-11 on
  the same prompt: Opus wrote one candidate in 87 turns / 43 tool calls / 27
  min; Sonnet wrote two in ~17 min each, both accepted, both reading inputs the
  first 39 candidates ignored, both arguing against themselves. Quality was not
  the differentiator; cost and wall clock were. Opus is not wasted here, but it
  is not buying anything measurable either. ALWAYS pass `--model`: the CLI
  default is a user setting and exhausted Fable credits killed a whole
  generation silently.
- **A generation run competes with your own session for the account's rolling
  window.** One invocation is a full agentic session, not a request. Three runs
  died on quota before this was understood. `-n 2` is the default for that
  reason; candidates accumulate in the registry whatever the generation number.
- **The dataset digest is computed once per run** (`digest.py`) and pasted into
  the prompt. It removed the expensive half of an invocation -- 36 of 43 tool
  calls were the agent characterising the dataset itself -- and, more
  importantly, made candidates' claims comparable: generation 3 quoted 6-12
  point edges measured privately, and no two could be checked against another.
- **The full pass sweeps by SERIES, not globally** (`--by-series`, since
  2026-09-12). Walking the whole open universe had grown to 9.4M records over
  9,385 pages in 89 min and was outrunning its own hourly schedule; 99% of that
  fetch was MVE parlay combos with no book. Bounding by close time does NOT
  help -- the parlays sit in the same 1-7 day window as the real game markets,
  so a 48h bound left 1 researchable game market against 30. `series_ticker` is
  a real server-side filter (a bogus series returns zero) and separates them
  cleanly. Measured back to back: 122,855 records / 4,142 pages / 22 min,
  keeping 96% of quotable markets and MORE game markets than the global sweep.
  Cost now scales with series we care about, not with Kalshi's parlay output.
- **Not every event is mutually exclusive.** Nested ladders ("over 1.5 / 2.5 /
  3.5 goals") correctly sum above 1.0; `sibling_coherence` assumes exclusivity
  and is documented as flawed because of it.

- **Ladders inflate paper P&L and real exposure.** 80 of 296 paper trades were
  rungs of five Nasdaq-100 events (sixteen strikes ten points apart, one close
  settles all). Unclustered that read +0.0860 [+0.0488,+0.1233]; series-
  clustered it is [-0.0020,+0.1741] and spans zero. Always read paper P&L via
  `paper_log.pnl_summary`, never a bare mean, and size per EVENT -- at 100
  contracts a signal the old sizing put 1,600 on a single index close.

- **Once several candidates pass the money gate, skill is the wrong
  tiebreak.** Generation 6 had four passers and `choose_parents()` broke the
  tie on fitness, breeding from `tick_grid_conditioned` (+0.0064/contract) over
  `unclimbed_favourite` (+0.0585) -- 9x apart in the quantity being hunted.
  Since 2026-09-20 the sort is (gate verdict, P&L lower bound, skill, n), the
  strength strictly BELOW the verdict so it can never lift an unproven or
  losing candidate over a proven one. INVARIANT #2 is intact: skill is still
  the fitness, and still orders candidates sharing a verdict and a strength.
- **Use the LOWER BOUND, and read the cluster count next to it.** Sorting on
  the point estimate would hand the run to whichever thin sample got lucky.
  `pnl_stats` records `pnl_n_series` for this: on gen006 `ladder_leader` won on
  231 fills, which reads as thin until you see the 86 series behind it and its
  largest contributing 7% of net P&L -- the least concentrated of the eight
  passers, against 34% for `tick_grid_conditioned`, the candidate the old sort
  picked.

### The goal is a PORTFOLIO, not a winner

Real trading runs several candidates at once. That has three consequences the
early framing of this project got wrong:

- **A profitable candidate SPINS OFF VARIANTS and stays intact.** A variant
  gets a fresh `created_at`, so INVARIANT #1 gives it a clean forward clock
  while the parent keeps accumulating an uncontaminated record. This is how a
  measurement made on selection data can be tested without contaminating the
  thing it was measured on -- `unclimbed_tight` and `unclimbed_far` (both
  2026-09-20) exist for exactly that reason.
- **Stopping generation to protect one finding is a mistake**, and the
  2026-09-17 decision to do so was reversed on 2026-09-20. A candidate cannot
  be judged for ~2 weeks, so idle time is judgement you never get. Generate
  continuously at `-n 2`.
- **Correlated candidates are one bet.** `unclimbed_tight` is a strict subset
  of its parent; running both live is not diversification. Before trading
  several candidates, measure the OVERLAP of the markets they act on, and size
  the correlated group as one position -- the same lesson the ladder finding
  taught at the market level.

### Mechanism audit of unclimbed_favourite, 2026-09-20

Done after it passed the P&L gate on both halves. Full detail in its docstring.

- **The edge is monotone in run-up** across six bands (+0.0866 at 0.00-0.02
  decaying to -0.0011 at 0.40-1.00). A fitted threshold shows a cliff; this
  shows a gradient. Strongest evidence yet that it is a mechanism.
- **The mirror FAILS: the stated mechanism is wrong.** "A price that held is
  under-rated" should not need the favourite side, and below 0.50 the same gate
  inverts -- mid 0.05-0.20 with run-up <0.05 returns -0.0371 [-0.0599,-0.0144].
  Do not reason from that story about where else to look.
- **Not a proxy** for volume, open interest or spread, which split evenly
  inside the gate. It DOES interact with horizon: +0.1032 beyond 48h to close
  against +0.0307 inside it.

### Standing decisions, 2026-09-17

- **DO NOT TRADE REAL MONEY YET.** `unclimbed_favourite` is the first candidate
  to pass the P&L gate on BOTH halves (+0.0769 selection / +0.0700
  confirmation) and to survive the series, weekend, staleness and
  matched-control checks -- the control, same band and spread without its path
  gate, returns -0.0024 over 9,272 fills. But the confirmation half is 204
  fills with a lower bound of +0.0055, it is 1 of 42 tested where ~2 false
  positives are expected, and +7.5c is far outside the 1-3 point range every
  other measured bias sits in. Waiting costs ~$326/day of paper profit; being
  wrong costs real money. Wait for the confirmation fills to roughly double.
- ~~**STOP GENERATING CANDIDATES for now.**~~ **REVERSED 2026-09-20.** The
  multiple-comparison worry was real but it ignored pipeline latency: a new
  candidate cannot be judged for ~2 weeks, so three idle days bought nothing
  and cost three days of forward evidence. The both-halves P&L gate is a strong
  enough screen (1 of 42 passes) to carry the multiplicity explicitly instead
  of abstaining. Generate continuously at `-n 2`.
- **Paper trading is live** (`paper_runner.py`, launchd at :02/:17/:32/:47) and
  is the only thing that closes the gap replay cannot: were you there at the
  entry instant, was the quote tradable, was the size available. REPLAY scored
  entries at a median 46 minutes' staleness; paper decides on a ~2-minute book.
  `PAPER_CANDIDATES` is a deliberate whitelist -- adding one is a claim that a
  strategy has earned a live test.

### Open decisions

- ~~**G2** — enforce the P&L gate in selection.~~ **DECIDED 2026-09-08** (commit
  `aa1f0ef`). `SelectionPolicy.eligible()` now drops candidates the gate has
  PROVEN lose money. Only proven losers are excluded, not everything short of
  proven profitable: require proof to reject, not proof to survive. The sort
  was amended 2026-09-20 to (gate verdict, P&L lower bound, skill, n) -- see
  the tiebreak note above.
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
