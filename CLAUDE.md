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

### Outside numbers (2026-10-04, human-approved)

The market cannot be beaten with only the market's own prices, so the next
question is whether Kalshi's book LAGS the outside number a market settles on.
`scripts/crypto_fair_value.py` measures it for crypto (Coinbase spot +
realised vol, first-signal entries, spot always older than the quote, a leak
canary for comparison). `scripts/weather_capture.py` (launchd
`weather-capture`, hourly :41) records NWS forecasts as issued -- not
backfillable, so captured before anything is decided. **If a measurement
finds an edge it becomes a NEW experiment** (G5 pre-approved for that case),
not a ForecastContext field, so the 52 quant candidates stay comparable.

**CRYPTO: MEASURED 2026-10-04, NO LAG -- DEAD at our resolution.** 708,019
quotes on 84,470 markets (BTC/ETH/SOL/XRP/DOGE; above, range, 15-min up/down),
Aug 24 - Oct 4. Ticker parse reproduces Kalshi's outcomes 99-100%. Trading the
gap between a spot+realised-vol fair value and the book, first signal within
72h: -2.2c to -3.7c per contract at every threshold, on BOTH halves, for every
coin, kind and time-to-close band. Bigger claimed edges lose MORE (claimed
+16c, realised -3c): when the model disagrees most, the book is right. The
naive model's Brier skill vs the mid is NEGATIVE (-0.024); a 20-30% blend adds
only +0.006, the size of skill that has never paid. Spot 1, 5 or 15 minutes
old makes no difference, so there is no minute-scale lag to collect; the LEAK
CANARY (spot 5 min after the quote) earns +6.9c, so the instrument can see an
edge when one exists. Full output: data/crypto-fair-value-2026-10-04.log.
Weather remains open (capture started 2026-10-04; needs weeks of forecasts).

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
  picked. It also REPLICATES: +0.1016 selection / +0.0948 confirmation, and
  +0.1173 / +0.1021 in the rank before that. But it is NOT a both-halves gate
  passer -- its confirmation half holds ~70-80 fills, below the 200 minimum, so
  the gate is silent there and `unclimbed_favourite` remains the only
  candidate with proof on both sides. Topping the PARENT sort is not a claim
  about deployment. And +10.2c/contract is further outside the 1-3 point range
  every measured bias sits in than anything yet seen; the standing skepticism
  applies harder here, not less.

- **The loop was circling, and three separate things caused it** (measured
  2026-09-20, all fixed the same day). Six generations produced 11 candidates
  and 8 of those came from generation 1; generations 3 and 4 chose the
  IDENTICAL parent pair. (1) Quota: `-n 8` hit the session limit on attempt 2
  and then burned six more invocations against the same wall, recorded as
  0/8 and 1/8 "rejected" as though the agent had written badly. It was never
  asked -- `is_quota_exhausted()` now aborts the batch. (2) Exploit-only
  parents: a candidate needs ~200 fills and 1-2 weeks to get a verdict, while
  generations run in days, so a candidate written in generation N is still
  unproven at N+1, N+2, N+3 and can NEVER be bred from. One slot now explores
  (`choose_explore`). (3) Cadence: generating on the calendar re-derived the
  same parents and spent a full agentic session doing it; `pool_changed()`
  now requires a new candidate or a moved verdict.
- **GATE_UNPROVEN means two opposite things.** "Too few fills to judge" and
  "measured, and indistinguishable from zero" share a verdict and want
  opposite treatment. The first explore slot ranked the whole unproven pool by
  fitness and picked `spread_scaled_shoulder` on 30,667 fills and
  `favourite_longshot`, whose edge is recorded as having failed to replicate --
  answered questions, not open ones. `SelectionPolicy.maturity` /
  `maturity_floor` separate them; core never learns what a fill is.

- **Generation 7 proved the quota fix the hard way.** Run 2026-09-20 on the
  OLD code, before the abort landed: parent `ladder_leader`, both slots
  rejected `backend exited 1: You've hit your session limit`, 0/2 accepted,
  ~55 min of scoring spent to buy nothing. Under the fix it aborts after slot 1
  and says why. Note what it also shows: with `-n 2` the old
  `k = max(1, n // 4)` gives ONE parent and both slots breed from it, which is
  the exploit-only monoculture the explore slot now breaks.

