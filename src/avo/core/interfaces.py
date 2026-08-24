"""The adapter boundary.

RULE: core/ never imports from experiments/. If core needs something
domain-specific, this protocol is wrong — widen the protocol, don't special-case.
An `if experiment == "kalshi-quant"` anywhere in core is a design failure.

An Experiment is four methods. Everything above that line is the framework.
"""
from __future__ import annotations

from typing import Any, Protocol, Sequence

from avo.core.types import Candidate, Score


class Experiment(Protocol):
    """Per-domain plumbing. Implement this to add a new experiment."""

    name: str

    def load_candidate(self, candidate: Candidate) -> Any:
        """Import and validate a candidate module. Raise on contract violation."""
        ...

    def score(self, candidate: Candidate, loaded: Any) -> Score:
        """Evaluate. MUST respect avo.core.holdout for anything time-based."""
        ...

    def seed_candidates(self) -> Sequence[Candidate]:
        """Controls / starting points. Never empty — the agent needs valid state."""
        ...

    def variation_prompt(self, parent: Candidate, siblings: Sequence[Score]) -> str:
        """Build the prompt that asks the backend for one new candidate."""
        ...


class Backend(Protocol):
    """A coding-agent CLI wrapped as a subprocess.

    Implementations: claude -p, codex exec, grok -p. Pin one per run and record
    it in RunState.backend. Mixing backends mid-run destroys comparability.
    """

    name: str

    def run(self, prompt: str, workdir: str, timeout_s: int) -> "BackendResult": ...


class BackendResult(Protocol):
    output: str
    exit_code: int
    files_written: Sequence[str]
