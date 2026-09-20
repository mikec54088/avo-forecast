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

from avo.core.generate import generate_once, is_quota_exhausted
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
        # `entries` may be a callable returning a fresh chunk iterator. An
        # iterator itself cannot be reused across 42 candidates -- it is
        # exhausted after the first -- so the caller passes a factory and each
        # candidate gets its own pass. Core stays ignorant of what a chunk is.
        if callable(entries):
            out.append(experiment.score(c, loaded, chunks=entries(),
                                        history=history, subset=subset))
        else:
            out.append(experiment.score(c, loaded, entries=entries,
                                        history=history, subset=subset))
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


def pool_signature(policy: SelectionPolicy, scored: Sequence[Score]) -> dict[str, int]:
    """Each candidate's gate verdict -- what selection would actually act on."""
    return {s.candidate_id: policy.verdict(s) for s in scored}


def pool_changed(
    policy: SelectionPolicy, scored: Sequence[Score], memory: RunMemory
) -> tuple[bool, str]:
    """Has any verdict moved since the last generation?

    Generating on a calendar rather than on evidence re-derives the same
    parents and spends a full agentic session to do it: generations 3 and 4
    chose the identical pair, and five consecutive generations produced three
    candidates between them. A generation is only worth its quota when the
    pool it selects from has actually changed -- a new candidate appearing, or
    an existing one crossing a verdict boundary.
    """
    gens = memory.generations()
    if not gens:
        return True, "first generation"
    prev_scores = [Score(**{k: (tuple(v) if k == "primary_ci" else v)
                            for k, v in d.items()})
                   for d in gens[-1].scores]
    before = pool_signature(policy, prev_scores)
    after = pool_signature(policy, scored)
    if not before:
        return True, "no prior scores recorded"
    new_ids = set(after) - set(before)
    if new_ids:
        return True, f"{len(new_ids)} new candidate(s) scored"
    moved = [c for c in after if before.get(c) != after[c]]
    if moved:
        return True, f"{len(moved)} verdict(s) changed: {', '.join(sorted(moved)[:3])}"
    return False, "no candidate is new and no verdict has moved"


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
    prior: Sequence[Score] | None = None,
    n_explore: int = 1,
) -> GenerationRecord:
    """Generate a batch, score everything, checkpoint."""
    started = datetime.now(timezone.utc)
    if prior is None:
        prior = score_all(experiment, entries, history, subset="selection")
    exploit = policy.choose_parents(prior, k=max(1, n_candidates // 4))

    # One slot explores. See SelectionPolicy.choose_explore: the exploit sort
    # ranks every proven candidate above every unproven one, so with verdicts
    # arriving in weeks and generations running in days, a newly written
    # candidate can never be bred from and no line deepens.
    explore = policy.choose_explore(prior, k=n_explore) if n_candidates > 1 else []
    explore = [e for e in explore
               if e.candidate_id not in {x.candidate_id for x in exploit}]
    parents = exploit + explore
    by_id = {c.candidate_id: c for c in experiment.seed_candidates()}

    print(f"\n=== generation {generation}: {n_candidates} candidates ===")
    if exploit:
        print("  exploit: " + ", ".join(
            f"{p.candidate_id} ({p.primary:+.4f})" for p in exploit))
    if explore:
        print("  explore: " + ", ".join(
            f"{p.candidate_id} ({p.primary:+.4f}, unproven)" for p in explore))

    made: list[str] = []
    stopped = ""
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
        elif is_quota_exhausted(a.reason):
            # Stop the batch. The window is the account's, not this
            # candidate's, so every remaining slot would fail identically --
            # generations 3 and 4 spent fourteen invocations learning that.
            stopped = (f"aborted after {i + 1}/{n_candidates}: "
                       f"backend out of budget ({a.reason[:60]})")
            print(f"  {stopped}", flush=True)
            break

    rec = GenerationRecord(
        generation=generation,
        started_at=started.isoformat(),
        backend=backend.name,
        candidate_ids=made,
        parents=[p.candidate_id for p in parents],
        scores=[asdict(s) for s in prior],
        notes=(f"{len(made)}/{n_candidates} accepted"
               + (f"; {stopped}" if stopped else "")),
    )
    memory.record_generation(rec)
    memory.record_scores(generation, prior, "selection")
    return rec
