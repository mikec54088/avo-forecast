# Build order

Backwards from the evaluator. The failure mode is a beautiful harness sitting on
top of a fitness function that measures noise.

## Phase 0 — data capture  [DONE 2026-08-24, runs forever]
Poll Kalshi open markets every 15 min; append snapshots to Parquet. Separately
sweep settled markets and record outcomes.

**Snapshots are the irrecoverable half.** Top-of-book at a given moment exists
only if something captured it. Resolutions are backfillable — Kalshi keeps
settled markets queryable — so a broken `settle()` costs you nothing permanent.
Prioritise accordingly: a running-but-imperfect snapshot cron beats a verified
one that starts next week.

No agent, no framework, no scoring. This phase is a cron job and a Parquet
directory.

**Done when** two consecutive snapshot runs have landed files and the scheduler
is installed. **Met 2026-08-24**: snapshots every 15 min on the quarter hour,
settle hourly at :12, both via `scripts/launchd/`.

Not cron — on macOS it is subject to TCC and fails *completely silently*
without Full Disk Access. A correct crontab line installed that day never
executed once. Verify by checking that data lands, never that a job is loaded.

## Phase 1 — candidate contract  [DONE 2026-08-24]
`forecast(market, context) -> float` plus a MANIFEST. Four hand-written
baselines as controls: market-implied, base-rate, shrunk-to-0.5, and
sharpened-away-from-0.5.
**Done when** all four import, run, and return valid probabilities.

The fourth is inverted: the other three should score at or below zero and
positive skill from any of them means the scorer is broken, whereas
`baseline_sharpened` is *expected* to score positive while the fitness
denominator is biased, and ~0 once it is not. Without it the control set only
probes the direction toward 0.5 — see the Phase 2 note below.

## Phase 2 — scorer  [DONE 2026-08-31]
Brier skill vs market implied. Temporal holdout enforcement. Pessimistic fill
model for the secondary P&L gate.
**Done when** the three symmetric baselines (market-implied, base-rate,
shrunk-to-0.5) score ~0 skill and are statistically indistinguishable from each
other, **and** `baseline_sharpened` has been read as the tripwire it is rather
than as a result.

> **Open before Phase 3** (measured 2026-08-31 on 77,966 observations, 630
> series; run `uv run python scripts/score_baselines.py`):
>
> 1. **`ENTRY_POLICY`** — "last snapshot before resolution" mixes a 20-minute
>    price with a two-day-old one (median 77 min, p90 603). Staleness inflates
>    Brier skill but not P&L. Recommended: cap at <=60 min.
> 2. **`market_prob`** — leave it. Fresh+tight books are unbiased (+0.0011, CI
>    includes zero, n=13,255); the shading is mostly a staleness artifact.
>    Re-measure after (1).
> 3. **Enforce the P&L gate**, and give `bootstrap_ci` the series-clustered
>    treatment `paper_trade` already has.
>
> The governing number: the cost hurdle is **~2 probability points** (1c
> half-spread + 1c fee) even on the tightest books, while measurable biases are
> 1-3 points. `baseline_sharpened` scored +0.0286 skill and returned
> -0.0020/contract. The target is not "beat the midpoint" but "beat it by >2
> points on a subset identifiable in advance".

> **This criterion is necessary but not sufficient — do not treat passing it as
> a clean scorer.** The original control set was asymmetric: `baseline_market`
> sits on the price and `baseline_shrunk` pulls *toward* 0.5, so nothing moved
> *away* from 0.5 — the direction the market midpoint is actually biased in.
> `baseline_sharpened` was added on 2026-08-24 to close that hole, and it is
> expected to score positive until the denominator question below is settled.
>
> Measured 2026-08-24 on 2,921 real observations: all three baselines behaved
> exactly as documented, while a two-line logit sharpen scored **+0.0195 skill,
> CI [+0.0121, +0.0273]**. The midpoint is shaded toward 0.5 (median spread
> 0.20, prices bounded to [0,1], so the mid is pushed centre-ward at the
> extremes). A generated candidate will find this quickly — "nudge the price
> away from 50/50" is among the first things an LLM tries — and it will look
> like a discovery.
>
> It is also untradeable: the skill sits where the book is wide (+0.0209, CI
> excludes zero) and vanishes on markets `simulate_fill` will actually trade
> (+0.0152, CI [-0.0005, +0.0320]). INVARIANT #2 and INVARIANT #4 disagree about
> what counts as a market.
>
> Re-run `uv run python scripts/check_calibration.py` before accepting this
> gate. The remaining open **G2** decision is whether `market_prob` stays the
> raw midpoint, becomes spread-aware, or is restricted to fillable spreads —
> deliberately deferred to on/after **2026-08-31** so it is decided on a week of
> data rather than one afternoon. That date is a Monday, chosen over the Friday
> so the sample spans a weekend: sports series settle daily while financial and
> economic ones do not, and a weekday-only window could not show whether the
> shading is concentrated in one category. `scripts/recheck.sh` runs both
> diagnostics and writes a dated report.

