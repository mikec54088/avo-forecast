# Experiments

The core is shared and stable. Each experiment is an adapter plus its own
candidates, types, and fitness function. The claim under test is that the core
transfers unchanged; every new experiment is evidence for or against it.

| slug | status | contract | evaluation cost |
|---|---|---|---|
| `kalshi_quant` | active | `forecast(market, context) -> float`, pure function | microseconds |
| `kalshi_research` | planned | candidate is an agent that may search and read | dozens of model calls each |

## Why research is a separate experiment, not a flag

A candidate that can search news is not `forecast()` with an extra context
field. It changes what a candidate *is* — from a function to an agent — and it
changes evaluation cost by orders of magnitude, which changes the viable
generation size, the cadence, and the subscription budget.

Making it a flag would mean one experiment whose cost model depends on runtime
configuration, and results from the two modes would not be comparable within a
single lineage.

Making it a separate experiment that shares `scoring.py` verbatim gives you the
thing you actually want: a controlled comparison. Same fitness, same markets,
same holdout rule, one variable changed. "Does research time buy calibration?"
becomes a measurable question instead of a design argument.

**kalshi_research is blocked** on kalshi_quant completing generation 1. There is
no point measuring the expensive arm before the cheap baseline exists.

## Adding an experiment

1. `experiments/<slug>/experiment.toml` — name, entrypoint, fitness, constraints, cadence.
2. `experiments/<slug>/experiment.py` — a class implementing
   `avo.core.interfaces.Experiment`, plus `build()`.
3. `experiments/<slug>/types.py` — domain types. These never go in core.
4. `experiments/<slug>/candidates/` — at least one working control. The agent
   needs valid state to mutate; a blank directory wastes a generation.
5. `experiments/<slug>/scoring.py` — the fitness function.

If step 2 makes you want to add a domain-specific method to core, widen the
protocol in `core/interfaces.py` instead. An `if experiment == ...` in core is a
design failure, not a shortcut.

## Candidate domains being considered

Judge any new one by whether evaluation is **cheap** and **trustworthy**:

- cheap + trustworthy → good fit (kernel optimization, prediction markets)
- cheap + untrustworthy → overfits (equity backtesting)
- expensive + trustworthy → too slow to iterate (equity paper trading)
