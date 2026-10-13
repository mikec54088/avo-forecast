# avo-forecast — working brief for Claude Code

An AVO-style long-horizon autonomous agent framework. A stable core drives an
LLM agent that writes and refines *candidates*; each **experiment** is a domain
adapter. `kalshi_quant` (forecasting prediction markets) is experiment #1;
others will follow, and the core is expected to transfer unchanged.

Read `docs/DESIGN.md` for reasoning, `docs/ROADMAP.md` for build order,
`docs/EXPERIMENTS.md` for the experiment model.

---

## Session habits (human-requested, 2026-09-29)

- **At the start of every new session, ask whether to schedule the daily
  report.** Session cron jobs die when a session ends and expire after 7 days,
  so it has to be re-offered each time. If yes, `CronCreate` a recurring job
  (~09:07 daily, off the :00/:30 marks) that reads, and changes nothing:
  capture freshness (`launchctl list | grep avoforecast`, newest snapshot under
  ~30 min old -- capture down is reported FIRST), `!!! ALARM` and the latest
  paper-vs-replay numbers in `data/paper-check.log`, last night's evolve result
  in `data/evolve.log` (parents, accepted/written, quota abort), any new
  candidate with an edge outside 1-3 points (run `scripts/leak_check.py`), and
  that research evidence is still landing. Push-notify only on an alarm, dead
  capture, or a decision needed.
- **Anything that has to happen later gets a schedule, not a promise.** When a
  task waits on time or an event (tonight's generation, a fill count, a
  settlement, a pause to lift), create a one-shot `CronCreate` for when it
  should be ready that does the check and reports back, instead of leaving it
  for the human to remember or for a later session to stumble on. Say what was
  scheduled and when. Scheduled jobs are read-only unless the human has said
  otherwise; they never cross a STOP-AND-ASK gate.
- **Explain everything in plain language, as if to a 14-year-old**, unless
  the human asks for the technical version.

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
   apply fees. Never fill at midpoint. *Amended in principle 2026-10-04
   (G3, human-approved): resting (maker) orders that wait to be filled are
   allowed, but only under a pessimistic passive-fill model measured on
   Kalshi's trade tape -- a resting order fills only when trades go THROUGH
   its price, behind the queue that was ahead of it, with the maker fee.
   Not built; nothing may assume maker fills until that model exists.*
5. **Run artifacts are never committed.** `data/`, `runs/` — both gitignored.
6. **One venue.** Kalshi only. Do not add Polymarket. Cross-venue arbitrage is
   the easiest thing for the agent to find and is not the research question.
7. **One backend per run**, recorded in `RunState.backend`. Never mix models
   mid-run; it destroys comparability.
8. **The backtest is a replay of paper trading** (added 2026-09-28). Every
   entry, every pool member and every piece of context must be decidable at
   the instant of the decision -- never by when or how a market settled. One
   rule, shared by both: `observations.is_decision_quote`, `first_signal`,
   `context_parts`, `acts`. `test_paper_trading_takes_exactly_the_trades_the_
   replay_takes` holds them together; a change that breaks it is a leak until
   proven otherwise. The last violation cost five weeks.

---

## Current phase: 5 — the generational loop

Phases 0-4 are done; see `docs/ROADMAP.md` for what each concluded. The loop
runs via `uv run python -m avo.cli evolve kalshi_quant`, and
`uv run python -m avo.cli rank kalshi_quant` scores and confirms without
generating.

Phase 6 (the supervisor) is still deliberately unbuilt, and so is `PriorsStore`
— cross-run priors tuned against two runs would be heuristics fitted to
imagination.

**QUANT GENERATION IS WEEKLY (Wed 03:35) since 2026-10-04** (human decision).
Under the honest replay all 52 candidates lose about the cost of trading,
every one has a verdict, and gens 15-19 bred near-copies of
`fielded_favourite` (skill +0.0001), half rejected for never leaving the
market price. The context holds only Kalshi's own prices; more nights do not
change that. **The human asked that it go back to NIGHTLY whenever there is a
reason** -- a candidate passes the P&L gate on either half, the context gains
new information, or unproven candidates need maturing again. Check at every
status check; the triggers are also in the plist comment.

### Outside numbers (2026-10-04/05, human-approved) -- summary

Crypto spot-vs-book lag, resting (maker) orders, and a time x price mispricing
map were all MEASURED and none beats the ~2.8c cost of crossing the spread.
Weather capture (`scripts/weather_capture.py`) is still collecting. If a
measurement ever finds an edge it becomes a NEW experiment (G5 pre-approved for
that case), not a `ForecastContext` field, so the 52 quant candidates stay
comparable. Numbers and logs: `docs/LEARNINGS.md`.

### How we stop going in circles (2026-09-28, human-approved)

Five weeks were spent breeding candidates against a backtest whose entry rule
used hindsight; the generator is a search engine and it found the hole. The
warning signs were all there -- an edge 3x every measured bias, a failed mirror
test, paper disagreeing from day one -- and each was explained away. Rules:

- **Paper judges, the backtest screens.** Nothing is "proven" on replay alone.
  `avo rank` writes the top both-halves passers to `data/kalshi_quant/
  paper_auto.json` and paper trades them from the next pass
  (`experiment.promote_to_paper`); `PAPER_CANDIDATES` stays as a hand whitelist.
