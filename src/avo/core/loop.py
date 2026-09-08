"""The generational loop: generate, score, select, repeat.

One generation is: ask the backend for N candidates, score everything the
experiment knows about, rank on the SELECTION subset only, confirm the leaders
on series selection never saw, checkpoint, and pick parents for the next round.

Two properties of this domain shape the design, both from DESIGN.md.

Generations are wide, not deep. Evaluation costs wall clock but nothing in
dollars, so 50 candidates take the same elapsed time as 1. The loop generates a
batch and harvests together rather than iterating serially.

Generations are SLOW. A candidate can only be scored on markets resolving after
it was written, so a freshly generated candidate has no score at all. Running
generations back to back would rank every new candidate on noise. The loop
refuses to advance until the youngest cohort has accumulated enough
observations, and says so rather than producing a confident ranking of nothing.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from avo.core.generate import generate_once
from avo.core.memory import GenerationRecord, RunMemory
from avo.core.selection import SelectionPolicy, Verdict, confirm
from avo.core.types import Score

MIN_OBSERVATIONS_TO_RANK = 2_000


def score_all(
    experiment,
    entries=None,
    history=None,
    subset: str = "all",
    only: Sequence[str] | None = None,
) -> list[Score]:
    """Score every seed candidate, skipping ones that fail to load.

    A candidate that will not import is a defect in that candidate, not a
    reason to abandon the generation.
    """
    out: list[Score] = []
    for c in experiment.seed_candidates():
        if only is not None and c.candidate_id not in only:
            continue
        try:
            loaded = experiment.load_candidate(c)
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {c.candidate_id}: {exc!r}", flush=True)
            continue
        out.append(experiment.score(c, loaded, entries=entries, history=history,
                                    subset=subset))
    return out


def rank_and_confirm(
    experiment, entries, history, policy: SelectionPolicy, gate=None
) -> tuple[list[Score], list[Verdict]]:
    """Rank on selection series; confirm on the held-out ones.

    Confirmation is computed for every candidate rather than only the winners.
    It costs the same pass, and the interesting number is how often a candidate
    that ranked well on selection fails to hold up -- which is the rate at
    which the loop would fool itself if the guard were absent.
    """
    sel = score_all(experiment, entries, history, subset="selection")
    conf = {s.candidate_id: s
            for s in score_all(experiment, entries, history, subset="confirmation")}
    verdicts = [confirm(s, conf[s.candidate_id], gate=gate)
                for s in sel if s.candidate_id in conf]
    verdicts.sort(key=lambda v: -v.selection.primary)
    return sel, verdicts


def ready_to_rank(
    scores: Sequence[Score], minimum: int = MIN_OBSERVATIONS_TO_RANK
) -> bool:
    """Has the newest cohort accumulated enough to be ranked at all?

    Guards the loop's most seductive failure: generating a second batch before
    the first has a real score, ranking on noise, and breeding from the winner.
    Candidates written minutes ago have no observations, and no amount of
    compute substitutes for markets actually resolving.
    """
    scored = [s.n_observations for s in scores if s.primary == s.primary]
    return bool(scored) and max(scored) >= minimum


def run_generation(
    experiment,
    backend,
    memory: RunMemory,
    candidates_dir: str | Path,
    repo_root: str | Path,
    generation: int,
    n_candidates: int,
    policy: SelectionPolicy,
    entries,
    history,
    timeout_s: int = 900,
    watch_paths: Sequence[str | Path] | None = None,
) -> GenerationRecord:
    """Generate a batch, score everything, checkpoint."""
    started = datetime.now(timezone.utc)
    prior = score_all(experiment, entries, history, subset="selection")
    parents = policy.choose_parents(prior, k=max(1, n_candidates // 4))
    by_id = {c.candidate_id: c for c in experiment.seed_candidates()}

    print(f"\n=== generation {generation}: {n_candidates} candidates ===")
    if parents:
        print("  parents: " + ", ".join(
            f"{p.candidate_id} ({p.primary:+.4f})" for p in parents))

    made: list[str] = []
    for i in range(n_candidates):
        parent = by_id.get(parents[i % len(parents)].candidate_id) if parents else None
        prompt = experiment.variation_prompt(parent, prior)
        a = generate_once(backend, experiment, prompt, candidates_dir, repo_root,
                          timeout_s=timeout_s, watch_paths=watch_paths)
        # Every accepted file, not just the first: one invocation can write
        # more than one candidate, and both land in the registry. A record that
        # names only one leaves the other unaccounted for -- which is how
        # persistent_quote_favourite came to be scored on 2026-09-05 without
        # appearing in any generation's candidate_ids.
        tags = [Path(k).stem for k in a.kept_paths] or (
            [Path(a.kept_path).stem] if a.kept_path else [])
        print(f"  [{i + 1}/{n_candidates}] "
              f"{'ACCEPT ' + ', '.join(tags) if a.accepted else 'REJECT ' + a.reason[:70]}",
              flush=True)
        if a.accepted:
            made.extend(tags)

    rec = GenerationRecord(
        generation=generation,
        started_at=started.isoformat(),
        backend=backend.name,
        candidate_ids=made,
        parents=[p.candidate_id for p in parents],
        scores=[asdict(s) for s in prior],
        notes=f"{len(made)}/{n_candidates} accepted",
    )
    memory.record_generation(rec)
    memory.record_scores(generation, prior, "selection")
    return rec
