"""How much room is there for buy-then-sell before settlement? (2026-09-27)

Run: uv run python scripts/swing_room.py   -> data/swing_room.csv

For each market-hour in the hourly full-pass snapshots, simulate a round trip
held for H hours, EXECUTABLE both ways:
  long  (buy YES): sell at bid(t+H), bought at ask(t)
  short (buy NO):  equivalent to selling YES at bid(t), buying back at ask(t+H)
minus Kalshi's taker fee (0.07 p (1-p) per contract) on BOTH trades.

"Room" = the better of the two directions > 0: a round trip that pays after
costs IF you had called the direction. It is an upper bound (hindsight
direction); a real strategy must predict it. Random direction averages ~ -cost.

Exclusions: price at entry outside 0.05-0.95; exit within 1h of close (the
settlement jump is the outcome, not a tradable swing); both books two-sided.
Entries sampled every 6h per market to limit overlap.
"""
from __future__ import annotations

import glob
import sys

import numpy as np
import pandas as pd

HORIZONS = [1, 6, 24, 72]
COLS = ["ticker", "event_ticker", "series_ticker", "observed_at", "close_time",
        "yes_bid", "yes_ask", "is_mve"]


def cat(s: str) -> str:
    s = s[2:]
    if s.endswith("GAME") or "MATCH" in s or s.startswith(("NFL", "NBA", "MLB", "NPB", "UFC",
                                                            "ATP", "WTA", "PGA", "F1")):
        return "sports games"
    if s.startswith(("TEMP", "HIGH", "LOWT", "RAIN", "SNOW")):
        return "weather"
    if s.startswith(("BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "HYPE", "SHIBA", "ZEC", "NEAR",
                     "CRYPTO")):
        return "crypto"
    if s.startswith(("INX", "NASDAQ", "DJI", "GOLD", "SILVER", "WTI", "BRENT", "NATGAS", "COPPER",
                     "PALLADIUM", "EURUSD", "USD", "GBP", "AUD", "UST", "SOFR")):
        return "stocks/commodities/FX"
    if s.startswith(("CPI", "FED", "JOBLESS", "CONTCLAIMS", "PPI", "USPPI", "ECONSTAT", "HOUSING",
                     "BUILD", "EHSALES", "CFNAI", "DOTPLOT", "FOMC", "CB", "30YMORT", "ARMOM")):
        return "economic data"
    if s.startswith(("AAAGAS", "DIESEL")):
        return "gas prices"
    if s.startswith(("TRUMP", "MAMDANI", "PRESS", "SECPRESS", "APRPOTUS", "GENERIC", "SWEDEN",
                     "ACTBLUE", "TRUTHSOCIAL", "LEAVEPOWELL", "MUSK", "ELON", "VANCE", "SENATE")):
        return "politics"
    if s.startswith(("NETFLIX", "RT", "YT", "SPOTIFY", "RANKLIST", "ALBUM", "STEAM", "BOXOFFICE",
                     "BILLBOARD")):
        return "entertainment/rankings"
    return "other"


def fee(p: pd.Series) -> pd.Series:
    return 0.07 * p * (1 - p)


def main() -> None:
    files = sorted(f for f in glob.glob("data/kalshi_quant/snapshots/date=*/*.parquet")
                   if not f.endswith("-near.parquet"))
    parts = []
    for f in files:
        d = pd.read_parquet(f, columns=COLS)
        d = d[~d["is_mve"].astype(bool) & (d["yes_bid"] > 0) & (d["yes_ask"] < 1)
              & (d["yes_ask"] > d["yes_bid"])]
        parts.append(d.drop(columns="is_mve"))
    d = pd.concat(parts, ignore_index=True)
    d["slot"] = pd.to_datetime(d["observed_at"], utc=True).dt.floor("h")
    d["close"] = pd.to_datetime(d["close_time"], utc=True)
    d = d.drop_duplicates(["ticker", "slot"])
    d["mid"] = (d["yes_bid"] + d["yes_ask"]) / 2
    print(f"{len(files)} hourly snapshots, {len(d):,} market-hours, "
          f"{d['ticker'].nunique():,} markets", file=sys.stderr)

    entry = d[(d["mid"].between(0.05, 0.95)) & (d["slot"].dt.hour % 6 == 0)]
    exits = d[["ticker", "slot", "yes_bid", "yes_ask"]]
    rows = []
    for h in HORIZONS:
        e = entry.assign(xslot=entry["slot"] + pd.Timedelta(hours=h))
        e = e[e["xslot"] <= e["close"] - pd.Timedelta(hours=1)]
        m = e.merge(exits, left_on=["ticker", "xslot"], right_on=["ticker", "slot"],
                    suffixes=("", "_x"))
        long_ = m["yes_bid_x"] - m["yes_ask"] - fee(m["yes_ask"]) - fee(m["yes_bid_x"])
        short = m["yes_bid"] - m["yes_ask_x"] - fee(m["yes_bid"]) - fee(m["yes_ask_x"])
        best = np.maximum(long_, short)
        cost = ((m["yes_ask"] - m["yes_bid"]) + (m["yes_ask_x"] - m["yes_bid_x"])) / 2 \
            + fee(m["mid"]) * 2
        move = ((m["yes_bid_x"] + m["yes_ask_x"]) / 2 - m["mid"]).abs()
        m = m.assign(best=best, cost=cost, move=move, cat=m["series_ticker"].map(cat),
                     long_dated=(m["close"] - m["slot"]).dt.days > 30)
        for (c, ld), g in m.groupby(["cat", "long_dated"]):
            rows.append({"horizon_h": h, "category": c + (" (closes >30d out)" if ld else ""),
                         "samples": len(g), "events": g["event_ticker"].nunique(),
                         "median_cost_c": 100 * g["cost"].median(),
                         "median_move_c": 100 * g["move"].median(),
                         "moved_past_cost_%": 100 * (g["best"] > 0).mean(),
                         "moved_past_cost+2c_%": 100 * (g["best"] > 0.02).mean(),
                         "median_gain_when_it_did_c": 100 * g.loc[g["best"] > 0, "best"].median()})
    out = pd.DataFrame(rows)
    out.to_csv("data/swing_room.csv", index=False)
    pd.set_option("display.width", 200)
    for h in HORIZONS:
        t = out[(out["horizon_h"] == h) & (out["samples"] >= 300)].sort_values(
            "moved_past_cost_%", ascending=False)
        print(f"\n=== hold {h}h ===")
        print(t.drop(columns="horizon_h").round(1).to_string(index=False))


if __name__ == "__main__":
    main()