- **Disagreement is an alarm, not a population mismatch to engineer away.**
  `paper_check` (launchd, 02:45) replays paper's markets daily and prints
  `!!! ALARM` in `data/paper-check.log` when decisions or money diverge. Read
  it at every status check.
- **Too good means leak-check first.** Any edge outside the 1-3 point range
  gets `scripts/leak_check.py CANDIDATE` before anything is built on it.
- **Fix leaks structurally, not one at a time.** Invariant #8.

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

**Exit 0 and a file on disk are NOT proof.** 2026-10-01: Kalshi dropped
`liquidity_dollars`, the strict parse rejected every market, and ~18h of
snapshots were written as empty 7.8 KB files (a healthy full pass is ~2.7 MB,
near ~85 KB) with every job exiting 0. Check file SIZES, not just presence.
`capture.parse_failure_alarm` now prints `!!! ALARM` in `data/capture.log`,
raises a desktop notification and exits nonzero when >5% of a pass's markets
fail to parse. Prices stay strict on purpose; only `liquidity` is defaulted.
### What has actually been learned -- headlines (full list: `docs/LEARNINGS.md`)

Read `docs/LEARNINGS.md` before proposing anything; most obvious ideas are dead.

- The cost hurdle is ~2 probability points; every measured bias is 1-3 points.
- The replay once leaked the answer (last-quote entry); fixed 2026-09-28 with
  first-signal entry. Honestly measured, NO candidate makes money (-1.5c to -3c).
  Replay numbers dated before 2026-09-28 are hints only.
- Skill and money disagree: read `avo rank` and the P&L gate together.
- Intervals must be series-clustered; size and read P&L per EVENT (ladders).
- ALWAYS pass `--model` to generation (Sonnet 5 generates); `-n 2` is the
  default because a generation run competes with your own session for quota.
- `liquidity` is always 0.0; a candidate gating on it becomes a constant.
- DO NOT TRADE REAL MONEY: no candidate has an honestly measured edge.

### The research track -- see the sections below; funnel history in `docs/LEARNINGS.md`

**NO SONNET IN THE RESEARCH TRACK (human decision, 2026-09-25).** The local
model (`qwen3.5:9b` via Ollama) is the research engine; Sonnet escalation is
dropped. Pilot results and the running local-only shadow test are in
`docs/PLAN-2026-09-22-RESEARCH-REVAMP.md` (P0/P1/P2).

**CURRENT STATE: RUNNING ON THE LOCAL ENGINE since 2026-09-26** (paused
2026-09-22, resumed by the human). launchd `com.avoforecast.kalshi-research-local`
hourly at :50, 20 research calls/pass (raised from 12 on 2026-10-03; hundreds of
markets were queueing behind the cap, and the log's "FAILED" count was those
deferrals, not failures -- it now reports them separately), no Claude calls. The live candidate WAS
`local_news_favourite` (retired 2026-10-04) (fresh identity, created 2026-09-26);
`roster_news_favourite` is retired -- scored on its Sonnet history, never asked
again. Every retrieval lands in `data/kalshi_research/evidence/`. NEVER reload
the old `com.avoforecast.kalshi-research-run` plist (Sonnet). Details and the
open questions (in-game research, rate) are in
`docs/PLAN-2026-09-22-RESEARCH-REVAMP.md`.

Since 2026-09-28: **no research after kickoff** (`markets.game_started`; ~25% of
calls were in-game before this) and a **politics research candidate**,
`politics_already_happened` -- yes/no political events in their final 48h,
"does a dated report say it already happened / was ruled out?". Slow by
design (~23 markets/week). Details in the plan doc's 2026-09-28 section.

**Four sports news-shift candidates RETIRED 2026-10-04** (human decision):
`bidirectional_news_shift`, `underdog_mirror_shift`, `tossup_news_shift`,
`lineup_window_shift`. They are one bet against sportsbook-anchored game
prices; across ~94 overlapping scored decisions the research candidates that
disagreed with the market were right 15-30% of the time and lost 7-19c a trade
before fees. `local_news_favourite` was retired the same day (3 right of 20).
Parent selection now skips every `retired` candidate (`cli.py`, via the same
exclude list as controls). With it retired, the only live research candidate
is `politics_already_happened`.

Since 2026-09-26 also: **evidence capture** (`kalshi-research-capture`, hourly,
every research-series game at T-12h/T-3h/T-1h, retrieval only, into
`data/kalshi_research/evidence/`) and **weekly research generation**
(`kalshi-research-evolve`, Sunday 08:05, Sonnet WRITES candidates with
`--force` because research candidates need months to rank). Sonnet never
answers research queries.

Scoring is inverted (`Experiment.score_many`, built 2026-09-28; a 52-candidate
rank takes ~21 min). The invention-slot deferral reasoning is in
`docs/LEARNINGS.md`: variation has outperformed invention, and the agent, not
Claude-in-session, must author any founder.

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

---

## Conventions

- Python 3.11+, `uv`, ruff, pytest. `mypy --strict` passes on `src/avo/core`.
- Dataclasses over dicts across module boundaries; type hints everywhere.
- No network in tests; fixtures in `tests/fixtures/`.
- Every candidate module exposes `forecast()` and a `MANIFEST` dict — see
  `experiments/kalshi_quant/candidates/baseline_market.py`.
- Domain types live in the experiment, never in `core/`.
