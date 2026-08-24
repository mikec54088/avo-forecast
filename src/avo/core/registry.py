"""Experiment discovery. Keeps core free of any import of a specific experiment."""
from __future__ import annotations

import importlib
import tomllib
from pathlib import Path
from typing import Any

EXPERIMENTS_ROOT = Path(__file__).resolve().parents[3] / "experiments"


def available() -> list[str]:
    return sorted(
        p.name for p in EXPERIMENTS_ROOT.iterdir()
        if p.is_dir() and (p / "experiment.toml").exists()
    )


def config(name: str) -> dict[str, Any]:
    path = EXPERIMENTS_ROOT / name / "experiment.toml"
    if not path.exists():
        raise KeyError(f"unknown experiment {name!r}; available: {available()}")
    return tomllib.loads(path.read_text())


def load(name: str):
    """Return the Experiment instance for `name`."""
    cfg = config(name)
    module = importlib.import_module(cfg["experiment"]["entrypoint"])
    return module.build()
