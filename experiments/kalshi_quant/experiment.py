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
from avo.core.selection import is_confirmation_group
from avo.core.types import Candidate, Score
from experiments.kalshi_quant.observations import (
    ENTRY_POLICY,
    Entry,
    SeriesHistory,
    load_entries,
)
from experiments.kalshi_quant.scoring import (
    Observation,
    bootstrap_ci,
    bootstrap_ci_clustered,
    simulate_fill,
    skill_score,
)

CONTRACT = "forecast(market: MarketSnapshot, context: ForecastContext) -> float"


# The P&L gate. ROADMAP Phase 2 calls for a "pessimistic fill model for the
# secondary P&L gate"; this is the gate half. It does NOT touch fitness --
# INVARIANT #2 keeps Brier skill as `primary` and selection still sorts on that
# alone. The gate answers a different question: is this skill worth money?
#
# It exists because the two demonstrably disagree. On 2026-08-31,
# baseline_sharpened scored +0.0286 skill (CI excluding zero) while returning
# -0.0020 per contract. An agent optimising Brier finds that kind of candidate
# first, because correcting an estimator is easier than forecasting.
#
# A candidate must clear the spread, not merely beat the midpoint: the cost of
# crossing plus fees is ~2 probability points even on the tightest books, while
# every bias measured so far is 1-3 points.
PNL_GATE_MIN_FILLS = 200


