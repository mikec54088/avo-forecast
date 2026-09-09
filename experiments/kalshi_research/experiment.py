"""kalshi_research implements core.interfaces.Experiment.

Same venue and the same fitness as kalshi_quant -- Brier skill vs the market's
implied probability, the pessimistic P&L gate, series-clustered intervals, the
selection/confirmation split by series with the same salt -- so the two are
directly comparable. That comparison, "does research time buy calibration?", is
the research question (docs/EXPERIMENTS.md).

What differs is where observations come from. kalshi_quant replays captured
snapshots through a candidate; this experiment reads the FORECAST LOG that the
runner wrote live (forecast_log.py). A candidate's forecasts already exist by
scoring time; score() only joins them to resolutions and judges them. The
holdout is per forecast: an outcome counts only if it resolved strictly after
the forecast was made.
"""
from __future__ import annotations

import glob
import importlib
import pkgutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from avo.core.holdout import is_eligible
from avo.core.selection import is_confirmation_group
from avo.core.types import Candidate, Score
from experiments.kalshi_quant.experiment import (  # noqa: F401
    paper_trade,
    passes_pnl_gate,
    pnl_verdict,
)
from experiments.kalshi_quant.observations import DATA_ROOT as QUANT_ROOT
from experiments.kalshi_quant.observations import Entry
from experiments.kalshi_quant.scoring import (
    Observation,
    bootstrap_ci,
    bootstrap_ci_clustered,
    skill_score,
)
from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research import forecast_log

CONTRACT = "forecast(market: MarketSnapshot, context: ResearchContext) -> float"


@dataclass(frozen=True)
class LoggedForecast:
    """One logged forecast joined to the outcome it was made before."""

    candidate_id: str
    forecast: float
    forecast_at: datetime
    entry: Entry                 # market as quoted at forecast time, outcome, resolved_at
    research_calls: int
    research_elapsed_s: float


def _market(r: Any) -> MarketSnapshot:
    return MarketSnapshot(
        ticker=r.ticker, event_ticker=r.event_ticker, series_ticker=r.series_ticker,
        title=r.title, observed_at=r.observed_at.to_pydatetime(),
        close_time=r.close_time.to_pydatetime(),
        yes_bid=float(r.yes_bid), yes_ask=float(r.yes_ask),
        last_price=None if pd.isna(r.last_price) else float(r.last_price),
        volume=float(r.volume), open_interest=float(r.open_interest),
        yes_bid_size=None if pd.isna(r.yes_bid_size) else float(r.yes_bid_size),
        yes_ask_size=None if pd.isna(r.yes_ask_size) else float(r.yes_ask_size),
        price_level_structure=str(r.price_level_structure),
    )


def load_forecast_observations(
    log_root: Path | None = None, resolutions_root: Path | None = None
) -> list[LoggedForecast]:
    """Every logged forecast whose market has since resolved -- AFTER the forecast."""
    log = forecast_log.read(log_root)
    if log.empty:
        return []
    res_root = (resolutions_root or QUANT_ROOT) / "resolutions"
    files = sorted(glob.glob(str(res_root / "date=*" / "*.parquet")))
    if not files:
        return []
    res = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    res = res[res["outcome"].notna()].drop_duplicates("ticker")
    j = log.merge(res[["ticker", "resolved_at", "outcome"]], on="ticker", how="inner")
    j["forecast_at"] = pd.to_datetime(j["forecast_at"], utc=True)
    j["resolved_at"] = pd.to_datetime(j["resolved_at"], utc=True)
    j["observed_at"] = pd.to_datetime(j["observed_at"], utc=True)
    j["close_time"] = pd.to_datetime(j["close_time"], utc=True)
    # THE HOLDOUT. A forecast made after its market resolved is not a forecast.
    j = j[j["resolved_at"] > j["forecast_at"]]
    out = []
    for r in j.itertuples(index=False):
        fa, ra = r.forecast_at.to_pydatetime(), r.resolved_at.to_pydatetime()
        assert is_eligible(fa, ra)
        out.append(LoggedForecast(
            candidate_id=str(r.candidate_id), forecast=float(r.forecast), forecast_at=fa,
            entry=Entry(market=_market(r), resolved_at=ra, outcome=int(r.outcome)),
            research_calls=int(r.research_calls), research_elapsed_s=float(r.research_elapsed_s),
        ))
    return out


