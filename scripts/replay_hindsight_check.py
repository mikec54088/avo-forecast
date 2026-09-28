"""Replay unclimbed_favourite WITHOUT knowing when the market resolves.

Policy LAST  (the backtest): the last quote before resolution, <=60 min stale.
Policy FIRST (paper-like):   walk the market's quotes oldest -> newest and act on
                             the FIRST one where the candidate signals.
Policy LEAD3:                act only on quotes >= 3h before resolution.
Same markets, same candidate, same fill rule; only the entry instant differs.
Book sizes at earlier quotes are not stored, so the entry's sizes are reused.

Usage: uv run python scripts/replay_hindsight_check.py OUT.parquet
"""
import dataclasses
import sys

import pandas as pd

from avo.core import registry
from avo.core.holdout import eligible
from experiments.kalshi_quant import entries_store
from experiments.kalshi_quant.scoring import simulate_fill
from experiments.kalshi_quant.types import ForecastContext

exp = registry.load("kalshi_quant")
c = next(x for x in exp.seed_candidates() if x.candidate_id == "unclimbed_favourite")
mod = exp.load_candidate(c)

def fill(m, outcome):
    f = simulate_fill(m, "yes", 100)
    if not f.filled:
        return None
    price = f.price_cents / 100
    return (1 - price) if outcome == 1 else -price

rows = []
for chunk in entries_store.iter_entries():
    for e in eligible(c, list(chunk), lambda e: e.resolved_at):
        m0 = e.market
        pts = list(e.price_history)
        # candidate quotes: every earlier point, then the entry quote itself
        quotes = [dataclasses.replace(m0, yes_bid=p.yes_bid, yes_ask=p.yes_ask, volume=p.volume,
                                      open_interest=p.open_interest, observed_at=p.observed_at)
                  for p in pts] + [m0]
        res = {"ticker": m0.ticker, "series": m0.series_ticker, "outcome": e.outcome}
        for i, q in enumerate(quotes):
            ctx = ForecastContext(now=q.observed_at, price_history=pts[:i])
            if mod.forecast(q, ctx) <= q.implied_prob:
                continue
            lead_h = (e.resolved_at - q.observed_at).total_seconds() / 3600
            if "first" not in res:
                res["first"], res["first_lead_h"] = fill(q, e.outcome), lead_h
            if lead_h >= 3 and "lead3" not in res:
                res["lead3"] = fill(q, e.outcome)
            if i == len(quotes) - 1:
                res["last"] = fill(q, e.outcome)
        if len(res) > 3:
            rows.append(res)
pd.DataFrame(rows).to_parquet(sys.argv[1])
