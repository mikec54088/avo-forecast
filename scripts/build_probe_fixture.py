"""Sample real markets into the fixture the candidate validator probes against.

    uv run python scripts/build_probe_fixture.py

Synthetic probe markets sweep one field at a time from a fixed base, so field
COMBINATIONS never occur. That systematically rejects candidates with narrow
gates -- on 2026-09-01 `near_close_shoulders`, which needs a shoulder price AND
a near close together, was rejected for "never deviating" when it was simply
never triggered. Blunt always-act candidates passed. That is a selection
pressure toward crude strategies, built into the harness.

Real markets carry realistic combinations for free. The sample is stratified so
the rare corners are present rather than left to chance, and committed so
validation works in a fresh clone with no data directory.
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "probe_markets.json"
PER_CELL = 6


def main() -> None:
    snaps = sorted(glob.glob(str(ROOT / "data/kalshi_quant/snapshots/date=*/*.parquet")))
    res_files = sorted(glob.glob(str(ROOT / "data/kalshi_quant/resolutions/date=*/*.parquet")))
    if not snaps:
        raise SystemExit("no snapshots on disk to sample from")

    df = pd.concat([pd.read_parquet(f) for f in snaps[-6:]], ignore_index=True)
    df = df.drop_duplicates("ticker")
    df["mid"] = (df.yes_bid + df.yes_ask) / 2.0
    df["spread"] = df.yes_ask - df.yes_bid
    df["ttc_h"] = (df.close_time - df.observed_at).dt.total_seconds() / 3600.0
    df["imb"] = (df.yes_bid_size - df.yes_ask_size) / (df.yes_bid_size + df.yes_ask_size)

    picks: list[pd.DataFrame] = []

    def take(mask, label):
        sub = df[mask]
        if len(sub):
            picks.append(sub.sample(min(PER_CELL, len(sub)), random_state=len(label)))

    # Price bands x spread, so shoulder-and-tight-book style gates are hit.
    for lo, hi in [(0, .05), (.05, .35), (.35, .65), (.65, .95), (.95, 1.0)]:
        band = (df["mid"] > lo) & (df["mid"] <= hi)
        take(band & (df.spread <= 0.04), f"tight{lo}")
        take(band & (df.spread > 0.04) & (df.spread <= 0.08), f"mid{lo}")
        take(band & (df.spread > 0.08), f"wide{lo}")
        # ...crossed with time to close, for calendar gates
        take(band & (df.ttc_h <= 1), f"near{lo}")
        take(band & (df.ttc_h > 24), f"far{lo}")
    # Corners that are rare and easy to miss
    take(df.last_price.isna(), "notraded")
    take(df.last_price.notna() & (df.last_price < df.yes_bid), "printbelow")
    take(df.last_price.notna() & (df.last_price > df.yes_ask), "printabove")
    take(df.volume <= 0, "novolume")
    take(df.volume > 100_000, "heavy")
    take(df.open_interest <= 0, "nooi")
    take(df.imb > 0.8, "bidheavy")
    take(df.imb < -0.8, "askheavy")
    take(df.price_level_structure == "deci_cent", "deci")
    take(df.price_level_structure == "linear_cent", "linear")

    sample = pd.concat(picks).drop_duplicates("ticker")
    cols = ["ticker", "event_ticker", "series_ticker", "title", "observed_at",
            "close_time", "yes_bid", "yes_ask", "last_price", "volume",
            "open_interest", "yes_bid_size", "yes_ask_size", "liquidity",
            "status", "price_level_structure", "is_mve"]
    rows = []
    for r in sample[cols].itertuples(index=False):
        d = dict(zip(cols, r, strict=True))
        d["observed_at"] = d["observed_at"].isoformat()
        d["close_time"] = d["close_time"].isoformat()
        for k in ("last_price", "yes_bid_size", "yes_ask_size"):
            if pd.isna(d[k]):
                d[k] = None
        d["title"] = str(d["title"])[:60]
        rows.append(d)

    # Real resolution history for the sampled series, so a memory candidate has
    # something true to read rather than a fabricated distribution.
    history: dict[str, list] = {}
    if res_files:
        res = pd.concat([pd.read_parquet(f) for f in res_files[-40:]], ignore_index=True)
        res = res[res.outcome.notna()].drop_duplicates("ticker")
        want = {r["series_ticker"] for r in rows}
        for s, g in res[res.series_ticker.isin(want)].groupby("series_ticker"):
            g = g.sort_values("resolved_at").tail(60)
            history[s] = [
                {"ticker": t, "resolved_at": ra.isoformat(), "outcome": int(o)}
                for t, ra, o in zip(g.ticker, g.resolved_at, g.outcome, strict=True)
            ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"markets": rows, "history": history}, indent=1))
    print(f"{len(rows)} markets, {len(history)} series with history -> {OUT}")
    print(f"  price      {min(r['yes_bid'] for r in rows):.4f} .. "
          f"{max(r['yes_ask'] for r in rows):.4f}")
    print(f"  never traded {sum(r['last_price'] is None for r in rows)}   "
          f"zero volume {sum(r['volume'] == 0 for r in rows)}")
    print(f"  {OUT.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