- **Selling before settlement: measured 2026-09-27, not built.** Asked "why
  only trades that settle -- why not buy low, sell high?" `scripts/swing_room.py`
  simulated executable round trips on 5 weeks of hourly snapshots (buy at ask,
  sell at bid later, fee both ways, final hour excluded) with HINDSIGHT on
  direction -- an upper bound. Round-trip cost is 6-10c typical (4c only on the
  tightest books), not the ~2c entry-only hurdle. Share of 72h windows where a
  correct direction call beat all costs: crypto 52%, gas 52%, short-dated
  politics 38%, stocks/FX 37%, weather 33% -- but sports games before the game
  5%, and anything closing >30 days out 3-21% (long-dated politics 5%, median
  move 0.6c vs 6.7c cost). So: trading years-long markets on swings is dead;
  pre-game sports barely moves (the moves are in-game, which hourly capture
  cannot see); the room is in short-dated markets tracking a daily-moving
  number, which are equally forecastable to settlement. Decision: no separate
  swing experiment for now -- point both tracks at those categories, and add
  early exit later as a way to collect a proven edge (it would be a G1/G2/G5
  question then). Full table: `data/swing_room.csv`.

- **THE REPLAY P&L LEAKS THE ANSWER (measured 2026-09-28). unclimbed_favourite's
  edge is an artifact of it.** ENTRY_POLICY scores each market at its LAST quote
  before resolution -- a point you can only identify in hindsight. Markets that
  settle EARLY on YES (props, totals, "both teams score": YES the moment it
  happens, NO only at the final whistle) are therefore caught at a moderate
  price just before a sudden YES. unclimbed_favourite's replay fills: median 46
  min before the outcome was known, 81% after kickoff; those settling <1.5h into
  the game won 82% at a 0.72 mid (+7.5c), those settling at the game's end
  -2.5c; fixed-time markets (game winners -4.3c, indices +0.9c) never paid.
  Same 1,443 markets, same candidate, entry moved to the FIRST quote it signals
  on (no hindsight, what paper does): +0.0472 [+0.020,+0.075] -> +0.0017
  [-0.026,+0.029]. Paper agrees: -0.0026 [-0.039,+0.034] on 1,354 fills /
  296 series. The entries POOL is also outcome-selected (a book must stay
  two-sided until <=60 min before resolution, which winning favourites often
  do not), so first-signal over the whole pool reads -0.09 and is not a clean
  estimate either. FIXED 2026-09-28 (G2, human-approved): ENTRY_POLICY is now
  `first_signal_within_72h_of_close` -- walk every quote from 72h before
  close, trade the first signal; the pool is every resolved market with such a
  quote. `scripts/leak_check.py` runs the checks above on any candidate. EVERY
  replay number in this file dated before 2026-09-28 -- gate passers, family
  verdicts, parent choices, the unclimbed_favourite decision -- was measured
  under the leak. Treat them as hints until re-measured.
- **Re-measured under the honest policy, 2026-09-28: NOTHING MAKES MONEY.**
  Pool 508,901 markets (was 184,610). Every key candidate LOSES on both
  halves, P&L/contract selection / confirmation:

      unclimbed_favourite    -0.0255 / -0.0313   (was the both-halves passer)
      unmarked_favourite     -0.0148 / -0.0273   (was the other one)
      ladder_leader          -0.0284 / +0.0024   (was the parent-sort leader)
      tick_grid_conditioned  -0.0141 / -0.0193
      neglected_leg          -0.0159 / -0.0255
      favourite_longshot     -0.0236 / -0.0231
      baseline_sharpened     -0.0270 / -0.0253
      long_horizon_favourite -0.2520 / -0.2274   (gen 13, written 09-27)

  Losses cluster at 1.5-3c: roughly the cost of trading a fairly priced
  market. That is the honest baseline the next generation starts from. The
  full rank of every candidate is in data/rank-2026-09-29-first-signal.log.

