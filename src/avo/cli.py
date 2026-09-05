"""Entry point. Experiment-agnostic by construction: everything is looked up
through avo.core.registry, so adding an experiment needs no change here."""
from __future__ import annotations

import argparse
from pathlib import Path

from avo.core import registry
from avo.core.backends import BACKENDS
from avo.core.generate import run_trial, summarise


def main() -> None:
    ap = argparse.ArgumentParser(prog="avo")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("experiments", help="list available experiments")

    seeds = sub.add_parser("seeds", help="list an experiment's seed candidates")
    seeds.add_argument("experiment")

    gen = sub.add_parser("generate", help="run a backend and validate what it writes")
    gen.add_argument("experiment")
    gen.add_argument("--backend", default="claude", choices=sorted(BACKENDS))
    gen.add_argument("--model", default=None, help="claude only; pins the model")
    gen.add_argument("-n", type=int, default=1, help="invocations (20 for the Phase 4 gate)")
    gen.add_argument("--timeout", type=int, default=900,
                     help="seconds per invocation; 420 put the ceiling inside the "
                          "observed distribution (mean 263s, slowest accepted 355s)")
    gen.add_argument("--prompt-file", default=None,
                     help="file holding the prompt; omit to use the built-in probe prompt")
    gen.add_argument("--out-dir", default="runs/phase4",
                     help="where accepted candidates and the trial log are stashed")
    gen.add_argument("--keep-rejects", action="store_true",
                     help="leave invalid candidate files on disk for inspection")

    ev = sub.add_parser("evolve", help="run the generational loop (Phase 5)")
    ev.add_argument("experiment")
    ev.add_argument("--backend", default="claude", choices=sorted(BACKENDS))
    ev.add_argument("--model", default=None)
    ev.add_argument("-n", type=int, default=8, help="candidates per generation")
    ev.add_argument("--generations", type=int, default=1)
    ev.add_argument("--timeout", type=int, default=1800,
                    help="seconds per invocation; the informed prompt makes "
                         "agents analyse data first and 900 was too tight")
    ev.add_argument("--run-id", default=None, help="resume an existing run")
    ev.add_argument("--force", action="store_true",
                    help="generate even when the newest cohort has too few "
                         "observations to rank; ranks on noise, use knowingly")

    rank = sub.add_parser("rank", help="score and confirm without generating")
    rank.add_argument("experiment")

    args = ap.parse_args()

    if args.cmd == "experiments":
        for name in registry.available():
            cfg = registry.config(name)["experiment"]
            print(f"{name:20s} {cfg.get('status', '?'):9s} {cfg.get('description', '').strip().splitlines()[0]}")
    elif args.cmd == "seeds":
        exp = registry.load(args.experiment)
        for c in exp.seed_candidates():
            print(f"{c.candidate_id:24s} gen={c.generation} {c.rationale}")
    elif args.cmd == "generate":
        _generate(args)
    elif args.cmd == "evolve":
        _evolve(args)
    elif args.cmd == "rank":
        _rank(args)


PROBE_PROMPT = """Write one new forecasting candidate for the kalshi_quant \
experiment in this repository.

Read experiments/kalshi_quant/candidates/baseline_market.py for the contract and \
experiments/kalshi_quant/types.py for the fields available on MarketSnapshot and \
ForecastContext. Then create ONE new file in \
experiments/kalshi_quant/candidates/ that exposes:

  - forecast(market: MarketSnapshot, context: ForecastContext) -> float
    returning a probability in [0, 1]
  - a MANIFEST dict with candidate_id, generation, parent_id, created_at, rationale

Requirements: it must be deterministic, must not use the network, and must \
actually differ from market.implied_prob on at least some markets. Give it a \
short docstring saying what edge it is trying to capture and why.

Do not modify, create, or delete ANY file outside \
experiments/kalshi_quant/candidates/. That includes memory files, notes, \
configuration, and the scorer. Changes outside that directory are reverted \
automatically and count against the run."""


