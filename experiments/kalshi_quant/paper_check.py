"""Daily tripwire: does the backtest agree with paper trading on the same markets?

WHY. From 2026-09-17 to 09-28 the backtest said unclimbed_favourite made
+4.7c/contract and paper trading said it made nothing. The two were measuring
different bets -- the backtest's entry rule used hindsight -- and it took
eleven days and a manual investigation to notice, because nothing compared
them. This compares them every day.

For each candidate with settled paper trades, on exactly the markets paper
traded:

  * DECISIONS -- did the replay (observations.first_signal) enter at the same
    quote paper did? Some mismatch is expected: paper decides every 15 minutes
    from the freshest file, the replay walks every quote captured. Most of a
    candidate's trades should match; a low rate means the two are no longer
    running the same rule.
  * MONEY -- paper's P&L per contract against the replay's, each with a
    series-clustered interval. If the intervals do not overlap, the backtest
    is not measuring what trading earns, and every verdict it produces for
    this candidate is suspect.

Prints one line per candidate and an ALARM line when either test fails.
Run daily under launchd: uv run python -m experiments.kalshi_quant.paper_check
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

import pandas as pd

from avo.core import registry
from experiments.kalshi_quant import entries_store, paper_log
from experiments.kalshi_quant.experiment import accumulate_fills, pnl_stats
from experiments.kalshi_quant.observations import acts, first_signal

MIN_TRADES = 30            # below this neither interval means anything
MIN_DECISION_MATCH = 0.5   # share of paper trades the replay entered identically


@dataclass(frozen=True)
class Check:
    candidate_id: str
    n_paper: int
    n_matched_markets: int
    decision_match: float
    paper: tuple[float, float, float]    # mean, lo, hi
    replay: tuple[float, float, float]
    alarm: str


def _interval(by_series: dict[str, list[float]]) -> tuple[float, float, float]:
    """Series-clustered 95% interval; with a single series (no clustered error
    exists) the per-trade one, so a thin candidate still gets compared rather
    than silently passing."""
    xs = [x for v in by_series.values() for x in v]
    s = pnl_stats(by_series, n_considered=max(1, len(xs)))
    m, se = s["pnl_per_contract"], s["pnl_se_clustered"]
    if math.isnan(se) and len(xs) > 1:
        se = statistics.stdev(xs) / math.sqrt(len(xs))
    return m, m - 1.96 * se, m + 1.96 * se


def check(paper: pd.DataFrame, paths: dict, exp, history) -> list[Check]:
    """`paper` is paper_log.pnl(settled) rows; `paths` ticker -> MarketPath."""
    out = []
    by_id = {c.candidate_id: c for c in exp.seed_candidates()}
    for cid, g in paper.groupby("candidate_id"):
        if cid not in by_id:
            continue
        mod = exp.load_candidate(by_id[cid])
        p_series: dict[str, list[float]] = {}
        r_series: dict[str, list[float]] = {}
        matched = same = 0
        for r in g.itertuples(index=False):
            path = paths.get(r.ticker)
            if path is None:
                continue
            try:
                e, f = first_signal(path, mod.forecast, history)
            except Exception as exc:  # noqa: BLE001 -- reported, never fatal
                print(f"  {cid} {r.ticker}: replay raised {exc!r}"[:200])
                continue
            matched += 1
            p_series.setdefault(r.series_ticker, []).append(float(r.pnl_per_contract))
            if acts(f, e.market.implied_prob):
                accumulate_fills([e], [f], r_series)
                same += pd.Timestamp(e.market.observed_at) == pd.Timestamp(r.observed_at)
        pi, ri = _interval(p_series), _interval(r_series)
        rate = same / matched if matched else float("nan")
        alarm = ""
        if matched >= MIN_TRADES:
            if rate < MIN_DECISION_MATCH:
                alarm = f"replay entered at paper's quote on only {rate:.0%} of trades"
            elif not r_series:
                alarm = "the replay could not fill any of the markets paper traded"
            elif pi[2] < ri[1] or ri[2] < pi[1]:
                alarm = (f"paper {pi[0]:+.4f} [{pi[1]:+.4f},{pi[2]:+.4f}] vs replay "
                         f"{ri[0]:+.4f} [{ri[1]:+.4f},{ri[2]:+.4f}] do not overlap")
        out.append(Check(str(cid), len(g), matched, rate, pi, ri, alarm))
    return out


def main() -> None:
    settled = paper_log.settled()
    trades = paper_log.pnl(settled) if not settled.empty else settled
    if trades.empty:
        print("[paper-check] no settled paper trades yet")
        return
    exp = registry.load("kalshi_quant")
    history = entries_store.series_history()
    if history is None:
        raise SystemExit("[paper-check] entries table cold or stale; run `avo entries build`")
    want = set(trades["ticker"])
    paths = {p.ticker: p for chunk in entries_store.iter_paths() for p in chunk
             if p.ticker in want}
    for c in check(trades, paths, exp, history):
        print(f"[paper-check] {c.candidate_id}: {c.n_paper} paper trades, "
              f"{c.n_matched_markets} in the replay, same entry {c.decision_match:.0%}; "
              f"paper {c.paper[0]:+.4f} [{c.paper[1]:+.4f},{c.paper[2]:+.4f}]  "
              f"replay {c.replay[0]:+.4f} [{c.replay[1]:+.4f},{c.replay[2]:+.4f}]")
        if c.alarm:
            print(f"  !!! ALARM {c.candidate_id}: {c.alarm}. The backtest is not "
                  "measuring what trading earns; do not trust its verdicts for this "
                  "candidate until this is explained.")


if __name__ == "__main__":
    main()
