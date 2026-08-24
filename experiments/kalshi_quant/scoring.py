"""Fitness for kalshi-quant: Brier skill vs the market's own implied probability.

INVARIANT #2: beating 50/50 is meaningless. The baseline is the market price.
Shared verbatim by kalshi-research so the two experiments are comparable.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from experiments.kalshi_quant.types import MarketSnapshot

# TODO(claude-code): replace with Kalshi's current published fee schedule
# (a function of price and contract count, not a flat per-contract charge).
FEE_PER_CONTRACT_CENTS = 1.0


def brier(p: float, outcome: int) -> float:
    return (p - outcome) ** 2


@dataclass(frozen=True)
class Observation:
    forecast: float
    market_prob: float
    outcome: int


def skill_score(obs: list[Observation]) -> tuple[float, float, float]:
    """(candidate_brier, market_brier, skill). skill = 1 - cb/mb."""
    if not obs:
        return (float("nan"),) * 3
    cb = sum(brier(o.forecast, o.outcome) for o in obs) / len(obs)
    mb = sum(brier(o.market_prob, o.outcome) for o in obs) / len(obs)
    return cb, mb, (float("nan") if mb == 0 else 1.0 - cb / mb)


def bootstrap_ci(
    obs: list[Observation], n: int = 2000, alpha: float = 0.05, seed: int = 0
) -> tuple[float, float]:
    """Always report this. With a few hundred markets the interval is wide, and
    a bare point estimate is how you fool yourself into seeing a discovery."""
    if len(obs) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    draws = [skill_score([obs[rng.randrange(len(obs))] for _ in obs])[2] for _ in range(n)]
    draws = sorted(d for d in draws if d == d)
    if not draws:
        return (float("nan"), float("nan"))
    return draws[int(alpha / 2 * len(draws))], draws[int((1 - alpha / 2) * len(draws)) - 1]


@dataclass(frozen=True)
class Fill:
    ticker: str
    side: str
    contracts: int
    price_cents: float
    filled: bool
    reason: str = ""


def simulate_fill(
    market: MarketSnapshot,
    side: str,
    desired_contracts: int,
    max_depth_fraction: float = 0.25,
    max_spread_cents: int = 8,
) -> Fill:
    """INVARIANT #4: cross the spread, cap at visible depth, add fees.

    Never fill at midpoint. An agent exploring 500 directions will reliably find
    strategies that only work against an optimistic simulator.
    """
    if market.spread_cents > max_spread_cents:
        return Fill(market.ticker, side, 0, 0.0, False, "spread too wide")

    if side == "yes":
        price, depth = float(market.yes_ask), market.yes_ask_size
    else:
        price, depth = float(100 - market.yes_bid), market.yes_bid_size

    if depth is not None:
        cap = int(depth * max_depth_fraction)
        if cap < 1:
            return Fill(market.ticker, side, 0, 0.0, False, "insufficient depth")
        desired_contracts = min(desired_contracts, cap)

    return Fill(market.ticker, side, desired_contracts, price + FEE_PER_CONTRACT_CENTS, True)
