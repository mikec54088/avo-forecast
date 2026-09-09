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

    def scoring_inputs(self) -> tuple[Any, Any]:
        """(entries, history) to pass to score() for a whole ranking pass.

        Whatever the experiment's observations are: replayed snapshots for
        kalshi_quant, a forward forecast log for kalshi_research. Core only
        threads them through; it never looks inside (INVARIANT #3).
        """
        ...

    def seed_candidates(self) -> Sequence[Candidate]:
        """Controls / starting points. Never empty — the agent needs valid state."""
        ...

    def validation_probe(self) -> str:
        """Python source that checks a generated candidate against this
        experiment's contract.

        Runs in a subprocess (see core.validate) with the candidate loaded as
        `mod`. Appends failure descriptions to `problems`; appending nothing
        means the candidate is accepted. Indented to sit inside an `if` block,
        so every line needs four leading spaces.

        Domain-specific by necessity -- only the experiment knows what a valid
        forecast is -- which is why it is a protocol method rather than a
        special case inside core (INVARIANT #3).
        """
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

    def run(self, prompt: str, workdir: str, timeout_s: int) -> BackendResult: ...


class BackendResult(Protocol):
    output: str
    exit_code: int
    files_written: Sequence[str]
