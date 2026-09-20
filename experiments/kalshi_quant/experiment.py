"""kalshi-quant implements core.interfaces.Experiment.

This file is the ONLY thing core knows about. Everything domain-specific lives
below it.
"""
from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Sequence

from avo.core.holdout import assert_clean, eligible
from avo.core.selection import (
    GATE_FAIL,
    GATE_PASS,
    GATE_UNPROVEN,
    is_confirmation_group,
)
from avo.core.types import Candidate, Score
from experiments.kalshi_quant import digest, entries_store
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


def pnl_verdict(score: Score) -> tuple[int, str]:
    """(GATE_PASS | GATE_UNPROVEN | GATE_FAIL, reason).

    Three states, not two, because "did not prove it made money" and "proved it
    lost money" want opposite handling in selection. G2 (decided 2026-09-08)
    drops only the proven losers from the parent pool: where this gate has power
    its evidence is decisive, and where a candidate is too selective to have a
    verdict yet, excluding it would cut off the only branch of the search that
    has looked promising.
    """
    n = int(score.secondary.get("pnl_n_fills", 0))
    if n < PNL_GATE_MIN_FILLS:
        if n == 0:
            return GATE_UNPROVEN, "no position taken (never disagrees with the market)"
        return GATE_UNPROVEN, f"too few fills to judge ({n} < {PNL_GATE_MIN_FILLS})"

    mean = score.secondary.get("pnl_per_contract", float("nan"))
    se = score.secondary.get("pnl_se_clustered", float("nan"))
    if mean != mean or se != se:
        return GATE_UNPROVEN, "P&L not measurable"

    lo = mean - 1.96 * se
    if lo > 0:
        return GATE_PASS, (f"profitable: {mean:+.4f}/contract, "
                           f"95% CI lower bound {lo:+.4f}")
    if mean + 1.96 * se < 0:
        return GATE_FAIL, f"loses money: {mean:+.4f}/contract"
    return GATE_UNPROVEN, f"not distinguishable from zero: {mean:+.4f}/contract, CI spans 0"


def pnl_strength(score: Score) -> float:
    """How strongly a candidate passes the P&L gate: the LOWER BOUND of its
    series-clustered interval.

    The lower bound rather than the mean, because it is the same quantity the
    gate itself tests -- a candidate earning +0.05 on a wide interval has not
    shown more than one earning +0.04 on a tight one. Used only to order
    candidates that share a gate verdict.
    """
    mean = score.secondary.get("pnl_per_contract", float("nan"))
    se = score.secondary.get("pnl_se_clustered", float("nan"))
    if mean != mean or se != se:
        return float("nan")
    return mean - 1.96 * se


