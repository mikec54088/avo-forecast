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
    gen.add_argument("--timeout", type=int, default=600, help="seconds per invocation")
    gen.add_argument("--prompt-file", default=None,
                     help="file holding the prompt; omit to use the built-in probe prompt")
    gen.add_argument("--out-dir", default="runs/phase4",
                     help="where accepted candidates and the trial log are stashed")
    gen.add_argument("--keep-rejects", action="store_true",
                     help="leave invalid candidate files on disk for inspection")

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

Do not modify any other file."""


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

    attempts = run_trial(backend, exp, prompt, cdir, repo_root, n=args.n,
                         timeout_s=args.timeout, out_dir=repo_root / args.out_dir,
                         keep=args.keep_rejects)
    print("\n" + summarise(attempts))


if __name__ == "__main__":
    main()
