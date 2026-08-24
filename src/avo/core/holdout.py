"""Temporal holdout. INVARIANT #1 — universal across experiments, no bypass.

A candidate is scored ONLY on observations whose ground truth became known
strictly after the candidate was created. This is what makes replay against
locally captured data legitimate rather than in-sample fitting.

Generic on purpose: any experiment with time-stamped ground truth uses this.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Sequence, TypeVar

from avo.core.types import Candidate

T = TypeVar("T")


class HoldoutViolation(RuntimeError):
    pass


def eligible(
    candidate: Candidate, observations: Sequence[T], resolved_at: Callable[[T], datetime]
) -> list[T]:
    return [o for o in observations if resolved_at(o) > candidate.created_at]


def assert_clean(
    candidate: Candidate, observations: Sequence[T], resolved_at: Callable[[T], datetime]
) -> None:
    """Raise if any observation predates the candidate. Call before scoring."""
    bad = [o for o in observations if resolved_at(o) <= candidate.created_at]
    if bad:
        raise HoldoutViolation(
            f"{candidate.candidate_id}: {len(bad)} observations at or before "
            f"created_at={candidate.created_at.isoformat()}"
        )


def is_eligible(candidate_created_at: datetime, resolved_at_ts: datetime) -> bool:
    return resolved_at_ts > candidate_created_at