def passes_pnl_gate(score: Score) -> tuple[bool, str]:
    """Would this candidate have made money, allowing for noise?

    Returns (passed, reason). Advisory: nothing in this file drops a candidate
    for failing, because a failing candidate is still evidence. Callers -- and
    core/selection.py when it exists -- decide what to do with the verdict.

    Requires the lower bound of the series-clustered 95% interval to exceed
    zero. Two deliberate choices:

    - Clustered, not i.i.d. Observations are dominated by a few series (MLB
      prop families especially), and on this data the two disagree about
      whether a band is profitable.
    - Lower bound, not the mean. A mean above zero on 20,000 correlated
      observations is weak evidence; requiring the interval to clear zero is
      the difference between "made money" and "did not lose money".

    A candidate with too few fills is not judged either way -- PNL_GATE_MIN_FILLS
    guards against a lucky handful of trades reading as an edge.
    """
    n = int(score.secondary.get("pnl_n_fills", 0))
    if n < PNL_GATE_MIN_FILLS:
        if n == 0:
            return False, "no position taken (never disagrees with the market)"
        return False, f"too few fills to judge ({n} < {PNL_GATE_MIN_FILLS})"

    mean = score.secondary.get("pnl_per_contract", float("nan"))
    se = score.secondary.get("pnl_se_clustered", float("nan"))
    if mean != mean or se != se:
        return False, "P&L not measurable"

    lo = mean - 1.96 * se
    if lo > 0:
        return True, f"profitable: {mean:+.4f}/contract, 95% CI lower bound {lo:+.4f}"
    if mean + 1.96 * se < 0:
        return False, f"loses money: {mean:+.4f}/contract"
    return False, f"not distinguishable from zero: {mean:+.4f}/contract, CI spans 0"


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
        subset: str = "all",
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

        # `subset` splits by SERIES for selection honesty, not by market. The
        # failure guarded against is a winner that only works on whichever
        # series dominated the sample, and Kalshi's top 20 series are over half
        # of all observations -- a random market-level split would leave the
        # same series on both sides and test nothing.
        if subset != "all":
            want_conf = subset == "confirmation"
            entries = [e for e in entries
                       if is_confirmation_group(e.market.series_ticker) == want_conf]
            if not entries:
                return Score(candidate.candidate_id, float("nan"),
                             (float("nan"), float("nan")), 0,
                             notes=f"no observations in the {subset} subset")

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
        # primary_ci is the SERIES-CLUSTERED interval: observations are not
        # independent, and resampling them individually reported intervals 2.3x
        # narrower than the data supports (40,726 observations, 571 series,
        # top 20 series 54.8% of the set). The i.i.d. interval is kept as a
        # diagnostic -- the ratio between them says whether an edge is
        # broad-based or rests on a handful of series.
        series = [e.market.series_ticker for e in scored]
        lo, hi = bootstrap_ci_clustered(obs, series)
        ilo, ihi = bootstrap_ci(obs)

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
                "skill_ci_iid_lo": ilo,
                "skill_ci_iid_hi": ihi,
                "skill_ci_width_ratio": (
                    (hi - lo) / (ihi - ilo) if (ihi - ilo) > 0 else float("nan")
                ),
                **trade,
            },
            notes=(
                f"entry policy: {ENTRY_POLICY}; primary_ci is series-clustered; "
                f"subset={subset}; {errors} errors"
            ),
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
                    meta={"role": m.get("role", "candidate")},
                )
            )
        return out

    def validation_probe(self) -> str:
        """Checks a generated candidate against the kalshi-quant contract.

        Runs the candidate over ~200 REAL markets sampled from captured
        snapshots (tests/fixtures/probe_markets.json, built by
        scripts/build_probe_fixture.py), not synthetic ones.

        That distinction is the whole point. Synthetic probes sweep one field
        at a time from a fixed base, so field COMBINATIONS never occur, and a
        candidate whose gate needs two conditions together is rejected for
        "never deviating" when it was simply never triggered. On 2026-09-01
        that rejected four legitimate generated candidates and three committed
        hand-written ones. Worse than the waste: the bias is not random. It
        rejects narrow, conditional strategies and passes blunt always-act
        ones -- a selection pressure toward crude candidates, baked into the
        harness. Real markets carry realistic combinations for free.

        The candidate is also run against the real MarketSnapshot dataclass
        rather than a stub, so a probe that drifts from the type it imitates
        cannot pass something the scorer would reject.

        Each check corresponds to a way a candidate has to fail before it can
        be scored:

          returns a float          - a str or None crashes skill_score
          inside [0, 1]            - the fixture spans 0.0010 to 0.9990, so an
                                     additive shift that forgets to clip is
                                     caught at the boundary rather than in a run
          deterministic            - a candidate using randomness cannot be
                                     rescored or compared across generations
          terminates               - enforced by the subprocess timeout around
                                     this probe, not by the probe itself
          deviates somewhere       - a candidate that always returns
                                     implied_prob is baseline_market renamed:
                                     it takes no position and scores exactly 0

        It does NOT check whether the candidate is any good. That is what the
        scorer is for, and a plausible candidate that scores badly is a result,
        not a defect.
        """
        return """    import json
    from datetime import datetime
    from pathlib import Path
    from experiments.kalshi_quant.types import (
        ForecastContext, MarketSnapshot, PricePoint, Resolution,
    )

    _fx = json.loads(Path("tests/fixtures/probe_markets.json").read_text())
    _hist = {
        s: [Resolution(r["ticker"], datetime.fromisoformat(r["resolved_at"]),
                       r["outcome"])
            for r in rows]
        for s, rows in _fx["history"].items()
    }

    def _mk(_row):
        return MarketSnapshot(
            ticker=_row["ticker"], event_ticker=_row["event_ticker"],
            series_ticker=_row["series_ticker"], title=_row["title"],
            observed_at=datetime.fromisoformat(_row["observed_at"]),
            close_time=datetime.fromisoformat(_row["close_time"]),
            yes_bid=_row["yes_bid"], yes_ask=_row["yes_ask"],
            last_price=_row["last_price"], volume=_row["volume"],
            open_interest=_row["open_interest"],
            yes_bid_size=_row["yes_bid_size"], yes_ask_size=_row["yes_ask_size"],
            liquidity=_row["liquidity"], status=_row["status"],
            price_level_structure=_row["price_level_structure"],
            is_mve=_row["is_mve"],
        )

    _probes = []
    for _row in _fx["markets"]:
        _m = _mk(_row)
        _s, _t = _row["series_ticker"], _row["ticker"]
        _ctx = ForecastContext(
            now=_m.observed_at,
            series_history={_s: _hist[_s]} if _s in _hist else {},
            price_history=[
                PricePoint(datetime.fromisoformat(_p["observed_at"]),
                           _p["yes_bid"], _p["yes_ask"], _p["volume"],
                           _p["open_interest"])
                for _p in _fx.get("price_history", {}).get(_t, [])
            ],
            siblings=[_mk(_r) for _r in _fx.get("siblings", {}).get(_t, [])],
        )
        _probes.append((_m, _ctx))

    _outs = []
    for _m, _c in _probes:
        try:
            _v = mod.forecast(_m, _c)
        except BaseException as _e:
            problems.append("forecast() raised on %s (mid %.4f): %r"
                            % (_m.ticker, _m.implied_prob, _e))
            break
        if isinstance(_v, bool) or not isinstance(_v, (int, float)):
            problems.append("forecast() returned %s, not a float" % type(_v).__name__)
            break
        _v = float(_v)
        if _v != _v:
            problems.append("forecast() returned NaN on %s" % _m.ticker)
            break
        if not (0.0 <= _v <= 1.0):
            problems.append("forecast() returned %.4f on %s (mid %.4f), outside [0,1]"
                            % (_v, _m.ticker, _m.implied_prob))
            break
        _outs.append(_v)

    if not problems:
        _again = [float(mod.forecast(_m, _c)) for _m, _c in _probes]
        if _again != _outs:
            problems.append("forecast() is not deterministic across identical calls")
        else:
            _moved = sum(1 for a, (m, _) in zip(_outs, _probes)
                         if abs(a - m.implied_prob) > 1e-12)
            if _moved == 0:
                problems.append("forecast() never deviates from implied_prob across "
                                "%d real markets (equivalent to baseline_market; "
                                "takes no position)" % len(_probes))
            else:
                info["deviates_on"] = _moved
                info["probe_markets"] = len(_probes)
"""

    def variation_prompt(self, parent: Candidate, siblings: Sequence[Score]) -> str:
        """What to tell the agent, given what has already been tried.

        The whole value is the failure list. A loop that cannot name its own
        dead ends rediscovers them: the generic prompt used on 2026-09-01
        produced 25 candidates that collapsed to about 7 ideas, four of them
        near-identical log-odds midpoints.

        The cost hurdle is stated explicitly because it is the constraint every
        failure so far has run into. 26 candidates were often right about
        direction and still lost money, because crossing the spread costs about
        2 probability points while every measurable bias is 1-3.
        """
        lines = [
            "Write ONE new forecasting candidate for the kalshi_quant "
            "experiment in this repository.",
            "",
            "Read experiments/kalshi_quant/types.py for the contract, and "
            "experiments/kalshi_quant/candidates/favourite_longshot.py for the "
            "house style.",
            "",
            "THE GOVERNING CONSTRAINT. Crossing the spread plus fees costs about "
            "2 probability points even on the tightest books, while every bias "
            "measured so far is 1-3 points. Beating the midpoint is not enough: "
            "a candidate must beat it by MORE THAN 2 POINTS on markets it can "
            "identify in advance. Most failures below were right about direction "
            "and still lost money.",
            "",
        ]
        if parent is not None:
            lines += [
                f"PARENT: {parent.candidate_id} (module {parent.module_path})",
                f"  rationale: {parent.rationale}",
                "Read it. Your candidate should differ in MECHANISM, not just in "
                "constants -- retuning a threshold is not a new hypothesis.",
                "",
            ]

        ranked = sorted((s for s in siblings if s.primary == s.primary),
                        key=lambda s: -s.primary)
        if ranked:
            lines.append("ALREADY TRIED AND SCORED. Do not resubmit a variation "
                         "of these:")
            for s in ranked[:24]:
                pnl = s.secondary.get("pnl_per_contract", float("nan"))
                lo, hi = s.primary_ci
                money = "no profit" if pnl != pnl or pnl <= 0 else f"P&L {pnl:+.4f}"
                lines.append(f"  {s.candidate_id:<26} skill {s.primary:+.4f} "
                             f"[{lo:+.4f},{hi:+.4f}]  n={s.n_observations:,}  {money}")
            lines.append("")

        lines += [
            "UNEXPLORED. ForecastContext carries two fields almost nothing uses:",
            "  price_history  this market's own earlier quotes, oldest first",
            "  siblings       other markets in the same EVENT, quoted at about "
            "the same time",
            "",
            "Prefer a candidate that ABSTAINS often and acts strongly on a "
            "well-defined subset over one that nudges every market slightly -- "
            "the failures above nudged everything and paid the spread for it.",
            "",
            "Give it a docstring stating the edge, the evidence for it, and what "
            "would falsify it. Be honest about weak evidence; a candidate that "
            "overstates its case is worse than one admitting it is a guess.",
            "",
            "Do not modify, create, or delete ANY file outside "
            "experiments/kalshi_quant/candidates/.",
        ]
        return "\n".join(lines)


def build() -> KalshiQuantExperiment:
    return KalshiQuantExperiment()
