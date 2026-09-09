"""Domain types for kalshi_research.

Reuses kalshi_quant's MarketSnapshot verbatim -- same venue, same book -- and
adds the one thing that makes this a different experiment: a research channel
the candidate may invoke, metered per market. The CONTRACT is

    forecast(market: MarketSnapshot, context: ResearchContext) -> float

which is deliberately the same shape as kalshi_quant's so scoring.py is shared
unchanged (docs/EXPERIMENTS.md). It is a NEW contract for a NEW experiment, not
a change to kalshi_quant's ForecastContext, so G1 for kalshi_quant is untouched.

Why the candidate is metered rather than trusted: research is the entire cost
of this experiment. A candidate that researches every market is a candidate
that cannot be run, and cost is a first-class constraint here in the way
abstention was a first-class constraint in kalshi_quant.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from experiments.kalshi_quant.types import MarketSnapshot, Resolution  # noqa: F401  re-export

DEFAULT_BUDGET = 3   # research calls per market per candidate


@dataclass(frozen=True)
class ResearchResult:
    query: str
    text: str
    elapsed_s: float
    calls: int = 1
    error: str = ""


class Researcher(Protocol):
    """Whatever answers a query. Pluggable so tests and controls never touch
    the network, and so the live one can be swapped or pinned per run."""

    name: str

    def research(self, query: str) -> ResearchResult: ...


class ResearchBudgetExceeded(RuntimeError):
    pass


@dataclass
class ResearchContext:
    """Everything a candidate may see besides the market, plus the channel.

    `research(query)` returns text. It counts. When the budget is spent it
    raises rather than returning empty text, because a candidate that silently
    gets nothing back would look like a candidate that found nothing, and the
    two must not be confused in the log.

    `siblings` are the other legs of the same event from the SAME snapshot the
    market came from, so they are quoted at the same instant and can never
    postdate the entry. No price_history in v1: it needs the full snapshot
    history loaded per pass, which a 15-minute cadence cannot afford yet.
    """

    now: datetime
    researcher: Researcher
    budget: int = DEFAULT_BUDGET
    siblings: list[MarketSnapshot] = field(default_factory=list)
    calls: int = 0
    elapsed_s: float = 0.0
    trail: list[ResearchResult] = field(default_factory=list)

    def research(self, query: str) -> str:
        if self.calls >= self.budget:
            raise ResearchBudgetExceeded(
                f"{self.calls} research calls already made; budget is {self.budget}")
        r = self.researcher.research(query)
        self.calls += r.calls
        self.elapsed_s += r.elapsed_s
        self.trail.append(r)
        return r.text

    @property
    def sibling_sum(self) -> float:
        return sum(s.implied_prob for s in self.siblings if s.has_two_sided_book)
