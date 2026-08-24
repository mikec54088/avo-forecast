"""Domain-NEUTRAL types. Nothing here may mention markets, prices, or kernels.

If you find yourself wanting to add a field that only one experiment uses, it
belongs in that experiment's own types module instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Candidate:
    """A solution artifact produced by a human or the agent."""

    candidate_id: str
    experiment: str
    generation: int
    parent_id: str | None
    created_at: datetime  # drives the temporal holdout
    module_path: str
    rationale: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Score:
    """Fitness result. `primary` is what selection sorts on, always."""

    candidate_id: str
    primary: float
    primary_ci: tuple[float, float]
    n_observations: int
    secondary: dict[str, float] = field(default_factory=dict)
    notes: str = ""


@dataclass(frozen=True)
class RunState:
    run_id: str
    experiment: str
    backend: str          # e.g. "claude:opus-5" — pinned, recorded, never mixed
    generation: int
    started_at: datetime
    scored: list[Score] = field(default_factory=list)
