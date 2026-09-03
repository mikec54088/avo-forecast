"""What the loop remembers between generations.

Deliberately a flat JSON log rather than a database. A run must be resumable
after a crash, inspectable by a human mid-run, and diffable -- and the volume is
tiny: tens of candidates per generation, not millions of rows.

INVARIANT #5: run artifacts are never committed. This writes under runs/.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from avo.core.types import Score


@dataclass
class GenerationRecord:
    """One generation: what was tried, what it earned, what survived."""

    generation: int
    started_at: str
    backend: str
    candidate_ids: list[str] = field(default_factory=list)
    scores: list[dict] = field(default_factory=list)
    verdicts: list[dict] = field(default_factory=list)
    parents: list[str] = field(default_factory=list)
    notes: str = ""


class RunMemory:
    """Append-only record of a run, one JSON file per generation.

    Checkpointed after every generation rather than at the end. DESIGN.md calls
    resumability mandatory because subscription limits can end a run mid-flight,
    and a generational loop that loses a generation loses days of wall clock --
    candidates can only be scored on markets resolving after they were written,
    so the work cannot be redone faster.
    """

    def __init__(self, run_id: str, root: str | Path = "runs") -> None:
        self.run_id = run_id
        self.dir = Path(root) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)

    def record_generation(self, rec: GenerationRecord) -> Path:
        path = self.dir / f"gen{rec.generation:03d}.json"
        path.write_text(json.dumps(asdict(rec), indent=1, default=str))
        return path

    def record_scores(
        self, generation: int, scores: Sequence[Score], subset: str
    ) -> None:
        path = self.dir / f"gen{generation:03d}-scores-{subset}.json"
        path.write_text(json.dumps([asdict(s) for s in scores], indent=1, default=str))

    def generations(self) -> list[GenerationRecord]:
        return [GenerationRecord(**json.loads(f.read_text()))
                for f in sorted(self.dir.glob("gen[0-9][0-9][0-9].json"))]

    def latest_generation(self) -> int:
        return max((g.generation for g in self.generations()), default=0)

    def tried_before(self) -> list[dict]:
        """Every candidate scored so far, with its skill and P&L.

        This is what variation_prompt turns into "do not resubmit these". A loop
        that cannot remember its own failures rediscovers them: the generic
        prompt used on 2026-09-01 produced 25 candidates collapsing to about 7
        ideas, four of them near-identical log-odds midpoints.
        """
        out: list[dict] = []
        for g in self.generations():
            for s in g.scores:
                out.append({
                    "generation": g.generation,
                    "candidate_id": s.get("candidate_id"),
                    "skill": s.get("primary"),
                    "ci": s.get("primary_ci"),
                    "n": s.get("n_observations"),
                    "pnl": (s.get("secondary") or {}).get("pnl_per_contract"),
                })
        return out


def new_run_id(experiment: str) -> str:
    return f"{experiment}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"


class PriorsStore:
    """Cross-run memory. Still deliberately unbuilt.

    The stub it replaced said "do not build until run #3", and that is still
    right: distilled priors tuned against two runs would be heuristics fitted to
    imagination. RunMemory covers within-run memory, which is what the
    generational loop actually needs.

    Never share priors across experiments. Retrieval would surface superficially
    similar but wrong precedents and prime bad hypotheses early, when the search
    is most steerable, and a bad conclusion has no natural expiry.
    """

    def __init__(self, experiment: str, root: str) -> None:
        raise NotImplementedError("Phase 5+. Do not build until run #3.")
