# Build order

Backwards from the evaluator. The failure mode is a beautiful harness sitting on
top of a fitness function that measures noise.

## Phase 0 — data capture  [START TONIGHT, then runs forever]
Poll Kalshi open markets every 15 min; append snapshots to Parquet. Separately
sweep settled markets and record outcomes.

**Snapshots are the irrecoverable half.** Top-of-book at a given moment exists
only if something captured it. Resolutions are backfillable — Kalshi keeps
settled markets queryable — so a broken `settle()` costs you nothing permanent.
Prioritise accordingly: a running-but-imperfect snapshot cron beats a verified
one that starts next week.

No agent, no framework, no scoring. This phase is a cron job and a Parquet
directory.

**Done when** two consecutive snapshot runs have landed files and the cron
entry is installed.

## Phase 1 — candidate contract  [week 1]
`forecast(market, context) -> float` plus a MANIFEST. Three hand-written
baselines as controls: market-implied, base-rate, shrunk-to-0.5.
**Done when** all three import, run, and return valid probabilities.

## Phase 2 — scorer  [week 1-2]
Brier skill vs market implied. Temporal holdout enforcement. Pessimistic fill
model for the secondary P&L gate.
**Done when** the three baselines score ~0 skill and are statistically
indistinguishable from each other.

## Phase 3 — human as the agent  [week 2-3]
Hand-write 8-10 real candidates: time-decay adjustment, favorite-longshot bias
correction, volume-weighted confidence, cross-series base rates. Score them.
**Highest-value phase. Do not skip.** This is where you find the missing field
in the contract, the lookahead bug, the liquidity filter you forgot.
**Done when** one manual generation has run end to end and you trust the number.

## Phase 4 — single-shot agent  [week 3-4]
Subprocess wrapper around `claude -p`. One parent + its score in, one candidate
file out, exit. No memory, no selection, no supervisor. Run it 20 times and
harden against malformed output.
**Done when** >80% of invocations produce a candidate the scorer accepts.

## Phase 5 — generational loop  [week 4-6]
Memory store (candidates, scores, lineage, distilled reasoning), selection
policy, batch generation, deployment, harvest. Checkpoint after every candidate
so a run is resumable — subscription limits make this mandatory, and AVO needs
it anyway.
**Done when** generation 2 is seeded from generation 1's results automatically.

## Phase 6 — supervisor  [week 6+]
Stagnation detection and redirection. Last, deliberately.
**Done when** it has correctly intervened on real data at least once.

---
Realistic: harness working ~week 4, first agent candidates ~week 5, first
statistically meaningful generational ranking ~week 9-10.