## Phase 3 — human as the agent  [DONE 2026-09-03]
Hand-write 8-10 real candidates: time-decay adjustment, favorite-longshot bias
correction, volume-weighted confidence, cross-series base rates. Score them.
**Highest-value phase. Do not skip.** This is where you find the missing field
in the contract, the lookahead bug, the liquidity filter you forgot.
**Done when** one manual generation has run end to end and you trust the number.

> **Met, and the number was a clean null.** 26 candidates scored on ~56,000
> resolved markets; every one failed the P&L gate. Notable specifics:
> the favourite-longshot bias measured 3-4.5 points in-sample and did NOT
> replicate forward; `microprice_fair_value` had the best skill (+0.0164) and
> significantly lost money; `price_momentum` returned -0.0554 because drift's
> edge is non-monotone (it peaks at moderate drift and decays at the extreme)
> while the candidate extrapolated linearly.
>
> ForecastContext gained `price_history` and `siblings` on 2026-09-02 (G1) after
> the null, on the theory that candidates were blind. First evidence is against
> that theory: neither field has produced an edge yet.

## Phase 4 — single-shot agent  [DONE 2026-09-01, gate passed 17/20]
Subprocess wrapper around `claude -p`. One parent + its score in, one candidate
file out, exit. No memory, no selection, no supervisor. Run it 20 times and
harden against malformed output.
**Done when** >80% of invocations produce a candidate the scorer accepts.

> **Met 2026-09-01: 17/20 = 85%, zero timeouts.** The first attempt read 13/20,
> and four of the seven failures were the validation probe's fault, not the
> candidates'. Synthetic probes swept one field at a time from a fixed base, so
> field COMBINATIONS never occurred and narrow-gate candidates were rejected for
> "never deviating" when they were simply never triggered. That bias is not
> random -- it rejects conditional strategies and passes blunt ones. The probe
> now runs against ~200 real markets.

## Phase 5 — generational loop  [IN PROGRESS from 2026-09-03]
Memory store (candidates, scores, lineage, distilled reasoning), selection
policy, batch generation, deployment, harvest. Checkpoint after every candidate
so a run is resumable — subscription limits make this mandatory, and AVO needs
it anyway.
**Done when** generation 2 is seeded from generation 1's results automatically.

> Built 2026-09-03: `core/selection.py`, `core/memory.py`, `core/loop.py`,
> `variation_prompt`, and `avo evolve` / `avo rank`.
>
> **The guard.** The temporal holdout does not stop selecting the maximum over
> many candidates on the same data. 25% of SERIES are permanently held back;
> selection never sees them, and a winner is only believed if it holds up there.
> Split by series because Kalshi's top 20 series are over half of all
> observations, so a market-level split would leave the same series on both
> sides and test nothing.
>
> `ready_to_rank` refuses to advance until the newest cohort has 2,000
> observations, because candidates are only scored on markets resolving AFTER
> they were written and a fresh cohort has no score at all.
>
> Two flaws the loop found by being run, which reasoning about it had not:
> it chose two CONTROLS as parents (they score mildly positive and a skill-only
> policy picks them), and the scope enforcement reverted concurrent human edits
> as if they were the agent's.

## Phase 6 — supervisor  [week 6+]
Stagnation detection and redirection. Last, deliberately.
**Done when** it has correctly intervened on real data at least once.

---
Realistic: harness working ~week 4, first agent candidates ~week 5, first
statistically meaningful generational ranking ~week 9-10.
