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
from pathlib import Path

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
    default: str = "VERDICT: NONE\nEVIDENCE: none found"
    answers: dict[str, str] = field(default_factory=dict)
    calls: int = 0

    def research(self, query: str) -> ResearchResult:
        self.calls += 1
        return ResearchResult(query, self.answers.get(query, self.default), 0.0)


# launchd gives a job PATH=/usr/bin:/bin:/usr/sbin:/sbin and nothing else, so
# shutil.which("claude") returns None there and a bare "claude" fails to exec.
# Measured 2026-09-10: every research call from the launchd runner failed this
# way while the identical call from a shell succeeded. Same lesson as the
# absolute `uv` path in scripts/launchd/*.plist -- resolve the binary, never
# assume the environment. Raise loudly rather than exec a name that is not
# there: a researcher that cannot run must not look like one that found
# nothing.
_CLAUDE_FALLBACKS = (
    Path.home() / ".local" / "bin" / "claude",
    Path("/opt/homebrew/bin/claude"),
    Path("/usr/local/bin/claude"),
)


def claude_exe() -> str:
    found = shutil.which("claude")
    if found:
        return found
    for p in _CLAUDE_FALLBACKS:
        if p.exists():
            return str(p)
    raise FileNotFoundError(
        "claude CLI not found on PATH or in " +
        ", ".join(str(p) for p in _CLAUDE_FALLBACKS) +
        "; a launchd job gets a minimal PATH, so set CLAUDE_EXE or add the path")


# The research protocol is versioned and recorded in the researcher's name,
# because it is part of the experimental setup exactly as the model is: answers
# produced under different instructions are not comparable.
#
# v1 asked for "findings, or exactly NOTHING FOUND" and got prose. Measured
# 2026-09-10 on the first eight live calls: the researcher answered a query
# about injuries with "No sourced reports of injury, scratch, or postponement
# affecting the active roster today" -- a NEGATION that restates the query's
# own terms. A candidate matching substrings read that as a positive and faded
# the favourite. Three of four fades were wrong that way. No keyword list fixes
# it; free prose can always negate.
#
# v2 demands a verdict token the candidate can parse. A negation, an
# unconfirmed report, a routine preview and silence all collapse to NONE.
PROMPT_VERSION = "v2"

PROMPT = (
    "You are a research assistant for a forecaster. Search the web and answer "
    "the QUERY below.\n\n"
    "Reply in EXACTLY this form, VERDICT first, nothing before it:\n\n"
    "VERDICT: NONE\n"
    "EVIDENCE: <at most 120 words, each claim dated and sourced>\n\n"
    "or\n\n"
    "VERDICT: <the name of the side whose chances are REDUCED>\n"
    "EVIDENCE: <at most 120 words, each claim dated and sourced>\n\n"
    "VERDICT must be the single token NONE unless you found a SPECIFIC, DATED "
    "report that changes the expected outcome of THIS event. All of these are "
    "NONE: no news found; a report you could not confirm; a routine preview or "
    "lineup listing; an injury to someone not involved in this event; a "
    "historical or background note; anything you are inferring rather than "
    "reading. Never restate the query's own words as though they were "
    "findings. When in doubt, NONE.\n\n"
    "No advice, no probability, no hedging outside EVIDENCE.\n\n"
    "QUERY: {query}"
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
    timeout_s: int = 180
    # 6 was too tight. Measured 2026-09-10: a well-covered MLB game answered in
    # 58s well inside 6 turns, but an obscure CS2 esports fixture burned them
    # all searching and returned exit 1 with the body "Error: Reached max turns
    # (6)". That is indistinguishable from a real failure and the runner
    # correctly deferred it -- but the query was answerable, just harder. The
    # hard queries are exactly the ones where the market is least efficient, so
    # a ceiling that only clears easy ones selects against the edge.
    max_turns: int = 16
    name: str = ""

    def __post_init__(self) -> None:
        if not self.model:
            # An unpinned researcher follows the CLI's default model, which is
            # a user setting that can change under the experiment. On
            # 2026-09-10 that default was Fable, its credits were exhausted,
            # and every research call returned the credit notice AS ITS TEXT --
            # which a keyword-matching candidate reads as "no news". INVARIANT
            # #7 applies to the researcher exactly as to the generating
            # backend: pin it, record it, never let it drift.
            raise ValueError(
                "ClaudeResearcher needs an explicit model (e.g. claude-sonnet-5); "
                "the CLI default is a user setting and would break comparability")
        exe = claude_exe()
        try:
            v = subprocess.run([exe, "--version"], capture_output=True, text=True,
                               timeout=20, check=False).stdout.strip().split()[0]
        except (OSError, subprocess.SubprocessError, IndexError):
            v = "unknown"
        self.name = f"claude:{self.model}:{v}:{PROMPT_VERSION}"

    def research(self, query: str) -> ResearchResult:
        exe = claude_exe()
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


DEFAULT_RESEARCH_MODEL = "claude-sonnet-5"


def make(kind: str, model: str | None = None) -> NullResearcher | ClaudeResearcher:
    if kind == "claude":
        return ClaudeResearcher(model=model or DEFAULT_RESEARCH_MODEL)
    if kind == "null":
        return NullResearcher()
    raise KeyError(f"unknown researcher {kind!r}; use claude or null")
