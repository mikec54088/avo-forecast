# avo-forecast

An AVO-style long-horizon autonomous agent framework: a stable core (loop,
memory, selection, supervisor) drives an LLM agent that writes and refines
candidate solutions, with per-domain **experiments** plugged in behind a narrow
interface.

Experiment #1 is `kalshi_quant` — forecasting Kalshi prediction markets, scored
by **Brier skill against the market's own implied probability**, measured
strictly on markets that resolved after a candidate was written.

- `CLAUDE.md` — working brief, invariants, and stop-and-ask gates. Read first.
- `docs/DESIGN.md` — why the architecture is shaped this way.
- `docs/ROADMAP.md` — build order and phase gates.
- `docs/EXPERIMENTS.md` — the experiment model and how to add one.

## Quickstart

```bash
uv venv && uv pip install -e ".[dev]"
pytest
python -m experiments.kalshi_quant.capture snapshot   # verify before cron'ing
```

Then install `scripts/cron.example`. Data capture is the only calendar-bound
part of this project — start it before building anything else.

## Layout

```
src/avo/
  core/         domain-neutral: types, interfaces, holdout, registry,
                memory/selection/supervisor (stubs)   — never imports experiments
  backends/     claude / codex / grok subprocess wrappers
experiments/
  kalshi_quant/     active   — pure-function forecasters
  kalshi_research/  paused   — redesigning retrieval + local inference
```

The core/experiment split is the architectural claim under test: the core should
transfer to a new domain unchanged, with only the adapter rewritten.