### The research track, measured 2026-09-20; paused 2026-09-22

The measurements below are the final pre-pause assessment. At that time it was
running clean and not yet evaluable: launchd at :50 hourly, exit 0, 12 days of
partitions, **45,214 forecasts, zero errors** (`error` and `research_error` are
empty strings on every row). Three candidates: `research_market` (the
no-research control, 22,931), `roster_news_favourite` (21,253),
`injury_news_favourite` (1,030).

The funnel is the whole story:

| stage | count |
|---|---|
| forecasts logged | 45,214 |
| research actually ran | 512 |
| ...and the forecast disagreed with the market | ~20 |
| ...resolved, so it can be scored | **13** |

- **Median `|forecast - mid|` across all 512 research calls is 0.0000.** The v2
  negation-safe VERDICT parsing is working -- it fires only on a sourced report
  and usually there is not one -- but the consequence is that eleven days of
  research produced thirteen decisions capable of earning or losing anything.
- **THE RATE IS THE PROBLEM, not the budget.** ~1.3 actionable resolved
  decisions/day means ~5 MONTHS to reach `PNL_GATE_MIN_FILLS = 200`. The
  per-pass cap is 25 with hourly passes (600/day theoretical) against 512 used
  in ten days, so the cap is nowhere near binding. Qualifying markets are the
  bottleneck. Waiting does not fix this; the candidate design has to.
- **Every one of the 13 is a fade of exactly -0.040.** `roster_news_favourite`
  ends `return max(p - SHIFT, 0.5 + EPS)` with `SHIFT = 0.04`: fixed magnitude,
  one direction, never a boost, no expression of confidence. That is why 512
  research calls collapse to ~20 opinions.
- **Direction right: 5 of 13.** Read the +0.0186 skill as a TRIPWIRE, not a
  result. Thirteen observations, and the sign is structurally flattered:
  shrinking a favourite toward 0.5 gains ~2.7x on each miss what it loses on
  each hit, so it reads positive while being wrong 8 times in 13. This is
  precisely the `baseline_sharpened` trap Phase 2 built a control for.

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

### The scoring loop is inverted (BUILT 2026-09-28)

`core.loop.score_subsets` calls the optional `Experiment.score_many`, which
kalshi_quant implements: each partition is read once and every candidate x
both subsets is scored on it (`experiment._Tally`). Under the first-signal
policy the per-candidate reload was ~2 min, twice per candidate, and
dominated a rank. Verified before/after on three partitions, every candidate,
all three subsets: identical scores. kalshi_research does not implement it and
keeps the per-candidate path. Measured 2026-09-29 with the i.i.d. bootstrap also vectorised:
a full rank of 52 candidates over 508,901 markets takes 21 min, 7.3 GB peak
(it was heading for ~40 h). The history below is why it was deferred.


`avo rank` takes ~54 min because `score_all` loops candidates OUTER and each
one re-reads the whole entries table -- 84 candidates x 2 subsets = 168 passes.
Inverting it (one pass over chunks, candidates inner) was measured on
2026-09-20 while computing cluster counts and is much faster.

It is NOT a memory regression, which was the reason to suspect it: `Observation`
is 48 bytes and all 44 scored candidates hold 3.33M kept observations, so
holding every accumulator at once costs ~0.24 GB (~0.46 GB at the full 84)
against 0.74 GB for the chunk and history. Peak goes ~0.74 -> ~1.2 GB, nowhere
near the cliff that motivated streaming.

The real cost is that it touches `core/loop.py` and the `score()` interface --
a new `score_many` entry point, not a tweak -- to save wall clock on a batch
job nothing is blocked on. Land it with the same before/after equality check
the entries-table and streaming changes got: every candidate, both subsets,
zero mismatches.

### Potential direction: an INVENTION slot (not built, revisit)

**Nothing the loop has ever produced is unparented.** Every candidate from
2026-09-05 onward is a variant, and the entire productive lineage descends from
just two Phase-4 ideas, `volume_weighted` and `last_trade_blend`, plus
`control_middle_only`. `variation_prompt` takes a parent; there is no code path
to a founder. Lineage depth has never exceeded 3, and two of the five depth-3
candidates were hand-written.

