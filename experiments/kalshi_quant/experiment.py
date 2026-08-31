"""kalshi-quant implements core.interfaces.Experiment.

This file is the ONLY thing core knows about. Everything domain-specific lives
below it.
"""
from __future__ import annotations

import importlib
import pkgutil
from datetime import datetime
from typing import Any, Sequence

from avo.core.holdout import assert_clean, eligible
from avo.core.types import Candidate, Score
from experiments.kalshi_quant.observations import Entry, SeriesHistory, load_entries
from experiments.kalshi_quant.scoring import (
    Observation,
    bootstrap_ci,
    simulate_fill,
    skill_score,
)

CONTRACT = "forecast(market: MarketSnapshot, context: ForecastContext) -> float"


def _pct(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))]


def paper_trade(
    entries: Sequence[Entry], forecasts: Sequence[float], edge: float = 0.0
) -> dict[str, float]:
    """Trade each disagreement with the market and report realised P&L.

    Reported, never selected on. INVARIANT #2 keeps Brier skill as the fitness;
    this exists because the two can point in opposite directions and only one
    of them is money. Measured 2026-08-31: baseline_sharpened scored +0.0286
    skill and returned -0.0019 per contract, series-clustered CI spanning zero.
    The ~2-point edge it finds is smaller than the ~2-point cost of crossing
    the spread and paying the fee.

    `edge` requires the forecast to differ from the market by at least that
    much before trading, which is the shape a real strategy takes.

    The standard error is clustered by series. Observations are NOT independent
    -- a handful of series are a large share of all resolutions, and MLB prop
    families dominate -- so an i.i.d. error would understate the interval the
    same way bootstrap_ci does on skill. On this data the naive and clustered
    intervals disagree about whether a band is profitable.
    """
    pnl: list[float] = []
    by_series: dict[str, list[float]] = {}
    for e, p in zip(entries, forecasts, strict=True):
        mid = e.market.implied_prob
        if abs(p - mid) <= edge:
            continue
        side = "yes" if p > mid else "no"
        fill = simulate_fill(e.market, side, 100)
        if not fill.filled:
            continue
        price = fill.price_cents / 100.0
        won = (e.outcome == 1) if side == "yes" else (e.outcome == 0)
        r = (1.0 - price) if won else -price
        pnl.append(r)
        by_series.setdefault(e.market.series_ticker, []).append(r)

    if not pnl:
        return {"pnl_per_contract": float("nan"), "pnl_n_fills": 0.0,
                "pnl_fill_rate": 0.0, "pnl_se_clustered": float("nan")}

    mean = sum(pnl) / len(pnl)
    k = len(by_series)
    if k > 1:
        sm = [sum(v) / len(v) for v in by_series.values()]
        m = sum(sm) / k
        se = (sum((x - m) ** 2 for x in sm) / (k - 1) / k) ** 0.5
    else:
        se = float("nan")
    return {
        "pnl_per_contract": mean,
        "pnl_n_fills": float(len(pnl)),
        "pnl_fill_rate": len(pnl) / len(entries),
        "pnl_se_clustered": se,
    }


class KalshiQuantExperiment:
    name = "kalshi_quant"

    def load_candidate(self, candidate: Candidate) -> Any:
        mod = importlib.import_module(candidate.module_path)
        if not hasattr(mod, "forecast"):
            raise TypeError(f"{candidate.module_path} missing forecast(); expected {CONTRACT}")
        if not hasattr(mod, "MANIFEST"):
            raise TypeError(f"{candidate.module_path} missing MANIFEST dict")
        return mod

    def score(
        self,
        candidate: Candidate,
        loaded: Any,
        entries: Sequence[Entry] | None = None,
        history: SeriesHistory | None = None,
    ) -> Score:
        """Brier skill vs the market's implied probability (INVARIANT #2).

        `entries` and `history` are injectable so a caller scoring many
        candidates loads the observation set once. Both default to reading the
        captured Parquet.
        """
        if entries is None:
            entries = load_entries()
        if history is None:
            history = SeriesHistory(list(entries))

        # INVARIANT #1: only truth that resolved strictly after this candidate
        # existed. assert_clean afterwards is not redundant -- it is the
        # tripwire that catches a future change to eligible().
        keep = eligible(candidate, list(entries), lambda e: e.resolved_at)
        assert_clean(candidate, keep, lambda e: e.resolved_at)
        if not keep:
            return Score(
                candidate_id=candidate.candidate_id,
                primary=float("nan"),
                primary_ci=(float("nan"), float("nan")),
                n_observations=0,
                notes="no observations resolved after created_at",
            )

        obs: list[Observation] = []
        scored: list[Entry] = []
        forecasts: list[float] = []
        errors = 0
        for e in keep:
            try:
                p = float(loaded.forecast(e.market, history.context_for(e)))
            except Exception:  # noqa: BLE001 - a broken candidate must not kill the run
                errors += 1
                continue
            # NaN fails this comparison too, so it is caught here as well.
            if not (0.0 <= p <= 1.0):
                errors += 1
                continue
            obs.append(Observation(p, e.market.implied_prob, e.outcome))
            scored.append(e)
            forecasts.append(p)

        if not obs:
            return Score(
                candidate_id=candidate.candidate_id,
                primary=float("nan"),
                primary_ci=(float("nan"), float("nan")),
                n_observations=0,
                notes=f"no usable forecasts ({errors} errors)",
            )

        cand_brier, market_brier, skill = skill_score(obs)
        lo, hi = bootstrap_ci(obs)

        # Secondary, never selected on (INVARIANT #2). Selection sorts on
        # `primary` alone; these exist so a skill number can be read honestly.
        stale = sorted(e.staleness_minutes for e in scored)
        trade = paper_trade(scored, forecasts)
        return Score(
            candidate_id=candidate.candidate_id,
            primary=skill,
            primary_ci=(lo, hi),
            n_observations=len(obs),
            secondary={
                "candidate_brier": cand_brier,
                "market_brier": market_brier,
                "forecast_errors": float(errors),
                "staleness_median_min": _pct(stale, 0.50),
                "staleness_p90_min": _pct(stale, 0.90),
                **trade,
            },
            notes=f"entry policy: last snapshot before resolution; {errors} errors",
        )

    def seed_candidates(self) -> Sequence[Candidate]:
        pkg = importlib.import_module("experiments.kalshi_quant.candidates")
        out: list[Candidate] = []
        for info in pkgutil.iter_modules(pkg.__path__):
            mod = importlib.import_module(f"{pkg.__name__}.{info.name}")
            m = mod.MANIFEST
            out.append(
                Candidate(
                    candidate_id=m["candidate_id"],
                    experiment=self.name,
                    generation=m.get("generation", 0),
                    parent_id=m.get("parent_id"),
                    created_at=datetime.fromisoformat(m["created_at"]),
                    module_path=f"{pkg.__name__}.{info.name}",
                    rationale=m.get("rationale", ""),
                )
            )
        return out

    def variation_prompt(self, parent: Candidate, siblings: Sequence[Score]) -> str:
        raise NotImplementedError("Phase 4.")


def build() -> KalshiQuantExperiment:
    return KalshiQuantExperiment()
