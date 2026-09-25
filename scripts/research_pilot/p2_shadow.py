"""P2 shadow test of the local v3 research pipeline on NEW games. LOCAL ONLY.

2026-09-25: the human stopped all Sonnet use ("it is eating up tokens") and
made the local model the research engine. The first 12 markets were run
against Sonnet (all agreed, all NONE); from then on Sonnet is not called at
all and its fields read NOT_RUN. Evaluation is by hand-adjudicating the local
positives plus a sample of NONEs. The original design follows.

Originally: Sonnet vs the local v3 pipeline on NEW games.

Authorised by the human 2026-09-25. Each pass takes the newest full-pass
snapshot, selects game markets with roster_news_favourite's own filter
(`wants_research`: research series, favourite 0.50-0.90, spread <= 4c, game
day), and asks BOTH researchers the identical question at the same moment:

  - Sonnet: the old arm's exact researcher (ClaudeResearcher, pinned
    claude-sonnet-5, prompt v2) and exact query string;
  - local: p1_replay v3 retrieval (Google News + RotoWire + Liquipedia,
    news <= 24h) and qwen3.5:9b screen-then-judge.

NOTHING is logged as a forecast, nothing trades, no candidate sees this. The
output is a comparison log, adjudicated by hand afterwards.

One event per market pair (the favourite side). At most PER_PASS markets per
pass, round-robin across series in an order shuffled per pass.
Stops for good at TARGET markets. Aborts the pass on a quota message so it
never burns the account's window (the lesson of generations 3, 4 and 7).

Run:  uv run --with trafilatura python scripts/research_pilot/p2_shadow.py
Out:  data/kalshi_research/pilot/p2_results.jsonl, p2_bundles.jsonl, p2_run.log
"""
from __future__ import annotations

import glob
import json
import random
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import p1_replay as local

from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research.candidates import roster_news_favourite as rnf

PER_PASS = 6
TARGET = 150   # local-only costs no Claude quota; more positives to adjudicate
MIN_LEAD = timedelta(minutes=20)
OUT = ROOT / "data/kalshi_research/pilot"
ESPORTS = set(local.LIQUIPEDIA_WIKI)


_START = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})")
ET = ZoneInfo("America/New_York")


def start_time(ticker: str) -> datetime | None:
    """Kalshi game tickers carry the start in US Eastern: ...-26SEP251840PITDET.
    Soccer tickers carry only the date; those return None."""
    m = _START.search(ticker)
    if not m or m.group(2) not in rnf._MON:
        return None
    return datetime(2000 + int(m.group(1)), rnf._MON[m.group(2)], int(m.group(3)),
                    int(m.group(4)), int(m.group(5)), tzinfo=ET).astimezone(UTC)


def latest_full_snapshot() -> Path:
    files = [f for f in glob.glob(str(ROOT / "data/kalshi_quant/snapshots/date=*/*.parquet"))
             if not f.endswith("-near.parquet")]
    return Path(max(files, key=lambda f: (Path(f).parent.name, Path(f).name)))


def done_events() -> set[str]:
    p = OUT / "p2_results.jsonl"
    if not p.exists():
        return set()
    return {json.loads(line)["event_ticker"] for line in p.open()}


def candidates(snap: pd.DataFrame, now: datetime, skip: set[str]) -> list[pd.Series]:
    keep = []
    for r in snap.itertuples(index=False):
        if r.event_ticker in skip or r.series_ticker not in rnf.RESEARCH_SERIES:
            continue
        m = MarketSnapshot(**{f: getattr(r, f) for f in MarketSnapshot.__dataclass_fields__
                              if hasattr(r, f)})
        st = start_time(r.ticker)
        # a fair test researches BEFORE the game: skip anything already started
        if rnf.wants_research(m, now) and (st is None or st > now + MIN_LEAD):
            keep.append(r)
    # one market per event (the favourite), then round-robin across series
    by_event: dict[str, object] = {}
    for r in keep:
        mid = (r.yes_bid + r.yes_ask) / 2
        best = by_event.get(r.event_ticker)
        if best is None or mid > (best.yes_bid + best.yes_ask) / 2:
            by_event[r.event_ticker] = r
    by_series: dict[str, list] = {}
    for r in sorted(by_event.values(), key=lambda r: r.ticker):
        by_series.setdefault(r.series_ticker, []).append(r)
    # Shuffled per pass. The first two passes used esports-first-then-alphabetical,
    # and with 6 slots never reached MLB/NCAAF/EPL -- the leagues with the most
    # news. Seeded by the hour so a pass is reproducible.
    order = sorted(by_series)
    random.Random(int(now.timestamp()) // 3600).shuffle(order)
    picked = []
    while len(picked) < PER_PASS and any(by_series.values()):
        for s in order:
            if by_series[s] and len(picked) < PER_PASS:
                picked.append(by_series[s].pop(0))
    return picked


def opponent(snap: pd.DataFrame, r) -> str:
    sib = {local.side_name(t) for t in snap[snap.event_ticker == r.event_ticker].title}
    rest = sorted(s for s in sib if s != local.side_name(r.title)
                  and not s.lower().startswith(("tie", "draw")))
    return rest[0] if rest else ""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    skip = done_events()
    if len(skip) >= TARGET:
        print(f"[p2] target {TARGET} reached ({len(skip)}); nothing to do")
        return
    now = datetime.now(UTC)
    f = latest_full_snapshot()
    snap = pd.read_parquet(f)
    snap = snap[snap.series_ticker.isin(rnf.RESEARCH_SERIES)]
    picks = candidates(snap, now, skip)[: TARGET - len(skip)]
    print(f"[p2] {now:%Y-%m-%d %H:%M}Z snapshot {f.parent.name}/{f.name}: "
          f"{len(picks)} markets ({len(skip)} done of {TARGET})", flush=True)
    for r in picks:
        this_side = local.side_name(r.title)
        opp = opponent(snap, r)
        x = type("X", (), {"ticker": r.ticker, "title": r.title, "this_side": this_side,
                           "series_ticker": r.series_ticker,
                           "forecast_at": pd.Timestamp(now)})()
        t0 = time.time()
        bundle = local.retrieve(x, opp)
        if bundle["state"] == "EVIDENCE_AVAILABLE":
            lv = local.judge(x, bundle, full=False)
        else:
            lv = {"verdict": bundle["state"], "reason": "", "sec": 0.0}
        wall = time.time() - t0
        rec = {"at": now.isoformat(), "ticker": r.ticker, "event_ticker": r.event_ticker,
               "series": r.series_ticker, "title": r.title, "this_side": this_side,
               "opponent": opp, "mid": (r.yes_bid + r.yes_ask) / 2,
               "sonnet": "NOT_RUN",  # Sonnet stopped by the human 2026-09-25
               "local_model": local.MODEL,
               "local": lv["verdict"], "local_reason": lv.get("reason", ""),
               "local_cited": lv.get("cited", ""), "local_screened": lv.get("screened", ""),
               "local_sec": lv.get("sec", 0.0), "local_wall": wall,
               "local_state": bundle["state"], "local_items": len(bundle["items"]),
               "local_errors": bundle["errors"]}
        bundle["at"] = now.isoformat()
        with (OUT / "p2_bundles.jsonl").open("a") as fh:
            fh.write(json.dumps(bundle, ensure_ascii=False) + "\n")
        with (OUT / "p2_results.jsonl").open("a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[p2] {r.ticker[:36]:36} local={rec['local']:10} ({wall:4.0f}s) "
              f"items={len(bundle['items'])}", flush=True)

if __name__ == "__main__":
    main()