def passes_pnl_gate(score: Score) -> tuple[bool, str]:
    """Would this candidate have made money, allowing for noise?

    Returns (passed, reason). The boolean view of `pnl_verdict`: only GATE_PASS
    counts as passing, so an unproven candidate and a proven loser both read
    False here. Selection needs to tell them apart and uses `pnl_verdict`.

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
    verdict, why = pnl_verdict(score)
    return verdict == GATE_PASS, why


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
    by_series: dict[str, list[float]] = {}
    accumulate_fills(entries, forecasts, by_series, edge)
    return pnl_stats(by_series, n_considered=len(entries))


def accumulate_fills(entries: Sequence[Entry], forecasts: Sequence[float],
                     by_series: dict[str, list[float]], edge: float = 0.0) -> None:
    """Add this batch's realised fills into `by_series`, keyed by series.

    Split out so the streaming scorer can accumulate across chunks without a
    second copy of the fill rule. A duplicate would be free to drift from
    INVARIANT #4, and the whole P&L gate rests on it.
    """
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
        by_series.setdefault(e.market.series_ticker, []).append(
            (1.0 - price) if won else -price)


def pnl_stats(by_series: dict[str, list[float]], n_considered: int) -> dict[str, float]:
    """Mean, fill count and series-clustered error from accumulated fills."""
    pnl = [r for v in by_series.values() for r in v]
    if not pnl:
        return {"pnl_per_contract": float("nan"), "pnl_n_fills": 0.0,
                "pnl_fill_rate": 0.0, "pnl_se_clustered": float("nan"),
                "pnl_n_series": 0.0}

    mean = sum(pnl) / len(pnl)
    k = len(by_series)
    n_considered = max(n_considered, 1)
    if k > 1:
        # Cluster-robust standard error for the POOLED mean above.
        #
        # The previous version took the unweighted mean of per-series means and
        # reported ITS standard error alongside a fill-weighted point estimate.
        # Those describe different quantities and on real data they disagree in
        # sign: measured 2026-09-08, persistent_quote_favourite's pooled mean was
        # +0.0149 while the unweighted mean of its series means was -0.0121.
        # `mean +/- 1.96 * se` was therefore not an interval for either one.
        #
        # It was also badly inflated, because equal weight per series lets a
        # singleton dominate: 43 of that candidate's 92 series held exactly one
        # fill, and a single fill's "series mean" is ~+/-0.5. The old estimator
        # ran 1.6-2.0x wide, which is not conservatism, it is noise. It hid
        # unchurned_favourite, whose interval is [-0.0126,+0.0862] under the old
        # estimator and [+0.0093,+0.0643] under this one.
        #
        # This is the standard Liang-Zeger form for a mean: sum the residuals
        # WITHIN each series, square the series totals, and correct by k/(k-1).
        # Series contribute in proportion to how much they were traded, and
        # correlation inside a series is still fully absorbed -- which was the
        # point of clustering in the first place.
        ss = sum((sum(v) - len(v) * mean) ** 2 for v in by_series.values())
        se = (ss * k / (k - 1)) ** 0.5 / len(pnl)
    else:
        se = float("nan")
    return {
        "pnl_per_contract": mean,
        "pnl_n_fills": float(len(pnl)),
        "pnl_fill_rate": len(pnl) / n_considered,
        "pnl_se_clustered": se,
        # Cluster COUNT, not just the clustered SE. A Liang-Zeger error is only
        # trustworthy with enough clusters; on a handful it is understated, and
        # the lower bound built from it is the number selection now sorts on.
        "pnl_n_series": float(k),
    }


class KalshiQuantExperiment:
    name = "kalshi_quant"

    def __init__(self) -> None:
        self._digest: digest.Digest | None = None

    def load_candidate(self, candidate: Candidate) -> Any:
        mod = importlib.import_module(candidate.module_path)
        if not hasattr(mod, "forecast"):
            raise TypeError(f"{candidate.module_path} missing forecast(); expected {CONTRACT}")
        if not hasattr(mod, "MANIFEST"):
            raise TypeError(f"{candidate.module_path} missing MANIFEST dict")
        return mod

    def scoring_inputs(self) -> tuple[list[Entry], SeriesHistory]:
        """Load the observation set once for a whole ranking pass.

        Also refreshes the dataset digest, because this is the one place that
        already holds the entries and every generating agent needs the same
        summary. See digest.py: computing it per agent cost 36 Bash calls an
        invocation and produced numbers no two candidates could be compared on.
        """
        # Prefer streaming: holding all 157,108 entries costs 2.88 GB of Python
        # objects, streaming them a partition at a time costs 0.74 GB and does
        # not grow with the archive. Verified identical on six candidates and
        # both subsets over the real table.
        sh = entries_store.series_history()
        if sh is not None:
            self._digest = digest.load_or_build()
            if self._digest is None:
                # The digest needs the whole set once; pay it here, not per
                # candidate, and only when it is missing.
                self._digest = digest.load_or_build(entries_store.load())
            return (lambda: entries_store.iter_entries()), sh

        entries = entries_store.load()
        if entries is None:
            # Cold or stale table. Falling back to the raw archive is correct
            # but expensive -- 10.9 GB and 36 minutes against 2.8 GB and 10
            # seconds -- so say which path was taken rather than being quietly
            # slow. entries_store.load() returns None, never [], precisely so
            # this branch cannot be confused with "there are no observations".
            print("  entries table cold or stale; rebuilding from the raw "
                  "archive (run `avo entries build` to materialise it)", flush=True)
            entries = load_entries()
        if not entries:
            raise SystemExit("no observations yet; capture needs to run first")
        self._digest = digest.load_or_build(entries)
        return entries, SeriesHistory(entries)

    def score(
        self,
        candidate: Candidate,
        loaded: Any,
        entries: Sequence[Entry] | None = None,
        history: SeriesHistory | None = None,
        subset: str = "all",
        chunks: Iterable[Sequence[Entry]] | None = None,
    ) -> Score:
        """Brier skill vs the market's implied probability (INVARIANT #2).

        `entries` and `history` are injectable so a caller scoring many
        candidates loads the observation set once. Both default to reading the
        captured Parquet.
        """
        if chunks is None:
            if entries is None:
                entries = load_entries()
            chunks = [entries]
        if history is None:
            history = SeriesHistory(list(entries) if entries is not None else [])

        # `subset` splits by SERIES for selection honesty, not by market. The
        # failure guarded against is a winner that only works on whichever
        # series dominated the sample, and Kalshi's top 20 series are over half
        # of all observations -- a random market-level split would leave the
        # same series on both sides and test nothing.
        want_conf = subset == "confirmation"

        # Accumulated across chunks. These are all the scorer keeps: three
        # floats and a label per observation, plus fills grouped by series.
        # Entry objects never outlive their chunk, so peak memory is the chunk
        # size rather than the dataset -- see entries_store.iter_entries.
        obs: list[Observation] = []
        series: list[str] = []
        stale_vals: list[float] = []
        by_series_pnl: dict[str, list[float]] = {}
        n_considered = 0
        errors = 0
        saw_any = False

        for chunk in chunks:
            batch = chunk
            if subset != "all":
                batch = [e for e in batch
                         if is_confirmation_group(e.market.series_ticker) == want_conf]
            if batch:
                saw_any = True
            # INVARIANT #1: only truth that resolved strictly after this
            # candidate existed. assert_clean afterwards is not redundant -- it
            # is the tripwire that catches a future change to eligible().
            keep = eligible(candidate, list(batch), lambda e: e.resolved_at)
            assert_clean(candidate, keep, lambda e: e.resolved_at)
            if not keep:
                continue
            n_considered += len(keep)

            batch_scored: list[Entry] = []
            batch_forecasts: list[float] = []
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
                series.append(e.market.series_ticker)
                stale_vals.append(e.staleness_minutes)
                batch_scored.append(e)
                batch_forecasts.append(p)
            accumulate_fills(batch_scored, batch_forecasts, by_series_pnl)

        if not saw_any:
            return Score(candidate.candidate_id, float("nan"),
                         (float("nan"), float("nan")), 0,
                         notes=f"no observations in the {subset} subset")
        if not n_considered:
            return Score(
                candidate_id=candidate.candidate_id,
                primary=float("nan"),
                primary_ci=(float("nan"), float("nan")),
                n_observations=0,
                notes="no observations resolved after created_at",
            )

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
        lo, hi = bootstrap_ci_clustered(obs, series)
        ilo, ihi = bootstrap_ci(obs)

        # Secondary, never selected on (INVARIANT #2). Selection sorts on
        # `primary` alone; these exist so a skill number can be read honestly.
        stale = sorted(stale_vals)
        trade = pnl_stats(by_series_pnl, n_considered=n_considered)
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

        Rewritten 2026-09-09 (docs/PLAN-2026-09-09.md, track A). The previous
        prompt steered every generation into the same dead region: its house-
        style exemplar was a pure midpoint transform, its tried-list was sorted
        by skill -- the metric the weekend staleness artifact inflates -- so the
        artifact family led as "best so far", it named two unexplored fields,
        and it asked for abstention with no floor. Result over three
        generations: 7 of 39 candidates read nothing but implied_prob, 12 read
        only the midpoint plus its own history, and generation 3 wrote one idea
        six times, each too selective to ever be judged.

        The agent already reads CLAUDE.md (it runs with the repo as cwd), so the
        learned-facts list reaches it. What did not reach it was WHY each family
        fails. A leaderboard is not a lesson; a mechanism is.
        """
        lines = [
            "Write ONE new forecasting candidate for the kalshi_quant "
            "experiment in this repository.",
            "",
            "Read experiments/kalshi_quant/types.py for the contract, and "
            "experiments/kalshi_quant/candidates/neglected_leg.py for the house "
            "style -- the docstring shape (edge, evidence with series-clustered "
            "intervals, a control with the gate inverted, what would falsify "
            "it, where it is weak), not the idea. Your idea must differ.",
            "",
            "THE GOVERNING CONSTRAINT. Crossing the spread plus fees costs about "
            "2 probability points even on the tightest books, while every bias "
            "measured so far is 1-3 points. A candidate must beat the midpoint "
            "by MORE THAN 2 POINTS on markets it can identify in advance.",
            "",
            "THE MEASURABILITY CONSTRAINT. A candidate is judged on ~200 fills "
            "across many series, and the observation set grows ~5,000 markets a "
            "day. To be judged within a week it must ACT on at least 1% of "
            "markets. Generation 3 abstained on 97-99.9% and produced 16-644 "
            "fills in a week: unfalsifiable, which is worse than wrong. Be "
            "selective, and be selective on a population large enough to test.",
            "",
            "WHY THE PREVIOUS FAMILIES FAILED -- the mechanisms, so you do not "
            "rediscover them:",
            "  * Any function of the midpoint alone (sharpen, logit, shrink, "
            "shoulders). Can only win if the market's own price is miscalibrated "
            "as a function of itself. It is, by 1-3 points, which is below the "
            "cost of trading it. Their Brier skill is REAL but is a staleness "
            "artifact -- a stale entry price has not absorbed the drift toward "
            "the outcome, sharpening recovers that drift, and it concentrates on "
            "weekend sports days (median entry staleness 33 min vs 18). It never "
            "converts to money: the whole family is proven to LOSE 0.5-2.7 cents "
            "per contract at 0.4-0.6 cent resolution. Do not write another.",
            "  * Favourite / longshot corrections gated on PRICE. Right about "
            "direction, 1-3 points of edge, below the hurdle. Nine variants.",
            "  * Favourites gated on a narrow behavioural condition (unmoved, "
            "untraded, unchurned, unclimbed, ground). Positive P&L on selection "
            "series that collapsed on held-out series (+0.0546 -> +0.0010), and "
            "too few fills to judge. Six of eight in generation 3 were this.",
            "  * Momentum / drift extrapolated linearly. Drift's edge is "
            "non-monotone; the linear version returned -0.055/contract.",
            "  * sibling_coherence assumed events are mutually exclusive. Nested "
            "ladders (over 1.5 / 2.5 / 3.5) correctly sum above 1.0.",
            "",
        ]
        if parent is not None:
            lines += [
                f"PARENT: {parent.candidate_id} (module {parent.module_path})",
                f"  rationale: {parent.rationale}",
                "Read it. Differ in MECHANISM -- which INPUTS you read and why "
                "they carry information -- not in constants. Retuning a "
                "threshold is not a new hypothesis.",
                "",
            ]

        # Order by what actually matters: money verdict, then skill within it.
        # Sorted by skill alone the artifact family leads the list and reads as
        # the best work so far. It is the worst.
        scored = [s for s in siblings if s.primary == s.primary]
        if scored:
            def _key(s: Score) -> tuple[int, float]:
                return (pnl_verdict(s)[0], -s.primary)
            ranked = sorted(scored, key=_key)
            label = {GATE_FAIL: "LOSES MONEY (proven)",
                     GATE_UNPROVEN: "unproven",
                     GATE_PASS: "PROFITABLE on selection series"}
            lines.append("ALREADY TRIED. Grouped by money verdict, which is the "
                         "one that counts; skill is shown because it is the "
                         "fitness, but skill without money is the artifact above.")
            for s in ranked[:30]:
                v, _ = pnl_verdict(s)
                pnl = s.secondary.get("pnl_per_contract", float("nan"))
                nf = int(s.secondary.get("pnl_n_fills", 0))
                lines.append(f"  {s.candidate_id:<26} {label[v]:<32} "
                             f"P&L {pnl:+.4f}/ct on {nf:,} fills  "
                             f"skill {s.primary:+.4f}")
            lines.append("")

        lines += [
            "UNEXPLORED INPUTS. Counts are how many of the candidates above "
            "read each one; the dataset summary at the end has their shape:",
            "  * EVENT COHERENCE -- context.siblings, context.sibling_sum. "
            "ZERO candidates have used the aggregate. Mispricing BETWEEN "
            "related contracts is arithmetic rather than prediction. Mind "
            "nested ladders: check exclusivity before assuming legs sum to 1.",
            "  * BOOK DEPTH -- market.yes_bid_size / yes_ask_size. Three "
            "candidates. Depth imbalance, depth against siblings, depth "
            "against the market's own history.",
            "  * HORIZON -- market.close_time - context.now. Two candidates. "
            "The population is overwhelmingly near-expiry; behaviour differs "
            "there.",
            "  * SERIES HISTORY -- context.series_history, resolutions of "
            "earlier markets in the same series known before now. Two "
            "candidates, both as a crude base rate.",
            "  DEAD, do not use: market.liquidity (always 0.0), market.is_mve "
            "(zero observations survive to scoring).",
            "",
            "Give it a docstring stating the edge, the evidence for it, the "
            "control, and what would falsify it. State the action rate. Every "
            "number you quote is in-sample and INVARIANT #1 will exclude it "
            "from the score, so call it a hypothesis, not a result.",
            "",
            "The dataset summary below was computed once, for you and for "
            "every other candidate in this generation. PREFER IT to deriving "
            "the same shape yourself: quoting the shared numbers is what makes "
            "your claims comparable with the others', and a full pass over the "
            "observation set costs more than the idea is usually worth. Load "
            "the data only to test something the summary does not answer.",
            "",
            "Do not modify, create, or delete ANY file outside "
            "experiments/kalshi_quant/candidates/, and do not leave scratch "
            "files (pickles, notes) in it -- only the one candidate module.",
        ]
        d = self._digest or digest.load_or_build()
        if d is not None:
            lines += ["", "=" * 70, d.text]
        return "\n".join(lines)


def build() -> KalshiQuantExperiment:
    return KalshiQuantExperiment()
