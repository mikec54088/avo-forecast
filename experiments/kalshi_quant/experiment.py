"""kalshi-quant implements core.interfaces.Experiment.

This file is the ONLY thing core knows about. Everything domain-specific lives
below it.
"""
from __future__ import annotations

import importlib
import pkgutil
from datetime import datetime
from typing import Any, Sequence

from avo.core.holdout import assert_clean, eligible
from avo.core.types import Candidate, Score
from experiments.kalshi_quant.observations import Entry, SeriesHistory, load_entries
from experiments.kalshi_quant.scoring import (
    Observation,
    bootstrap_ci,
    simulate_fill,
    skill_score,
)

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

    def score(
        self,
        candidate: Candidate,
        loaded: Any,
        entries: Sequence[Entry] | None = None,
        history: SeriesHistory | None = None,
    ) -> Score:
        """Brier skill vs the market's implied probability (INVARIANT #2).

        `entries` and `history` are injectable so a caller scoring many
        candidates loads the observation set once. Both default to reading the
        captured Parquet.
        """
        if entries is None:
            entries = load_entries()
        if history is None:
            history = SeriesHistory(list(entries))

        # INVARIANT #1: only truth that resolved strictly after this candidate
        # existed. assert_clean afterwards is not redundant -- it is the
        # tripwire that catches a future change to eligible().
        keep = eligible(candidate, list(entries), lambda e: e.resolved_at)
        assert_clean(candidate, keep, lambda e: e.resolved_at)
        if not keep:
            return Score(
                candidate_id=candidate.candidate_id,
                primary=float("nan"),
                primary_ci=(float("nan"), float("nan")),
                n_observations=0,
                notes="no observations resolved after created_at",
            )

        obs: list[Observation] = []
        errors = 0
        for e in keep:
            try:
                p = float(loaded.forecast(e.market, history.context_for(e)))
            except Exception:  # noqa: BLE001 - a broken candidate must not kill the run
                errors += 1
                continue
            # NaN fails this comparison too, so it is caught here as well.
            if not (0.0 <= p <= 1.0):
                errors += 1
                continue
            obs.append(Observation(p, e.market.implied_prob, e.outcome))

        if not obs:
            return Score(
                candidate_id=candidate.candidate_id,
                primary=float("nan"),
                primary_ci=(float("nan"), float("nan")),
                n_observations=0,
                notes=f"no usable forecasts ({errors} errors)",
            )

        cand_brier, market_brier, skill = skill_score(obs)
        lo, hi = bootstrap_ci(obs)

        # Secondary, never selected on (INVARIANT #2). Reported because skill
        # earned only on markets simulate_fill refuses is not tradeable.
        fillable = sum(
            1 for e in keep
            if simulate_fill(e.market, "yes", 1).filled
        )
        return Score(
            candidate_id=candidate.candidate_id,
            primary=skill,
            primary_ci=(lo, hi),
            n_observations=len(obs),
            secondary={
                "candidate_brier": cand_brier,
                "market_brier": market_brier,
                "fillable_fraction": fillable / len(keep),
                "forecast_errors": float(errors),
            },
            notes=f"entry policy: last snapshot before resolution; {errors} errors",
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