Deferred 2026-09-20, deliberately, with the reasoning recorded so it is not
re-litigated from scratch:

- The case FOR is diversification, not yield. A two-idea root stock is fragile.
- The case AGAINST on current evidence: 09-01's 16 founders produced 1 gate
  passer (6%); generation 1's 8 variants produced 5 (62%). Variation is
  outperforming invention tenfold. That comparison IS confounded -- those
  founders predate the dataset digest and the loop's memory of failures, so
  they were written blind -- but it is the only evidence there is.
- A third slot costs +50% quota per generation, and quota is what killed
  generations 3 and 4 outright. Do not add it before the abort fix is proven.
- Novelty must be MEASURED, not asserted: check the overlap of markets a
  founder fires on against every existing candidate, and reject it as a
  rediscovery if they substantially coincide. The same overlap instrument the
  portfolio section needs for correlation sizing.
- **The agent must author founders, not Claude-in-session.** A session that has
  read confirmation-half numbers cannot write an uncontaminated candidate.

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

- **DO NOT TRADE REAL MONEY.** Superseded 2026-09-28: the condition below
  ("wait for ~400 confirmation fills") no longer applies -- the edge it waits
  on was the entry-policy leak (see "THE REPLAY P&L LEAKS THE ANSWER"). There
  is currently NO candidate with a replay edge measured honestly. Kept for
  history: `unclimbed_favourite` was the first
  candidate to pass the P&L gate on BOTH halves and to survive the series,
  weekend, staleness and matched-control checks -- the control, same band and
  spread without its path gate, returns -0.0024 over 9,272 fills. Numbers
  refreshed 2026-09-24 against the rebuilt 184,610-entry table (the 09-17
  figures, +0.0769 / +0.0700 on 204 confirmation fills, were computed on a
  table that had silently stopped growing -- see the entries-staleness note):

      selection     983 fills / 172 series   +0.0438  [+0.0098, +0.0778]
      confirmation  339 fills /  68 series   +0.0598  [+0.0067, +0.1128]

  The condition stands and is NOT yet met. Confirmation has gone 204 -> 339
  fills, up 66%, not the roughly-doubled ~400 this decision asks for, and the
  lower bound is +0.0067 -- still a hair above zero. It is now 2 of 45 tested
  rather than 1 of 42, where ~2 false positives are expected, and the edge is
  still far outside the 1-3 point range every other measured bias sits in.
  Being wrong costs real money. Wait for the confirmation fills to reach ~400.

  The ~$326/day of forgone paper profit quoted here on 2026-09-17 is STALE and
  deliberately not restated: paper trading changed population on 2026-09-24
  (commit `4c06150`) and its cumulative figure is mid-transition. Re-measure it
  before using it to argue either way.
- ~~**STOP GENERATING CANDIDATES for now.**~~ **REVERSED 2026-09-20.** The
  multiple-comparison worry was real but it ignored pipeline latency: a new
  candidate cannot be judged for ~2 weeks, so three idle days bought nothing
  and cost three days of forward evidence. The both-halves P&L gate is a strong
  enough screen (2 of 45 pass as of 2026-09-24) to carry the multiplicity
  explicitly instead
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
- ~~**G5** — open `kalshi_research`.~~ **AUTHORIZED 2026-09-09; PAUSED BY THE
  HUMAN 2026-09-22.** Research candidates CANNOT be backtested: replaying a
  historical market while searching today's web returns the answer. The pause
  preserves all forward history. Resume only through the shadow/canary gates in
  `docs/PLAN-2026-09-22-RESEARCH-REVAMP.md` and with explicit human approval.

---

## Conventions

- Python 3.11+, `uv`, ruff, pytest. `mypy --strict` passes on `src/avo/core`.
- Dataclasses over dicts across module boundaries; type hints everywhere.
- No network in tests; fixtures in `tests/fixtures/`.
- Every candidate module exposes `forecast()` and a `MANIFEST` dict — see
  `experiments/kalshi_quant/candidates/baseline_market.py`.
- Domain types live in the experiment, never in `core/`.
