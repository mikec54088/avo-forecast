"""Entry point. Experiment-agnostic by construction: everything is looked up
through avo.core.registry, so adding an experiment needs no change here."""
from __future__ import annotations

import argparse

from avo.core import registry


def main() -> None:
    ap = argparse.ArgumentParser(prog="avo")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("experiments", help="list available experiments")

    seeds = sub.add_parser("seeds", help="list an experiment's seed candidates")
    seeds.add_argument("experiment")

    args = ap.parse_args()

    if args.cmd == "experiments":
        for name in registry.available():
            cfg = registry.config(name)["experiment"]
            print(f"{name:20s} {cfg.get('status', '?'):9s} {cfg.get('description', '').strip().splitlines()[0]}")
    elif args.cmd == "seeds":
        exp = registry.load(args.experiment)
        for c in exp.seed_candidates():
            print(f"{c.candidate_id:24s} gen={c.generation} {c.rationale}")


if __name__ == "__main__":
    main()
