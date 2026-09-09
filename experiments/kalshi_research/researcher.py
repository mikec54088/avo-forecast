"""Research backends: what actually answers `context.research(query)`.

One live implementation and two that never touch the network. Tests and the
validation probe use the stub; controls that never call research pay nothing
regardless of which is wired in.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field

from experiments.kalshi_research.types import ResearchResult


@dataclass
class NullResearcher:
    """Answers nothing, costs nothing. For runs whose candidates never
    research, and as the safe default when no backend is configured."""

    name: str = "null"

    def research(self, query: str) -> ResearchResult:
        return ResearchResult(query, "", 0.0, calls=0, error="null researcher")


@dataclass
class StubResearcher:
    """Canned answers, call-counted. Deterministic by construction so the
    validation probe can check a candidate is deterministic GIVEN its inputs."""

    name: str = "stub"
    default: str = "NOTHING FOUND"
    answers: dict[str, str] = field(default_factory=dict)
    calls: int = 0

    def research(self, query: str) -> ResearchResult:
        self.calls += 1
        return ResearchResult(query, self.answers.get(query, self.default), 0.0)


PROMPT = (
    "You are a research assistant for a forecaster. Search the web for the "
    "following and reply with AT MOST 200 words of dated, sourced, factual "
    "findings relevant to predicting the outcome. No advice, no probability. "
    "If nothing relevant exists, reply exactly: NOTHING FOUND.\n\nQUERY: {query}"
)


@dataclass
class ClaudeResearcher:
    """`claude -p` with web tools allowed, one call per query.

    Each call is a model invocation with search, so the cost of this experiment
    is measured in these. The name carries the model and CLI version so a run is
    reproducible against the tool that produced it (INVARIANT #7 applies to the
    researcher exactly as it does to the generating backend).
    """

    model: str | None = None
    timeout_s: int = 120
    max_turns: int = 6
    name: str = ""

    def __post_init__(self) -> None:
        exe = shutil.which("claude") or "claude"
        try:
            v = subprocess.run([exe, "--version"], capture_output=True, text=True,
                               timeout=20, check=False).stdout.strip().split()[0]
        except (OSError, subprocess.SubprocessError, IndexError):
            v = "unknown"
        self.name = f"claude:{self.model or 'default'}:{v}"

    def research(self, query: str) -> ResearchResult:
        exe = shutil.which("claude") or "claude"
        argv = [exe, "-p", "--output-format", "text",
                "--allowedTools", "WebSearch,WebFetch",
                "--max-turns", str(self.max_turns)]
        if self.model:
            argv += ["--model", self.model]
        argv.append(PROMPT.format(query=query))
        t0 = time.monotonic()
        try:
            cp = subprocess.run(argv, capture_output=True, text=True,
                                timeout=self.timeout_s, check=False)
            text, err = cp.stdout.strip(), ("" if cp.returncode == 0 else
                                            f"exit {cp.returncode}: {cp.stderr.strip()[-200:]}")
        except subprocess.TimeoutExpired:
            text, err = "", f"timed out after {self.timeout_s}s"
        except OSError as exc:
            text, err = "", repr(exc)
        return ResearchResult(query, text, time.monotonic() - t0, error=err)


def make(kind: str, model: str | None = None) -> NullResearcher | ClaudeResearcher:
    if kind == "claude":
        return ClaudeResearcher(model=model)
    if kind == "null":
        return NullResearcher()
    raise KeyError(f"unknown researcher {kind!r}; use claude or null")
