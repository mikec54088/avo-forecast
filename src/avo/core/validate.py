"""Decide whether a generated candidate file is usable, without trusting it.

Generated code is imported and executed to check it. That is unavoidable -- a
candidate is a function, and the only way to know it returns probabilities is to
call it -- so the checking happens in a SUBPROCESS with a timeout. An agent
writes an infinite loop or a segfault eventually, and neither should take down
the run that was evaluating it.

Domain-neutral (INVARIANT #3): this knows a candidate is a module with
`forecast` and `MANIFEST`, and nothing about markets. The domain-specific half
-- what a valid return value is, what fields a market has -- comes from the
experiment through `probe_source`.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

VALIDATION_TIMEOUT_S = 60


@dataclass(frozen=True)
class Validation:
    """Why a candidate was accepted or rejected.

    `problems` is the interesting field. Over 20 invocations its distribution
    says whether a low accept rate is the agent's fault or the contract's: a
    spread of unrelated failures is a weak agent, the same failure 20 times is
    an ambiguous contract, and an ambiguous contract is a G1 problem worth
    fixing before generating at scale.
    """

    path: str
    accepted: bool
    problems: Sequence[str] = field(default_factory=tuple)
    candidate_id: str = ""

    def __str__(self) -> str:
        if self.accepted:
            return f"ACCEPT {Path(self.path).name} ({self.candidate_id})"
        return f"REJECT {Path(self.path).name}: {'; '.join(self.problems)}"


def validate_candidate_file(
    path: str | Path,
    probe_source: str,
    repo_root: str | Path,
    timeout_s: int = VALIDATION_TIMEOUT_S,
) -> Validation:
    """Import `path` in a subprocess and run the experiment's probe against it.

    `probe_source` is Python provided by the experiment. It receives the loaded
    module as `mod` and appends strings to `problems`. Returning no problems
    means accepted.
    """
    path = Path(path)
    if not path.exists():
        return Validation(str(path), False, ("file does not exist",))

    harness = f"""
import json, sys, importlib.util
problems = []
info = {{}}
try:
    spec = importlib.util.spec_from_file_location("_candidate", {str(path)!r})
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
except BaseException as exc:
    print(json.dumps({{"problems": ["module failed to import: %r" % (exc,)], "info": {{}}}}))
    sys.exit(0)

if not hasattr(mod, "forecast"):
    problems.append("no forecast() function")
if not hasattr(mod, "MANIFEST"):
    problems.append("no MANIFEST dict")
elif not isinstance(mod.MANIFEST, dict):
    problems.append("MANIFEST is not a dict")
else:
    for key in ("candidate_id", "created_at"):
        if key not in mod.MANIFEST:
            problems.append("MANIFEST missing %r" % key)
    info["candidate_id"] = str(mod.MANIFEST.get("candidate_id", ""))

if not problems:
{probe_source}

print(json.dumps({{"problems": problems, "info": info}}))
"""
    try:
        cp = subprocess.run(
            [sys.executable, "-c", harness],
            cwd=str(repo_root), capture_output=True, text=True,
            timeout=timeout_s, check=False,
        )
    except subprocess.TimeoutExpired:
        msg = (f"validation timed out after {timeout_s}s"
               " (likely an infinite loop in forecast())")
        return Validation(str(path), False, (msg,))

    line = next((ln for ln in reversed(cp.stdout.splitlines()) if ln.startswith("{")), "")
    if not line:
        detail = (cp.stderr or cp.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else "no output"
        return Validation(str(path), False, (f"validation harness produced no verdict: {tail}",))

    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return Validation(str(path), False, ("validation harness output was not JSON",))

    problems = tuple(str(p) for p in payload.get("problems", []))
    return Validation(
        path=str(path),
        accepted=not problems,
        problems=problems,
        candidate_id=str(payload.get("info", {}).get("candidate_id", "")),
    )


def new_candidate_files(
    files_written: Sequence[str], candidates_dir: str | Path
) -> list[str]:
    """The subset of a backend's written files that look like candidate modules.

    An agent may also write scratch notes, tests, or a README. Only .py files
    landing in the experiment's candidates directory are considered, and dunder
    files are skipped so a touched __init__.py is not mistaken for a candidate.
    """
    root = Path(candidates_dir).resolve()
    out = []
    for f in files_written:
        p = Path(f).resolve()
        if p.suffix == ".py" and p.parent == root and not p.name.startswith("_"):
            out.append(str(p))
    return sorted(out)
