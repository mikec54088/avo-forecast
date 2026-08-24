"""kalshi-quant implements core.interfaces.Experiment.

This file is the ONLY thing core knows about. Everything domain-specific lives
below it.
"""
from __future__ import annotations

import importlib
import pkgutil
from datetime import datetime
from typing import Any, Sequence

from avo.core.types import Candidate, Score

CONTRACT = "forecast(market: MarketSnapshot, context: ForecastContext) -> float"


class KalshiQuantExperiment:
    name = "kalshi_quant"

    def load_candidate(self, candidate: Candidate) -> Any:
        mod = importlib.import_module(candidate.module_path)
        if not hasattr(mod, "forecast"):
            raise TypeError(f"{candidate.module_path} missing forecast(); expected {CONTRACT}")
        if not hasattr(mod, "MANIFEST"):
            raise TypeError(f"{candidate.module_path} missing MANIFEST dict")
        return mod

    def score(self, candidate: Candidate, loaded: Any) -> Score:
        raise NotImplementedError(
            "Phase 2. Wire captured snapshots + resolutions through "
            "avo.core.holdout, then experiments.kalshi_quant.scoring."
        )

    def seed_candidates(self) -> Sequence[Candidate]:
        pkg = importlib.import_module("experiments.kalshi_quant.candidates")
        out: list[Candidate] = []
        for info in pkgutil.iter_modules(pkg.__path__):
            mod = importlib.import_module(f"{pkg.__name__}.{info.name}")
            m = mod.MANIFEST
            out.append(
                Candidate(
                    candidate_id=m["candidate_id"],
                    experiment=self.name,
                    generation=m.get("generation", 0),
                    parent_id=m.get("parent_id"),
                    created_at=datetime.fromisoformat(m["created_at"]),
                    module_path=f"{pkg.__name__}.{info.name}",
                    rationale=m.get("rationale", ""),
                )
            )
        return out

    def variation_prompt(self, parent: Candidate, siblings: Sequence[Score]) -> str:
        raise NotImplementedError("Phase 4.")


def build() -> KalshiQuantExperiment:
    return KalshiQuantExperiment()
