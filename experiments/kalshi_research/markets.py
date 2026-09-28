"""What kind of market is this, and when does its game start?

Shared by capture (when to save evidence), the local researcher (which
pipeline answers) and the candidates (when to stop asking). Kept free of
imports from those modules so any of them can use it.

GAME START. Kalshi game tickers carry the start in US Eastern
(-26SEP261910...). Many do not: most NCAAF and all Latin American soccer carry
a date only. For those the CLOSE says it: every timed research series closes
exactly 48h after the start (72h for MLB), measured 2026-09-28 over 413 timed
events with zero exceptions; and on the date-only series the hourly volume
spike -- the game being traded live -- lands 46-47h before close, i.e. 1-2h
after close - 48h. So close - 48h is the start for a date-only game. If a
series ever closes LATER than 48h after its start, the estimate falls early
and research stops early -- the safe direction.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
_MON = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
_TIMED = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})")
_DATED = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")
CLOSE_AFTER_START = timedelta(hours=48)
# Research is logged at the pass's start instant but runs during the pass
# (a pass researches soonest-closing first and has taken 2-8 min), so stop a
# little before the whistle rather than at it.
START_MARGIN = timedelta(minutes=15)

POLITICS = re.compile(r"^KX(TRUMP|PRESSSEC|SECPRESS|MAMDANI|TRUTHSOCIAL|APRPOTUS|GENERICBALLOT"
                      r"|LEAVEPOWELL|VANCE|LEAVITT|SENATE|BILLSCOUNT|FEDGOVNOM|NEWSOM|MUSK"
                      r"|WHITEHOUSE)")
POLITICS_HORIZON = timedelta(days=7)


def start_time(event_ticker: str) -> datetime | None:
    """The start written in the ticker, if it carries a time."""
    m = _TIMED.search(event_ticker)
    if not m or m.group(2) not in _MON:
        return None
    return datetime(2000 + int(m.group(1)), _MON[m.group(2)], int(m.group(3)),
                    int(m.group(4)), int(m.group(5)), tzinfo=ET).astimezone(UTC)


def game_start(event_ticker: str, close_time: datetime) -> datetime:
    """The ticker's start, or close - 48h for a date-only ticker (see above)."""
    return start_time(event_ticker) or close_time - CLOSE_AFTER_START


def game_started(event_ticker: str, close_time: datetime, now: datetime,
                 margin: timedelta = START_MARGIN) -> bool:
    """True from `margin` before the start onward. Research after the start
    reads live blogs and scores -- information the quote it is scored against
    may not carry yet -- so no research candidate may use it."""
    return now >= game_start(event_ticker, close_time) - margin


def is_politics(series_ticker: str) -> bool:
    return bool(POLITICS.match(series_ticker))


# ----------------------------------------------------------- politics search

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
