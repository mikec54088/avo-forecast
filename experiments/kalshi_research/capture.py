"""Evidence capture: save what the web said about each game, 3 times before it.

Decided by the human 2026-09-26. Research ideas cannot be backtested against
today's web (it returns the answer), but they CAN be tested against evidence
saved at the time. So this job does the one step that cannot be replayed --
retrieval -- for every game in the research series, and stores it in the
append-only evidence store. The local model can be run over stored bundles
later, as often as needed, for free. Like price snapshots, evidence not
captured today is gone forever.

No model call, no Claude call. Retrieval only (local_research.retrieve).

When: at about 12h, 3h and 1h before the start. Kalshi game tickers carry
the start in US Eastern (-26SEP261910...). Tickers with a date only (most
NCAAF, much soccer) get fixed game-day checkpoints at 06:00, 12:00 and 15:00
UTC -- before any US kickoff; late for a European midday match, and labelled
so a later analysis can tell the two apart.

Which: one bundle per EVENT (retrieval already searches both sides), for every
game event in RESEARCH_SERIES with a two-sided book -- no price filter, so a
future candidate may study underdogs too. A run takes the checkpoints falling
in [now - 30 min, now + 60 min) that are not yet stored, soonest first, capped
at MAX_PER_RUN to stay polite to Google News (~6 requests each).

Run hourly under launchd: uv run python -m experiments.kalshi_research.capture
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from experiments.kalshi_research import local_research as lr
from experiments.kalshi_research.candidates.roster_news_favourite import RESEARCH_SERIES
from experiments.kalshi_research.runner import latest_snapshot

ET = ZoneInfo("America/New_York")
_MON = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
_TIMED = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})")
_DATED = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")
BEFORE = {"T-12h": timedelta(hours=12), "T-3h": timedelta(hours=3), "T-1h": timedelta(hours=1)}
GAMEDAY_UTC = {"day-06Z": 6, "day-12Z": 12, "day-15Z": 15}
EARLY = timedelta(minutes=60)     # a run may capture a checkpoint up to this early
LATE = timedelta(minutes=30)      # ... or this late (a missed run, a slow pass)
# Date-only games share three fixed checkpoints, so a Saturday stacks ~170
# NCAAF games on day-06Z -- more than one run's cap. They sit hours before any
# US kickoff, so they may be caught up over the next few runs.
LATE_GAMEDAY = timedelta(hours=3)
MAX_PER_RUN = 60


def start_time(event_ticker: str) -> datetime | None:
    m = _TIMED.search(event_ticker)
    if not m or m.group(2) not in _MON:
        return None
    return datetime(2000 + int(m.group(1)), _MON[m.group(2)], int(m.group(3)),
                    int(m.group(4)), int(m.group(5)), tzinfo=ET).astimezone(UTC)


def checkpoints(event_ticker: str) -> list[tuple[str, datetime]]:
    st = start_time(event_ticker)
    if st is not None:
        return [(lab, st - d) for lab, d in BEFORE.items()]
    m = _DATED.search(event_ticker)
    if not m or m.group(2) not in _MON:
        return []
    day = datetime(2000 + int(m.group(1)), _MON[m.group(2)], int(m.group(3)), tzinfo=UTC)
    return [(lab, day.replace(hour=h)) for lab, h in GAMEDAY_UTC.items()]


def games(snapshot: pd.DataFrame) -> pd.DataFrame:
    """One row per game event: its favourite market (highest mid)."""
    d = snapshot[snapshot["series_ticker"].isin(RESEARCH_SERIES)
                 & ~snapshot["is_mve"].astype(bool)
                 & (snapshot["yes_bid"] > 0) & (snapshot["yes_ask"] < 1)
                 & ~snapshot["title"].str.lower().str.startswith(("tie", "draw"))]
    d = d.assign(mid=(d["yes_bid"] + d["yes_ask"]) / 2)
    return d.sort_values("mid", ascending=False).drop_duplicates("event_ticker")


def captured(root: Path, days: list[str]) -> set[str]:
    keys: set[str] = set()
    for day in days:
        for f in (root / f"date={day}").glob("*.jsonl"):
            for line in f.open():
                b = json.loads(line)
                if b.get("kind") == "capture":
                    keys.add(f"{b['event_ticker']}|{b['checkpoint']}")
    return keys


def due(snapshot: pd.DataFrame, now: datetime, done: set[str]) -> list[tuple[datetime, str, pd.Series]]:
    out = []
    for _, r in games(snapshot).iterrows():
        for lab, at in checkpoints(str(r["event_ticker"])):
            key = f"{r['event_ticker']}|{lab}"
            late = LATE_GAMEDAY if lab in GAMEDAY_UTC else LATE
            if key not in done and now - late <= at < now + EARLY:
                out.append((at, lab, r))
    return sorted(out, key=lambda x: x[0])


def run(root: Path = lr.EVIDENCE_ROOT, max_per_run: int = MAX_PER_RUN) -> str:
    now = datetime.now(UTC)
    snap_path = latest_snapshot("full")
    if snap_path is None:
        raise SystemExit("no full snapshot; capture must be running")
    snap = pd.read_parquet(snap_path)
    days = [(now + timedelta(days=k)).strftime("%Y-%m-%d") for k in (-1, 0, 1)]
    todo = due(snap, now, captured(root, days))
    t0, n, failed = time.monotonic(), 0, 0
    for at, lab, r in todo[:max_per_run]:
        me = lr.side_name(str(r["title"]))
        m = lr.Market(ticker=str(r["ticker"]), title=str(r["title"]),
                      series=str(r["series_ticker"]), this_side=me,
                      opponent=lr.opponent(snap, str(r["event_ticker"]), me), asked_at=now)
        b = lr.retrieve(m)
        b.update({"kind": "capture", "checkpoint": lab, "checkpoint_at": at.isoformat(),
                  "event_ticker": str(r["event_ticker"]), "series": str(r["series_ticker"]),
                  "mid": float(r["mid"]), "snapshot": snap_path.name})
        lr.store(b, root)
        n += 1
        failed += b["state"] == "SEARCH_FAILED"
    return (f"[capture] {now:%Y-%m-%d %H:%M}Z {snap_path.name}: {len(todo)} checkpoints due, "
            f"stored {n} ({failed} SEARCH_FAILED), deferred {max(0, len(todo) - n)} "
            f"in {time.monotonic() - t0:.0f}s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=MAX_PER_RUN)
    print(run(max_per_run=ap.parse_args().max), flush=True)


if __name__ == "__main__":
    main()
