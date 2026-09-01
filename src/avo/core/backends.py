"""Coding-agent CLIs wrapped as subprocesses.

Domain-neutral by construction: a backend knows how to run a prompt and report
what happened, and nothing about markets or candidates.

INVARIANT #7: one backend per run, recorded in RunState.backend. The `name`
carries the CLI's reported version so a run is reproducible against the tool
that produced it -- "claude" alone is not a pinned backend.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SubprocessResult:
    """Implements the BackendResult protocol in core.interfaces."""

    output: str
    exit_code: int
    files_written: Sequence[str]
    elapsed_s: float = 0.0
    timed_out: bool = False
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def _snapshot(root: Path) -> dict[str, tuple[float, int]]:
    """mtime and size of every file under root, for before/after comparison."""
    out: dict[str, tuple[float, int]] = {}
    for p in root.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts:
            try:
                st = p.stat()
            except OSError:
                continue
            out[str(p)] = (st.st_mtime, st.st_size)
    return out


@dataclass
class SubprocessBackend:
    """A coding-agent CLI invoked as `argv + [prompt]`.

    `files_written` is derived by diffing the workdir rather than by parsing the
    agent's narration. Agents describe what they did in prose, inconsistently
    and sometimes inaccurately; the filesystem does not.
    """

    name: str
    argv: Sequence[str]
    env: dict[str, str] = field(default_factory=dict)

    def run(self, prompt: str, workdir: str, timeout_s: int) -> SubprocessResult:
        root = Path(workdir)
        root.mkdir(parents=True, exist_ok=True)
        before = _snapshot(root)

        proc_env = {**os.environ, **self.env}
        started = time.monotonic()
        try:
            cp = subprocess.run(
                [*self.argv, prompt],
                cwd=root,
                env=proc_env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
            out, err, code, timed_out = cp.stdout, cp.stderr, cp.returncode, False
        except subprocess.TimeoutExpired as exc:
            # A hung agent is a normal outcome, not an exception the caller
            # should have to handle. Report it and let them count it.
            out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            code, timed_out = -1, True
        except FileNotFoundError:
            return SubprocessResult(
                output="", exit_code=127, files_written=[],
                elapsed_s=time.monotonic() - started,
                stderr=f"backend executable not found: {self.argv[0]!r}",
            )

        after = _snapshot(root)
        written = sorted(k for k, v in after.items() if before.get(k) != v)
        return SubprocessResult(
            output=out, exit_code=code, files_written=written,
            elapsed_s=time.monotonic() - started, timed_out=timed_out, stderr=err,
        )


def _version(executable: str) -> str:
    try:
        cp = subprocess.run([executable, "--version"], capture_output=True,
                            text=True, timeout=30, check=False)
        return cp.stdout.strip().splitlines()[0] if cp.stdout.strip() else "unknown"
    except (OSError, subprocess.SubprocessError, IndexError):
        return "unknown"


def claude_backend(model: str | None = None, extra: Sequence[str] = ()) -> SubprocessBackend:
    """`claude -p`, non-interactive.

    --permission-mode acceptEdits lets it write the candidate without prompting;
    there is no human at the keyboard during a generation run.
    """
    exe = shutil.which("claude") or "claude"
    argv = [exe, "-p", "--permission-mode", "acceptEdits"]
    if model:
        argv += ["--model", model]
    argv += list(extra)
    return SubprocessBackend(name=f"claude:{model or 'default'}:{_version(exe)}", argv=argv)


def grok_backend(extra: Sequence[str] = ()) -> SubprocessBackend:
    exe = shutil.which("grok") or "grok"
    return SubprocessBackend(name=f"grok:{_version(exe)}", argv=[exe, "-p", *extra])


BACKENDS = {"claude": claude_backend, "grok": grok_backend}
