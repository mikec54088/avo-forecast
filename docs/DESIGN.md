# Design notes

## Why this shape

AVO-style agent frameworks separate a stable **core** (loop, memory, selection,
supervisor) from thin per-domain **adapters** (tools, observation encoding,
evaluator). The claim is that the core transfers across domains unchanged. This
repo is structured to test that claim: Kalshi forecasting is adapter #1.

The core assumption an AVO loop makes is that **evaluation is cheap and
trustworthy**. That assumption is what picks the domain.

- Kernel optimization: cheap (seconds) and trustworthy (a faster kernel is faster).
- Equity backtesting: cheap but NOT trustworthy — overfits to a fixed history.
- Equity paper trading: trustworthy but NOT cheap — weeks per evaluation.
- **Prediction markets: reasonably cheap and trustworthy.** Binary contracts with
  unambiguous resolution let you score *calibration* over independently-resolving
  events rather than estimating a Sharpe ratio from a correlated return stream.

That last point is the whole reason for this domain choice. Independence is what
buys statistical power: 300 resolved markets across FOMC prints, weather, and
sports are 300 near-independent observations. 300 equity trades all long the
market are close to one observation repeated.

## Fitness: Brier skill score

For a resolved market with outcome `y` in {0,1}, candidate forecast `p`, and
market implied probability at entry `q`:

    brier(p) = (p - y)^2
    skill    = 1 - brier(p) / brier(q)

Positive skill means the candidate beat the market. Aggregate by mean over the
holdout set. Report a bootstrap CI alongside the point estimate — with a few
hundred markets the CI will be wide, and pretending otherwise is how you fool
yourself.

Secondary gate: paper P&L under the pessimistic fill model. A candidate must
show positive skill AND non-negative P&L to be promoted. Skill is primary
because it converges faster and is harder to game.

## Temporal holdout

Candidates are timestamped at creation. Scoring filters to markets resolving
strictly after that timestamp. This is what makes replay against locally
captured data legitimate rather than in-sample fitting — the agent cannot have
observed the outcome, because it had not happened.

Consequence: a freshly written candidate has no score. Generations must be
spaced far enough apart that enough markets resolve in between. Start with a
4-week cadence and revise once you can see the empirical resolution rate.

## Generational, not iterative

Because evaluation takes weeks of wall clock but nothing in dollars, run
candidates in parallel: generate ~50, deploy all, harvest together. Wall clock
for 50 equals wall clock for 1. This is the main structural difference from a
kernel-optimization AVO loop, which iterates serially in minutes.

Implication for the supervisor: its stagnation heuristics must distinguish "the
search has plateaued across generations" from "we are 9 days into a 30-day
evaluation window." Tune it against real generational data, not guesses.

## Model backend

The agent runs as a subprocess, not an SDK call — `claude -p` with
`--output-format json`. This keeps the backend interface to
`run(prompt, workdir) -> Result` and makes swapping in `codex` or `grok -p`
trivial. Pin one model per experiment and record it in the run manifest; mixing
models mid-run destroys comparability.

Always pass non-interactive flags and wrap in a timeout. A run that stalls
waiting on a tool-approval prompt at hour 30 is a lost run.

## Deliberately deferred

- Supervisor heuristics (Phase 6) — needs real generational data first.
- Memory distillation (Phase 5) — what is worth carrying forward is
  domain-shaped and unknown until candidates exist.
- Cross-run priors store — separate, namespaced, read-only at run start,
  written only on successful runs. Do not build until run #3.
