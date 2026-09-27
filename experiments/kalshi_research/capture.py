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

POLITICS (added 2026-09-27, human decision): short-dated political and "will
X say Y" markets -- Truth Social post counts, approval averages, executive
orders, mentions, announcements. Not games, so no sides: one bundle per EVENT
from a topic search built out of its market titles (common words, plus the
quoted words of a "say" event grouped into OR-batches). Timing follows the
CLOSE, not a start: daily at 15:00 UTC while within POLITICS_HORIZON of close,
then 3h and 1h before it. Measured over 2026-09-20..26: ~565 short-dated
markets in ~122 events and ~41 series a week, spreads mostly 1-2c. The
high-volume political markets are years-long ("leave office before 2029") and
excluded: they cannot be scored in any useful time.

Run hourly under launchd: uv run python -m experiments.kalshi_research.capture
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
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

POLITICS = re.compile(r"^KX(TRUMP|PRESSSEC|SECPRESS|MAMDANI|TRUTHSOCIAL|APRPOTUS|GENERICBALLOT"
                      r"|LEAVEPOWELL|VANCE|LEAVITT|SENATE|BILLSCOUNT|FEDGOVNOM|NEWSOM|MUSK"
                      r"|WHITEHOUSE)")
POLITICS_HORIZON = timedelta(days=7)
BEFORE_CLOSE = {"C-3h": timedelta(hours=3), "C-1h": timedelta(hours=1)}
_STOP = {
    "will", "the", "a", "an", "be", "of", "in", "on", "at", "by", "to", "for", "from",
    "before", "after", "between", "and", "or", "any", "least", "than", "more", "less",
    "exactly", "above", "below", "during", "week", "month", "day", "days", "number",
    "distinct", "per", "rules", "e", "g", "eg", "include", "including", "other", "next",
    "his", "her", "their", "this", "that", "what", "which", "who", "is", "are", "have",
    "has", "make", "made", "take", "did", "does", "do", "say", "said", "says", "us", "et",
    "am", "pm", "new", "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
} | {m.lower() for m in _MON}
# Some events' titles carry no topic at all ("Above 8.3%"): the series says it.
SERIES_TOPIC = {
    "KXGENERICBALLOTVOTEHUB": "generic congressional ballot poll",
    "KXTRUMPVH": "Trump approval rating poll",
    "KXTRUMPAPPROVE": "Trump approval rating",
    "KXAPRPOTUS": "Trump approval rating",
    "KXTRUTHSOCIAL": "Trump Truth Social posts",
}


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


def politics(snapshot: pd.DataFrame, now: datetime) -> dict[str, pd.DataFrame]:
    """Short-dated political events: every market of each event, keyed by event."""
    close = pd.to_datetime(snapshot["close_time"], utc=True)
    d = snapshot[snapshot["series_ticker"].str.match(POLITICS)
                 & ~snapshot["is_mve"].astype(bool)
                 & (close > pd.Timestamp(now)) & (close <= pd.Timestamp(now + POLITICS_HORIZON))]
    return {e: g for e, g in d.groupby("event_ticker")}