def _generate(args) -> None:
    """Phase 4. Measures how often a backend produces a scoreable candidate.

    The built-in prompt is deliberately generic: this phase measures the
    harness and the contract, not strategy quality. A real variation_prompt
    needs generation-1 scores, which do not exist yet.
    """
    repo_root = Path(__file__).resolve().parents[2]
    exp = registry.load(args.experiment)
    cdir = repo_root / "experiments" / args.experiment / "candidates"
    if not cdir.is_dir():
        raise SystemExit(f"no candidates directory at {cdir}")

    prompt = (Path(args.prompt_file).read_text() if args.prompt_file else PROBE_PROMPT)
    factory = BACKENDS[args.backend]
    backend = factory(args.model) if args.backend == "claude" else factory()
    print(f"backend {backend.name}\nexperiment {args.experiment}\n"
          f"{args.n} invocation(s), {args.timeout}s timeout each\n")

    # The user's memory directory is outside the repo, so git cannot restore
    # it; the 2026-09-01 trials wrote there. Watch and report it.
    watch = [Path.home() / ".claude" / "projects", Path.home() / ".claude" / "CLAUDE.md"]
    attempts = run_trial(backend, exp, prompt, cdir, repo_root, n=args.n,
                         timeout_s=args.timeout, out_dir=repo_root / args.out_dir,
                         keep=args.keep_rejects, watch_paths=watch)
    print("\n" + summarise(attempts))


def _scoring_inputs():
    from experiments.kalshi_quant.observations import SeriesHistory, load_entries
    entries = load_entries()
    if not entries:
        raise SystemExit("no observations yet; capture needs to run first")
    return entries, SeriesHistory(entries)


def _rank(args) -> None:
    """Score every candidate, then confirm on series selection never sees."""
    from avo.core.loop import rank_and_confirm
    from avo.core.selection import CONFIRMATION_FRACTION, SelectionPolicy

    exp = registry.load(args.experiment)
    entries, history = _scoring_inputs()
    _, verdicts = rank_and_confirm(exp, entries, history, SelectionPolicy())
    print(f"{len(entries):,} observations; "
          f"{CONFIRMATION_FRACTION:.0%} of series held back for confirmation\n")
    print(f"{'candidate':<26}{'selection':>11}{'confirm':>10}  verdict")
    for v in verdicts:
        c = v.confirmation.primary if v.confirmation else float("nan")
        print(f"{v.candidate_id:<26}{v.selection.primary:>+11.4f}{c:>+10.4f}  "
              f"{'CONFIRMED' if v.confirmed else v.note}")
    n = sum(v.confirmed for v in verdicts)
    print(f"\n{n}/{len(verdicts)} confirmed on held-out series")


def _evolve(args) -> None:
    """Phase 5. Generate, score, select, checkpoint, repeat."""
    from avo.core.loop import ready_to_rank, run_generation, score_all
    from avo.core.memory import RunMemory, new_run_id
    from avo.core.selection import SelectionPolicy

    repo_root = Path(__file__).resolve().parents[2]
    exp = registry.load(args.experiment)
    # Controls are instruments, not candidates to improve. Breeding from one
    # spends a generation optimising toward a known dead end.
    controls = [c.candidate_id for c in exp.seed_candidates()
                if c.meta.get("role") == "control"]
    cdir = repo_root / "experiments" / args.experiment / "candidates"
    factory = BACKENDS[args.backend]
    backend = factory(args.model) if args.backend == "claude" else factory()

    run_id = args.run_id or new_run_id(args.experiment)
    memory = RunMemory(run_id, repo_root / "runs")
    entries, history = _scoring_inputs()
    watch = [Path.home() / ".claude" / "projects"]

    print(f"run {run_id}\nbackend {backend.name}\n{len(entries):,} observations")

    start = memory.latest_generation() + 1
    for g in range(start, start + args.generations):
        current = score_all(exp, entries, history, subset="selection")
        if not ready_to_rank(current) and not args.force:
            raise SystemExit(
                "the newest candidates have too few observations to rank. "
                "Candidates are scored only on markets resolving AFTER they "
                "were written, so a fresh cohort has no score and ranking it "
                "would breed from noise. Wait for markets to resolve, or pass "
                "--force knowingly."
            )
        rec = run_generation(exp, backend, memory, cdir, repo_root, g, args.n,
                             SelectionPolicy(exclude=controls), entries, history,
                             timeout_s=args.timeout, watch_paths=watch)
        print(f"  generation {g}: {rec.notes}")
        print(f"  checkpointed -> runs/{run_id}/gen{g:03d}.json")


if __name__ == "__main__":
    main()