def _pct(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))]


class KalshiResearchExperiment:
    name = "kalshi_research"

    def load_candidate(self, candidate: Candidate) -> Any:
        mod = importlib.import_module(candidate.module_path)
        if not hasattr(mod, "forecast"):
            raise TypeError(f"{candidate.module_path} missing forecast(); expected {CONTRACT}")
        if not hasattr(mod, "MANIFEST"):
            raise TypeError(f"{candidate.module_path} missing MANIFEST dict")
        return mod

    def scoring_inputs(self) -> tuple[list[LoggedForecast], None]:
        obs = load_forecast_observations()
        if not obs:
            raise SystemExit("no resolved forecasts yet; the research runner must "
                             "run and its markets must settle first")
        return obs, None

    def score(
        self,
        candidate: Candidate,
        loaded: Any,
        entries: Sequence[LoggedForecast] | None = None,
        history: Any = None,
        subset: str = "all",
    ) -> Score:
        """Judge the candidate's LOGGED forecasts. Nothing is recomputed."""
        if entries is None:
            entries = load_forecast_observations()
        mine = [o for o in entries if o.candidate_id == candidate.candidate_id]
        if subset != "all":
            want = subset == "confirmation"
            mine = [o for o in mine
                    if is_confirmation_group(o.entry.market.series_ticker) == want]
        if not mine:
            return Score(candidate.candidate_id, float("nan"), (float("nan"), float("nan")),
                         0, notes=f"no resolved forecasts in the {subset} subset")

        obs = [Observation(o.forecast, o.entry.market.implied_prob, o.entry.outcome)
               for o in mine]
        scored = [o.entry for o in mine]
        forecasts = [o.forecast for o in mine]
        cb, mb, skill = skill_score(obs)
        series = [e.market.series_ticker for e in scored]
        lo, hi = bootstrap_ci_clustered(obs, series)
        ilo, ihi = bootstrap_ci(obs)
        stale = sorted(e.staleness_minutes for e in scored)
        trade = paper_trade(scored, forecasts)
        calls = sum(o.research_calls for o in mine)
        return Score(
            candidate_id=candidate.candidate_id, primary=skill, primary_ci=(lo, hi),
            n_observations=len(obs),
            secondary={
                "candidate_brier": cb, "market_brier": mb, "forecast_errors": 0.0,
                "staleness_median_min": _pct(stale, 0.5), "staleness_p90_min": _pct(stale, 0.9),
                "skill_ci_iid_lo": ilo, "skill_ci_iid_hi": ihi,
                "skill_ci_width_ratio": ((hi - lo) / (ihi - ilo) if (ihi - ilo) > 0 else float("nan")),
                "research_calls": float(calls),
                "research_calls_per_market": calls / len(mine),
                "research_elapsed_s": sum(o.research_elapsed_s for o in mine),
                **trade,
            },
            notes=f"forward-only from the forecast log; primary_ci is series-clustered; subset={subset}",
        )

    def seed_candidates(self) -> Sequence[Candidate]:
        pkg = importlib.import_module("experiments.kalshi_research.candidates")
        out: list[Candidate] = []
        for info in pkgutil.iter_modules(pkg.__path__):
            mod = importlib.import_module(f"{pkg.__name__}.{info.name}")
            m = mod.MANIFEST
            out.append(Candidate(
                candidate_id=m["candidate_id"], experiment=self.name,
                generation=m.get("generation", 0), parent_id=m.get("parent_id"),
                created_at=datetime.fromisoformat(m["created_at"]),
                module_path=f"{pkg.__name__}.{info.name}",
                rationale=m.get("rationale", ""),
                meta={"role": m.get("role", "candidate")},
            ))
        return out

    def validation_probe(self) -> str:
        """Real fixture markets, a STUB researcher. No network in validation.

        Checks: returns a float in [0,1]; deterministic given the same stub
        answers; respects the research budget (a candidate that keeps calling
        after the budget is spent raises, and that is a rejection); does not
        research EVERY market -- a candidate that spends on all 200 fixtures
        cannot be afforded live. Unlike kalshi_quant there is no "must deviate"
        check: on canned answers a sensible research candidate may abstain.
        """
        return """    import json
    from datetime import datetime
    from pathlib import Path
    from experiments.kalshi_quant.types import MarketSnapshot
    from experiments.kalshi_research.types import ResearchContext
    from experiments.kalshi_research.researcher import StubResearcher

    _fx = json.loads(Path("tests/fixtures/probe_markets.json").read_text())

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

    _markets = [_mk(_r) for _r in _fx["markets"]]
    _sibs = {_t: [_mk(_r) for _r in _rs] for _t, _rs in _fx.get("siblings", {}).items()}

    def _run():
        _stub = StubResearcher()
        _outs = []
        for _m in _markets:
            _ctx = ResearchContext(now=_m.observed_at, researcher=_stub,
                                   siblings=_sibs.get(_m.ticker, []))
            _v = mod.forecast(_m, _ctx)
            if isinstance(_v, bool) or not isinstance(_v, (int, float)):
                problems.append("forecast() returned %s, not a float" % type(_v).__name__)
                return None, _stub.calls
            _v = float(_v)
            if _v != _v or not (0.0 <= _v <= 1.0):
                problems.append("forecast() returned %r on %s, outside [0,1]" % (_v, _m.ticker))
                return None, _stub.calls
            _outs.append(_v)
        return _outs, _stub.calls

    try:
        _a, _calls = _run()
    except BaseException as _e:
        problems.append("forecast() raised: %r" % (_e,))
        _a, _calls = None, 0
    if _a is not None:
        _b, _ = _run()
        if _a != _b:
            problems.append("forecast() is not deterministic given identical research answers")
        if _calls >= len(_markets):
            problems.append("researches every market (%d calls on %d markets); "
                            "unaffordable live -- it must be selective" % (_calls, len(_markets)))
        info["research_calls_on_probe"] = _calls
        info["probe_markets"] = len(_markets)
"""

    def variation_prompt(self, parent: Candidate, siblings: Sequence[Score]) -> str:
        lines = [
            "Write ONE new forecasting candidate for the kalshi_research "
            "experiment in this repository.",
            "",
            "Read experiments/kalshi_research/types.py for the contract. The "
            "candidate is forecast(market, context) -> float, and context.research"
            "(query) returns text from a live web search. Each call is a model "
            "invocation with search; the budget is context.budget per market "
            "(default 3) and exceeding it raises.",
            "",
            "THE GOVERNING CONSTRAINT. Crossing the spread plus fees costs about "
            "2 probability points. The book alone has been searched exhaustively "
            "(kalshi_quant, 40+ candidates) and holds no tradeable edge. What the "
            "book cannot contain is information: injuries, weather, a poll, a "
            "scheduling change. Research only where such information plausibly "
            "exists and the market is unlikely to have absorbed it.",
            "",
            "THE COST CONSTRAINT. Research is the entire cost of this experiment. "
            "Decide from market.title and the quote WHETHER to research before "
            "researching. Abstain (return market.implied_prob) on markets where "
            "no external information can help, e.g. 15-minute crypto index "
            "markets. A candidate that researches every market cannot be run.",
            "",
            "THE MEASURABILITY CONSTRAINT. Forecasts are made live and scored "
            "only once the market resolves; judged on ~200 fills across many "
            "series. Act on enough markets to be judged within two weeks.",
            "",
        ]
        if parent is not None:
            lines += [f"PARENT: {parent.candidate_id} (module {parent.module_path})",
                      f"  rationale: {parent.rationale}",
                      "Differ in MECHANISM: what you research, on which markets, "
                      "and how the finding maps to a probability.", ""]
        ranked = sorted((s for s in siblings if s.primary == s.primary),
                        key=lambda s: (pnl_verdict(s)[0], -s.primary))
        if ranked:
            lines.append("ALREADY TRIED (money verdict first):")
            for s in ranked[:20]:
                _, why = pnl_verdict(s)
                lines.append(f"  {s.candidate_id:<26} {why[:60]}  skill {s.primary:+.4f}  "
                             f"research/market {s.secondary.get('research_calls_per_market', 0):.2f}")
            lines.append("")
        lines += [
            "Give it a docstring stating the edge, which markets it researches "
            "and why, what it asks, how the answer maps to a probability, and "
            "what would falsify it. It cannot be backtested: replaying an old "
            "market against today's web returns the answer. Do not try.",
            "",
            "Do not modify, create, or delete ANY file outside "
            "experiments/kalshi_research/candidates/, and leave no scratch files.",
        ]
        return "\n".join(lines)


def build() -> KalshiResearchExperiment:
    return KalshiResearchExperiment()