def close_checkpoints(close: datetime, now: datetime) -> list[tuple[str, datetime]]:
    out = [(lab, close - d) for lab, d in BEFORE_CLOSE.items()]
    day = (now - timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
    while day < close - BEFORE_CLOSE["C-3h"]:
        out.append((f"day-{day:%Y%m%d}-15Z", day))
        day += timedelta(days=1)
    return out


def _words(text: str) -> list[str]:
    text = re.sub(r'"[^"]*"', " ", text)
    return [w for w in re.findall(r"[A-Za-z][A-Za-z'.-]+", text)
            if w.lower().strip(".'") not in _STOP and len(w) > 2]


def topic_queries(titles: list[str], series: str = "") -> list[str]:
    """At most 4 searches per event: its common topic words (or the series'
    topic when the titles carry none), then the quoted words of a "say" event
    in OR-batches of 8."""
    counts: dict[str, int] = {}
    order: list[str] = []
    for t in titles:
        for w in dict.fromkeys(_words(t)):
            if w not in counts:
                order.append(w)
            counts[w] = counts.get(w, 0) + 1
    common = [w for w in order if counts[w] >= 0.6 * len(titles)][:6]
    if len(common) < 2:
        common = _words(titles[0])[:6]
    topic = SERIES_TOPIC.get(series) or " ".join(common)
    if not topic:
        topic = re.sub(r"^KX", "", series).lower()
    queries = [topic]
    # Titles make narrow topics ("...approval rating according RealClearPolitics"
    # found nothing in 24h where "Trump approval rating" found 30), so a
    # broader first-three-words search rides along.
    broad = " ".join(topic.split()[:3])
    if broad != topic:
        queries.append(broad)
    quoted = list(dict.fromkeys(q for t in titles for q in re.findall(r'"([^"]+)"', t)))
    entity = next((w for w in common if w[0].isupper()), common[0] if common else "")
    for i in range(0, min(len(quoted), 24), 8):
        batch = " OR ".join(f'"{q}"' for q in quoted[i:i + 8])
        queries.append(f"{entity} ({batch})".strip())
    return queries[:4]


def captured(root: Path, days: list[str]) -> set[str]:
    keys: set[str] = set()
    for day in days:
        for f in (root / f"date={day}").glob("*.jsonl"):
            for line in f.open():
                b = json.loads(line)
                if b.get("kind") == "capture":
                    keys.add(f"{b['event_ticker']}|{b['checkpoint']}")
    return keys


def due(snapshot: pd.DataFrame, now: datetime, done: set[str]) -> list[tuple[datetime, str, Any]]:
    """(when, checkpoint, payload): a game's favourite row (Series), or a
    political event's markets (DataFrame)."""
    out: list[tuple[datetime, str, Any]] = []
    for _, r in games(snapshot).iterrows():
        for lab, at in checkpoints(str(r["event_ticker"])):
            key = f"{r['event_ticker']}|{lab}"
            late = LATE_GAMEDAY if lab in GAMEDAY_UTC else LATE
            if key not in done and now - late <= at < now + EARLY:
                out.append((at, lab, r))
    for ev, g in politics(snapshot, now).items():
        close = pd.to_datetime(g["close_time"], utc=True).min().to_pydatetime()
        for lab, at in close_checkpoints(close, now):
            late = LATE_GAMEDAY if lab.startswith("day-") else LATE
            if f"{ev}|{lab}" not in done and now - late <= at < now + EARLY:
                out.append((at, lab, g))
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
        if isinstance(r, pd.DataFrame):          # a political event
            ev = str(r["event_ticker"].iloc[0])
            b = lr.retrieve_topic(ev, topic_queries(r["title"].astype(str).tolist(),
                                                     str(r["series_ticker"].iloc[0])), now)
            b.update({"kind": "capture", "category": "politics", "checkpoint": lab,
                      "checkpoint_at": at.isoformat(), "event_ticker": ev,
                      "series": str(r["series_ticker"].iloc[0]), "snapshot": snap_path.name,
                      "markets": [{"ticker": str(x.ticker), "title": str(x.title),
                                   "mid": float((x.yes_bid + x.yes_ask) / 2),
                                   "close_time": str(x.close_time)}
                                  for x in r.itertuples(index=False)]})
        else:
            me = lr.side_name(str(r["title"]))
            m = lr.Market(ticker=str(r["ticker"]), title=str(r["title"]),
                          series=str(r["series_ticker"]), this_side=me,
                          opponent=lr.opponent(snap, str(r["event_ticker"]), me),
                          asked_at=now)
            b = lr.retrieve(m)
            b.update({"kind": "capture", "category": "sports", "checkpoint": lab,
                      "checkpoint_at": at.isoformat(), "event_ticker": str(r["event_ticker"]),
                      "series": str(r["series_ticker"]), "mid": float(r["mid"]),
                      "snapshot": snap_path.name})
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
