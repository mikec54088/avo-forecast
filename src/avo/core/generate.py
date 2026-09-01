"""Run a backend once, keep the candidate if it validates.

Phase 4 is deliberately one invocation in, one candidate out, no memory and no
selection. The point of the phase is not to produce good candidates -- that
needs generation-1 scores and a real variation_prompt -- but to find out how
often a coding agent can produce a candidate the scorer will accept AT ALL, and
why it fails when it does not.
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from avo.core.validate import Validation, new_candidate_files, validate_candidate_file


def stamp_created_at(path: str | Path, when: datetime | None = None) -> str:
    """Rewrite a candidate's MANIFEST created_at to its real creation time.

    INVARIANT #1 scores a candidate only on ground truth that resolved after
    created_at, so that field is the entire holdout. An agent asked to write a
    MANIFEST copies the surrounding files, and the first live invocation on
    2026-09-01 did exactly that -- it inherited its siblings' timestamp rather
    than using its own. Once that is systematic, generated candidates are scored
    on data that predates them, which is in-sample fitting arriving through the
    front door, past the invariant meant to stop it.

    So it is stamped here rather than requested in the prompt. A rule the
    harness enforces cannot be forgotten by a model having an off day, and this
    is the one field where being wrong is silent -- a stale timestamp produces a
    better-looking score, not an error.

    Returns the timestamp written, or "" if no replacement was made.
    """
    path = Path(path)
    when = when or datetime.now(timezone.utc)
    iso = when.isoformat()
    src = path.read_text()
    # Match the created_at entry whatever quoting or spacing the agent used.
    # Key and value may each be single- or double-quoted; agents are not
    # consistent about it, and a missed match here fails silently.
    pattern = re.compile(r"""(['"]created_at['"]\s*:\s*)(['"])(.*?)\2""")
    new_src, n = pattern.subn(lambda m: f'{m.group(1)}"{iso}"', src, count=1)
    if not n:
        return ""
    path.write_text(new_src)
    return iso


@dataclass(frozen=True)
class Attempt:
    """One invocation, whatever happened."""

    attempt_id: str
    backend: str
    accepted: bool
    reason: str
    elapsed_s: float
    files_written: Sequence[str] = field(default_factory=tuple)
    validations: Sequence[dict] = field(default_factory=tuple)
    kept_path: str = ""
    created_at: str = ""


def generate_once(
    backend,
    experiment,
    prompt: str,
    candidates_dir: str | Path,
    repo_root: str | Path,
    timeout_s: int = 600,
    keep: bool = False,
) -> Attempt:
    """Invoke the backend once and validate whatever candidate it wrote.

    The agent writes directly into the experiment's candidates directory,
    because that is where a candidate has to end up and where its imports
    resolve. A file that fails validation is REMOVED unless `keep` is set: an
    invalid candidate left on disk would be picked up by seed_candidates() and
    break every later run, and silently poisoning the registry is a worse
    failure than losing a bad file.
    """
    candidates_dir = Path(candidates_dir)
    attempt_id = uuid.uuid4().hex[:8]
    before = {p.name for p in candidates_dir.glob("*.py")}

    result = backend.run(prompt, str(repo_root), timeout_s)

    if result.timed_out:
        reason = f"backend timed out after {timeout_s}s"
    elif result.exit_code == 127:
        reason = result.stderr or "backend executable not found"
    elif result.exit_code != 0:
        tail = (result.stderr or result.output or "").strip().splitlines()
        reason = f"backend exited {result.exit_code}: {tail[-1] if tail else 'no output'}"
    else:
        reason = ""

    # Diff the directory as well as trusting files_written: an agent may write
    # through a path the snapshot did not resolve identically.
    after = {p.name for p in candidates_dir.glob("*.py")}
    fresh = sorted(
        {str(candidates_dir / n) for n in after - before}
        | set(new_candidate_files(result.files_written, candidates_dir))
    )

    if reason and not fresh:
        return Attempt(attempt_id, backend.name, False, reason, result.elapsed_s,
                       tuple(result.files_written))

    if not fresh:
        return Attempt(attempt_id, backend.name, False,
                       "no candidate file was written", result.elapsed_s,
                       tuple(result.files_written))

    probe = experiment.validation_probe()
    validations: list[Validation] = [
        validate_candidate_file(f, probe, repo_root) for f in fresh
    ]
    good = [v for v in validations if v.accepted]

    if good:
        for v in validations:
            if not v.accepted and not keep:
                Path(v.path).unlink(missing_ok=True)
        # Stamp AFTER validation: the probe checks created_at is present, and
        # this makes it truthful. See stamp_created_at.
        stamped = stamp_created_at(good[0].path)
        return Attempt(attempt_id, backend.name, True, "accepted", result.elapsed_s,
                       tuple(result.files_written),
                       tuple(asdict(v) for v in validations), good[0].path, stamped)

    if not keep:
        for v in validations:
            Path(v.path).unlink(missing_ok=True)
    return Attempt(
        attempt_id, backend.name, False,
        "; ".join(p for v in validations for p in v.problems) or "rejected",
        result.elapsed_s, tuple(result.files_written),
        tuple(asdict(v) for v in validations),
    )


def run_trial(
    backend,
    experiment,
    prompt: str,
    candidates_dir: str | Path,
    repo_root: str | Path,
    n: int = 20,
    timeout_s: int = 600,
    out_dir: str | Path | None = None,
    keep: bool = False,
) -> list[Attempt]:
    """Invoke `n` times and report the accept rate.

    ROADMAP Phase 4 is done when this exceeds 80%. Accepted candidates are moved
    aside rather than left in place: a trial is a measurement of the harness,
    and leaving twenty unscored candidates in the registry would corrupt the
    next real generation.
    """
    attempts: list[Attempt] = []
    stash = Path(out_dir) if out_dir else None
    if stash:
        stash.mkdir(parents=True, exist_ok=True)

    for i in range(n):
        a = generate_once(backend, experiment, prompt, candidates_dir,
                          repo_root, timeout_s, keep=keep)
        if a.accepted and stash and a.kept_path:
            dest = stash / Path(a.kept_path).name
            shutil.move(a.kept_path, dest)
            a = Attempt(a.attempt_id, a.backend, a.accepted, a.reason, a.elapsed_s,
                        a.files_written, a.validations, str(dest))
        attempts.append(a)
        print(f"  [{i + 1}/{n}] {'ACCEPT' if a.accepted else 'REJECT'} "
              f"{a.elapsed_s:5.0f}s  {a.reason[:90]}", flush=True)

    if stash:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        (stash / f"trial-{stamp}.json").write_text(
            json.dumps([asdict(a) for a in attempts], indent=1, default=str)
        )
    return attempts


def summarise(attempts: Sequence[Attempt]) -> str:
    if not attempts:
        return "no attempts"
    ok = sum(a.accepted for a in attempts)
    rate = ok / len(attempts)
    headline = f"accepted {ok}/{len(attempts)} = {rate:.0%}   (ROADMAP Phase 4 gate: >80%)"
    mean_s = sum(a.elapsed_s for a in attempts) / len(attempts)
    lines = [headline, f"mean wall clock {mean_s:.0f}s"]
    fails: dict[str, int] = {}
    for a in attempts:
        if not a.accepted:
            fails[a.reason[:80]] = fails.get(a.reason[:80], 0) + 1
    if fails:
        lines.append("failure modes:")
        lines += [f"  {n:>3}x  {r}" for r, n in
                  sorted(fails.items(), key=lambda kv: -kv[1])]
        lines.append(
            "  (one failure repeated is an ambiguous CONTRACT, which is a G1"
            "\n   problem worth fixing before generating at scale; a scatter"
            "\n   of unrelated failures is a weak prompt or a weak backend)"
        )
    return "\n".join(lines)
