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
import subprocess
import time
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


def git_dirty(repo_root: str | Path) -> set[str]:
    """Repo-relative paths git currently reports as changed or untracked."""
    try:
        cp = subprocess.run(["git", "status", "--porcelain", "-z"],
                            cwd=str(repo_root), capture_output=True, text=True,
                            timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return set()
    out = set()
    for chunk in cp.stdout.split("\0"):
        if len(chunk) > 3:
            out.add(chunk[3:])
    return out


def out_of_scope_changes(
    repo_root: str | Path, candidates_dir: str | Path, before: set[str]
) -> list[str]:
    """Repo paths that changed outside the candidates directory.

    Detection only. Acting on it is `revert_out_of_scope`, which is off by
    default -- see the warning there.
    """
    repo_root = Path(repo_root)
    try:
        rel_scope = str(Path(candidates_dir).resolve().relative_to(repo_root.resolve()))
    except ValueError:
        rel_scope = "\0"
    after = git_dirty(repo_root)
    return sorted(
        p for p in after - before
        if not p.startswith(rel_scope) and not p.startswith("runs/")
    )


def revert_out_of_scope(
    repo_root: str | Path,
    candidates_dir: str | Path,
    before: set[str],
    delete_untracked: bool = False,
) -> list[str]:
    """Undo tracked repo changes outside the candidates directory.

    OFF BY DEFAULT, and it should stay that way unless the caller controls the
    whole machine for the duration of the run.

    This cannot distinguish an agent's edit from a human's. It compares
    `git status` before and after an invocation that takes fifteen minutes, so
    anything written during that window looks like the agent did it. On
    2026-09-03 that destroyed a half-built Phase 5 -- selection.py and
    memory.py reverted to their stubs, loop.py and its tests deleted outright
    -- because they were written while a generation was in flight. The earlier
    safety rule (only touch paths that were clean beforehand) protected
    uncommitted work that already existed and did nothing for work created
    during the run.

    Deleting untracked files is worse than reverting tracked ones: a tracked
    file reverts to its committed state, an untracked file is simply gone. It
    now requires `delete_untracked` on top of opting in at all.

    The correct fix is isolation rather than cleanup -- run the agent in a
    separate git worktree so it cannot reach the main tree, and copy the
    candidate back. Until that exists, detection with a loud report is the
    honest default.
    """
    repo_root = Path(repo_root)
    stray = out_of_scope_changes(repo_root, candidates_dir, before)
    if not stray:
        return []

    tracked, untracked = [], []
    for rel in stray:
        cp = subprocess.run(["git", "ls-files", "--error-unmatch", rel],
                            cwd=str(repo_root), capture_output=True,
                            text=True, timeout=30, check=False)
        (tracked if cp.returncode == 0 else untracked).append(rel)

    if tracked:
        subprocess.run(["git", "checkout", "--", *tracked], cwd=str(repo_root),
                       capture_output=True, timeout=60, check=False)
    if delete_untracked:
        for rel in untracked:
            target = repo_root / rel
            if target.is_file():
                target.unlink(missing_ok=True)
        return stray
    return tracked


def restore_overwritten(
    repo_root: str | Path, paths: Sequence[str | Path]
) -> tuple[list[str], list[str]]:
    """Put back candidates an agent overwrote. Returns (restored, unrecoverable).

    A candidate filename that already existed is not a new candidate, it is a
    replacement for one that was being scored. The loss is silent in a way the
    directory cannot show afterwards: `after - before` never sees an overwrite,
    so the file is neither validated nor stamped, while the run's record still
    names the candidate that used to be there. On 2026-09-05 two attempts in one
    generation both wrote ladder_upper_body.py, and gen001.json lists it twice
    for two different candidates, one of which no longer exists.

    A tracked file is restored from git and is genuinely recovered. An untracked
    one was destroyed by the write itself and cannot be. It is moved aside to
    `<name>.py.overwritten` rather than left in place or deleted: leaving it
    would keep the agent's unvalidated, unstamped candidate sitting under the
    old one's filename, where seed_candidates() imports it as the candidate it
    replaced -- the same silent registry poisoning this function exists to stop,
    only wearing the dead candidate's name. Deleting it would destroy the one
    copy of work that may be worth reading. Neither file is a candidate
    afterwards, which is the honest outcome: the original is gone.
    """
    repo_root = Path(repo_root)
    restored: list[str] = []
    lost: list[str] = []

    def _git(*args: str) -> int:
        return subprocess.run(["git", *args], cwd=str(repo_root),
                              capture_output=True, text=True, timeout=60,
                              check=False).returncode

    for path in paths:
        src = Path(path)
        try:
            rel = str(src.resolve().relative_to(repo_root.resolve()))
        except ValueError:
            rel = ""
        # Recoverable only if git is holding a copy of what was there.
        if rel and _git("ls-files", "--error-unmatch", rel) == 0 \
                and _git("checkout", "--", rel) == 0:
            restored.append(rel)
            continue
        # Not recoverable. Move the replacement out of the registry so it stops
        # impersonating the candidate it destroyed, without deleting it.
        try:
            src.replace(src.parent / (src.name + ".overwritten"))
        except OSError:
            pass
        lost.append(rel or str(src))
    return restored, lost


def external_writes(paths: Sequence[str | Path], since: float) -> list[str]:
    """Files under `paths` modified after `since`. Reported, never reverted.

    These live outside the repo -- the user's memory directory above all -- so
    git cannot restore them and this must not guess. Silently rewriting a
    user's own files would be a worse failure than the stray write itself.
    """
    out = []
    for root in paths:
        root = Path(root)
        if not root.exists():
            continue
        for f in ([root] if root.is_file() else root.rglob("*")):
            try:
                if f.is_file() and f.stat().st_mtime > since:
                    out.append(str(f))
            except OSError:
                continue
    return sorted(out)


# Substrings that mean the ACCOUNT is out of budget, not that this candidate
# failed. Generations 3 and 4 each burned seven further invocations against an
# exhausted window -- every one rejected identically, ~30 seconds apart -- and
# recorded 0/8 and 1/8 as though the agent had written bad candidates. It had
# not been asked. Retrying cannot succeed until the window resets, so the
# generation must stop rather than spend the remaining slots proving it.
QUOTA_MARKERS = (
    "session limit",
    "usage limit",
    "rate limit",
    "quota",
    "credit balance",
    "insufficient credit",
)


def is_quota_exhausted(reason: str) -> bool:
    """Does this rejection mean the account is out of budget?

    Matched on the reason string because that is all the backend gives us: the
    CLI exits 1 for a bad prompt and 1 for an exhausted window alike, so the
    exit code cannot distinguish them.
    """
    low = (reason or "").lower()
    return any(m in low for m in QUOTA_MARKERS)


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
    reverted: Sequence[str] = field(default_factory=tuple)
    external: Sequence[str] = field(default_factory=tuple)
    # EVERY accepted candidate this attempt produced, not just the first. One
    # invocation can write more than one, and a file the record does not name
    # is a file nothing later accounts for -- see generate_once.
    kept_paths: Sequence[str] = field(default_factory=tuple)
    collided: Sequence[str] = field(default_factory=tuple)


def generate_once(
    backend,
    experiment,
    prompt: str,
    candidates_dir: str | Path,
    repo_root: str | Path,
    timeout_s: int = 600,
    keep: bool = False,
    watch_paths: Sequence[str | Path] | None = None,
    revert_scope: bool = False,
    delete_untracked: bool = False,
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
    dirty_before = git_dirty(repo_root)
    started_at = time.time()

    result = backend.run(prompt, str(repo_root), timeout_s)

    # Detected and reported by default; reverted only if the caller opts in.
    # This cannot tell an agent's edit from a concurrent human one -- see
    # revert_out_of_scope.
    if revert_scope:
        reverted = tuple(revert_out_of_scope(repo_root, candidates_dir,
                                             dirty_before, delete_untracked))
        stray = reverted
    else:
        stray = tuple(out_of_scope_changes(repo_root, candidates_dir, dirty_before))
        reverted = ()
    external = tuple(external_writes(watch_paths or (), started_at))
    if stray:
        verb = "reverted" if revert_scope else "NOTE out-of-scope"
        print(f"      {verb} {len(stray)} change(s) outside the candidates dir: "
              f"{', '.join(stray[:4])}", flush=True)
    if external:
        print(f"      NOTE {len(external)} file(s) written outside the repo "
              f"(not reverted): {', '.join(external[:3])}", flush=True)

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

    # A write onto an EXISTING candidate filename is a replacement, not a new
    # candidate, and it is never what the loop wants: the file it landed on is
    # already in the registry and already being scored. Only files_written can
    # surface one, since an overwrite leaves `after - before` unchanged. Take
    # them out of `fresh` before validation -- accepting one would stamp a
    # candidate the record already attributes to something else.
    collided = [f for f in fresh if Path(f).name in before]
    if collided:
        fresh = [f for f in fresh if f not in set(collided)]
        restored, lost = restore_overwritten(repo_root, collided)
        if restored:
            print(f"      restored {len(restored)} overwritten candidate(s): "
                  f"{', '.join(Path(r).name for r in restored)}", flush=True)
        if lost:
            print(f"      LOST {len(lost)} overwritten candidate(s), untracked and "
                  f"unrecoverable: {', '.join(Path(x).name for x in lost)}", flush=True)

    if reason and not fresh:
        return Attempt(attempt_id, backend.name, False, reason, result.elapsed_s,
                       tuple(result.files_written), reverted=stray,
                       external=external, collided=tuple(collided))

    if not fresh:
        why = ("only overwrote existing candidate(s): "
               + ", ".join(Path(c).name for c in collided)) if collided else \
              "no candidate file was written"
        return Attempt(attempt_id, backend.name, False, why, result.elapsed_s,
                       tuple(result.files_written), reverted=stray,
                       external=external, collided=tuple(collided))

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
        #
        # EVERY accepted file, not just the first. One invocation can write two
        # candidates, and the rejected ones above are the only files this
        # function removes -- so a second accepted file stays on disk, where
        # seed_candidates() globs it into the registry. Stamping only good[0]
        # left it there carrying whatever created_at the agent chose, which is
        # the exact failure stamp_created_at exists to prevent, arriving through
        # a second door. On 2026-09-05 it put persistent_quote_favourite into
        # the registry backdated 29 hours, with 83% of its scored observations
        # already resolved when the file was written.
        #
        # One timestamp for the batch: they were written by one invocation, and
        # giving them different clocks would imply an ordering that does not
        # exist.
        when = datetime.now(timezone.utc)
        stamps = [stamp_created_at(v.path, when) for v in good]
        return Attempt(attempt_id, backend.name, True, "accepted", result.elapsed_s,
                       tuple(result.files_written),
                       tuple(asdict(v) for v in validations), good[0].path,
                       stamps[0], stray, external,
                       tuple(v.path for v in good), tuple(collided))

    if not keep:
        for v in validations:
            Path(v.path).unlink(missing_ok=True)
    return Attempt(
        attempt_id, backend.name, False,
        "; ".join(p for v in validations for p in v.problems) or "rejected",
        result.elapsed_s, tuple(result.files_written),
        tuple(asdict(v) for v in validations), reverted=stray, external=external,
        collided=tuple(collided),
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
    watch_paths: Sequence[str | Path] | None = None,
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
                          repo_root, timeout_s, keep=keep, watch_paths=watch_paths)
        if a.accepted and stash and a.kept_paths:
            # Move all of them. Leaving the second behind would put an unscored
            # candidate in the registry, which is what a trial must not do.
            moved = []
            for src in a.kept_paths:
                dest = stash / Path(src).name
                shutil.move(src, dest)
                moved.append(str(dest))
            a = Attempt(a.attempt_id, a.backend, a.accepted, a.reason, a.elapsed_s,
                        a.files_written, a.validations, moved[0], a.created_at,
                        a.reverted, a.external, tuple(moved), a.collided)
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
    """Accept rate, split by what kind of failure occurred.

    Timeouts are reported apart from validation failures because they mean
    different things and have different fixes. A timeout says the budget was
    too small -- on 2026-09-01 three of twenty ran past a 420s ceiling while
    accepted runs reached 355s, so the ceiling sat inside the distribution. A
    validation failure says the candidate was wrong, or the probe was.
    """
    if not attempts:
        return "no attempts"
    ok = sum(a.accepted for a in attempts)
    timeouts = [a for a in attempts if not a.accepted and "timed out" in a.reason]
    invalid = [a for a in attempts if not a.accepted and "timed out" not in a.reason]
    rate = ok / len(attempts)
    mean_s = sum(a.elapsed_s for a in attempts) / len(attempts)
    slowest_ok = max((a.elapsed_s for a in attempts if a.accepted), default=0.0)
    lines = [
        f"accepted {ok}/{len(attempts)} = {rate:.0%}   (ROADMAP Phase 4 gate: >80%)",
        f"  {len(timeouts):>3} timed out      (budget too small, not a bad candidate)",
        f"  {len(invalid):>3} failed validation",
        f"mean wall clock {mean_s:.0f}s   slowest accepted {slowest_ok:.0f}s",
    ]
    n_rev = sum(len(a.reverted) for a in attempts)
    n_ext = sum(len(a.external) for a in attempts)
    if n_rev or n_ext:
        lines.append(f"out-of-scope writes: {n_rev} reverted in-repo, "
                     f"{n_ext} outside the repo (reported only)")
    n_col = sum(len(a.collided) for a in attempts)
    n_multi = sum(1 for a in attempts if len(a.kept_paths) > 1)
    if n_col:
        lines.append(f"{n_col} write(s) landed on an existing candidate filename "
                     f"and were refused")
    if n_multi:
        lines.append(f"{n_multi} invocation(s) produced more than one candidate")

    if invalid:
        fails: dict[str, int] = {}
        for a in invalid:
            fails[a.reason[:80]] = fails.get(a.reason[:80], 0) + 1
        lines.append("validation failures:")
        lines += [f"  {n:>3}x  {r}" for r, n in
                  sorted(fails.items(), key=lambda kv: -kv[1])]
        note = (
            "  (one failure repeated points at the CONTRACT or the probe, not\n"
            "   the agent -- on 2026-09-01 four 'never deviates' rejections\n"
            "   were all caused by a probe that held book depth, history and\n"
            "   close time constant. A scatter of unrelated failures is a\n"
            "   weak prompt or a weak backend.)"
        )
        lines.append(note)
    return "\n".join(lines)
