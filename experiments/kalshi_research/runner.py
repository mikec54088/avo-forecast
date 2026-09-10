"""One forward pass: forecast open markets live, append to the forecast log.

Markets come from the most recent NEAR-PASS SNAPSHOT (kalshi_quant's capture,
markets closing within 24h, every 15 minutes) rather than from a fresh API
walk. Three reasons: the quote is at most 15 minutes old and INVARIANT #4 fills
against it; settle() is driven by what appears in snapshots, so every market
forecast here is guaranteed to have its resolution recorded without any new
API surface; and it costs zero requests.

Each (candidate, ticker) is scored on ONE forecast. The rule for which one:

  * a candidate that ACTS (returns something other than the market) or SPENDS
    research is done with that market at that moment -- the forecast is logged
    and it is never asked again;
  * a candidate that abstains is asked again on later passes, and its
    abstention is logged once, when the market is inside `final_hours` of its
    close, so that skill and P&L are computed over the same population as an
    acting candidate's.

That rule exists because of where the markets are. Measured 2026-09-09 over a
Sunday-Tuesday: major-league game markets (KXNCAAFGAME, KXMLBGAME, KXMLSGAME,
KXEPLGAME ...) carry a close_time two to three DAYS after the game and never
enter the near-pass (<=24h) window at all -- 0 of 1,505 rows in a typical near
pass, 24 obscure-league game markets in three days. They arrive only through
the hourly full pass. So the runner reads the full pass with a wide selection
window, and a research candidate decides for itself when the game is close
enough to be worth spending on. Logging its early abstention as its one
forecast would have made every research candidate look like the control.

It is a different entry policy from kalshi_quant's "last snapshot before
resolution within 60 min", and it must be: a research candidate cannot be
re-run later against an earlier quote.

Runs under launchd like capture does (scripts/launchd/); cron is TCC-blocked on
this Mac and fails silently. Takes a lock so passes never overlap.
"""
from __future__ import annotations

import argparse
import glob
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Self

import pandas as pd

from experiments.kalshi_quant.observations import _row_to_snapshot
from experiments.kalshi_quant.types import MarketSnapshot
from experiments.kalshi_research import forecast_log
from experiments.kalshi_research.researcher import make
from experiments.kalshi_research.types import (
    DEFAULT_BUDGET,
    ResearchContext,
    Researcher,
)

SNAPSHOT_ROOT = Path(__file__).resolve().parents[2] / "data" / "kalshi_quant" / "snapshots"
MAX_SPREAD = 0.08          # simulate_fill refuses wider books; do not spend research on them
LOCK_STALE_SECONDS = 40 * 60


class _Lock:
    def __init__(self, root: Path, name: str = "run") -> None:
        self.path = root / f".{name}.lock"

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            age = datetime.now(timezone.utc).timestamp() - self.path.stat().st_mtime
            if age < LOCK_STALE_SECONDS:
                raise SystemExit(f"another pass holds {self.path.name} (age {age:.0f}s); skipping")
            print(f"clearing stale lock ({age:.0f}s old)")
        self.path.write_text(str(os.getpid()))
        return self

    def __exit__(self, *exc: object) -> None:
        self.path.unlink(missing_ok=True)


def latest_snapshot(kind: str = "full", root: Path = SNAPSHOT_ROOT) -> Path | None:
    """Most recent snapshot file. "near" = the 15-minute <=24h pass (quotes
    <=15 min old, but no major-league game markets); "full" = the hourly
    unbounded pass (quotes <=60 min old, everything)."""
    files = glob.glob(str(root / "date=*" / "*.parquet"))
    files = [f for f in files if f.endswith("-near.parquet") == (kind == "near")]
    return Path(max(files, key=os.path.getmtime)) if files else None


def latest_near_snapshot(root: Path = SNAPSHOT_ROOT) -> Path | None:
    return latest_snapshot("near", root)


def select_markets(
    df: pd.DataFrame, now: datetime, max_close_hours: float,
    max_markets: int, max_spread: float = MAX_SPREAD,
) -> pd.DataFrame:
    """Two-sided, fillable, non-MVE, closing inside the window; soonest first."""
    if df.empty:
        return df
    close = pd.to_datetime(df["close_time"], utc=True)
    horizon = (close - pd.Timestamp(now)).dt.total_seconds() / 3600.0
    keep = (
        (df["yes_bid"] > 0) & (df["yes_ask"] > df["yes_bid"]) & (df["yes_ask"] < 1)
        & ((df["yes_ask"] - df["yes_bid"]) <= max_spread + 1e-9)
        & (~df["is_mve"].astype(bool))
        & (horizon > 0) & (horizon <= max_close_hours)
    )
    out = df[keep].assign(_h=horizon[keep]).sort_values("_h").drop(columns="_h")
    return out.head(max_markets)


def _siblings(df: pd.DataFrame, m: MarketSnapshot) -> list[MarketSnapshot]:
    sib = df[(df["event_ticker"] == m.event_ticker) & (df["ticker"] != m.ticker)]
    return [_row_to_snapshot(r) for r in sib.itertuples(index=False)]


def run_pass(
    researcher: Researcher,
    snapshot: pd.DataFrame,
    now: datetime | None = None,
    max_close_hours: float = 120.0,
    max_markets: int = 3000,
    budget: int = DEFAULT_BUDGET,
    root: Path | None = None,
    experiment: Any = None,
    final_hours: float = 1.0,
) -> tuple[Path | None, dict[str, int]]:
    """Ask every not-yet-done candidate about every selected market; log the
    ones that are done after this pass (see the module docstring)."""
    from avo.core import registry

    now = now or datetime.now(timezone.utc)
    root = root or forecast_log.DATA_ROOT
    exp = experiment or registry.load("kalshi_research")
    cands = []
    for c in exp.seed_candidates():
        try:
            cands.append((c, exp.load_candidate(c)))
        except Exception as exc:  # noqa: BLE001 - one broken candidate must not stop the pass
            print(f"  skip {c.candidate_id}: {exc!r}", flush=True)

    already = forecast_log.seen(root)
    chosen = select_markets(snapshot, now, max_close_hours, max_markets)
    stats = {"markets": len(chosen), "asked": 0, "forecasts": 0, "acted": 0,
             "deferred": 0, "skipped_seen": 0, "errors": 0, "research_calls": 0,
             "research_failed": 0}
    rows: list[dict[str, object]] = []

    for r in chosen.itertuples(index=False):
        m = _row_to_snapshot(r)
        hours_left = (m.close_time - now).total_seconds() / 3600.0
        sibs = None
        for c, mod in cands:
            if (c.candidate_id, m.ticker) in already:
                stats["skipped_seen"] += 1
                continue
            if sibs is None:
                sibs = _siblings(snapshot, m)
            ctx = ResearchContext(now=now, researcher=researcher, budget=budget, siblings=sibs)
            t0 = time.monotonic()
            err = ""
            try:
                p = float(mod.forecast(m, ctx))
                if not (0.0 <= p <= 1.0):
                    err, p = f"forecast {p!r} outside [0,1]", m.implied_prob
            except Exception as exc:  # noqa: BLE001
                err, p = f"raised {exc!r}"[:200], m.implied_prob
            stats["asked"] += 1
            stats["research_calls"] += ctx.calls
            stats["research_failed"] += ctx.research_failed
            acted = abs(p - m.implied_prob) > 1e-12 or ctx.calls > 0 or bool(err)
            # A broken research channel is not a forecast. Consuming the market
            # would log an abstention indistinguishable from the control and
            # never ask again -- see ResearchContext.research_failed.
            if ctx.research_failed and hours_left > final_hours:
                stats["deferred"] += 1
                continue
            if not acted and hours_left > final_hours:
                stats["deferred"] += 1      # ask again next pass
                continue
            stats["forecasts"] += 1
            stats["acted"] += acted
            stats["errors"] += bool(err)
            rows.append(forecast_log.row(
                c.candidate_id, m, p, now, ctx.calls,
                ctx.elapsed_s or (time.monotonic() - t0), researcher.name, err,
                "; ".join(ctx.errors)[:300],
                " || ".join(f"Q: {t.query} -> {t.text}" for t in ctx.trail)[:4000]))

    path = forecast_log.append(rows, root) if rows else None
    return path, stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["run"])
    ap.add_argument("--researcher", default="claude", choices=["claude", "null"])
    ap.add_argument("--model", default=None,
                    help="research model; defaults to "
                         "researcher.DEFAULT_RESEARCH_MODEL. Pinned on purpose "
                         "(INVARIANT #7): the CLI default is a user setting.")
    ap.add_argument("--source", default="full", choices=["full", "near"],
                    help="which capture pass to read; full is the only one that "
                         "carries major-league game markets")
    ap.add_argument("--max-close-hours", type=float, default=120.0,
                    help="selection window; game markets close 2-3 days after the game")
    ap.add_argument("--final-hours", type=float, default=1.0,
                    help="an abstention is logged once inside this many hours of close")
    ap.add_argument("--max-markets", type=int, default=3000)
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    args = ap.parse_args()

    snap = latest_snapshot(args.source)
    if snap is None:
        raise SystemExit(f"no {args.source} snapshot found; capture must be running")
    age_min = (time.time() - snap.stat().st_mtime) / 60
    limit = 45 if args.source == "near" else 100
    if age_min > limit:
        raise SystemExit(f"latest {args.source} snapshot is {age_min:.0f} min old; "
                         "capture may have stopped")

    with _Lock(forecast_log.DATA_ROOT):
        started = datetime.now(timezone.utc)
        df = pd.read_parquet(snap)
        path, st = run_pass(make(args.researcher, args.model), df,
                            max_close_hours=args.max_close_hours,
                            max_markets=args.max_markets, budget=args.budget,
                            final_hours=args.final_hours)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        print(f"[{args.source} <={args.max_close_hours:g}h] {st['markets']} markets from "
              f"{snap.name} ({age_min:.0f} min old); asked {st['asked']}, logged "
              f"{st['forecasts']} ({st['acted']} acted), deferred {st['deferred']}, "
              f"{st['skipped_seen']} done earlier, {st['errors']} errors, "
              f"{st['research_calls']} research calls "
              f"({st['research_failed']} FAILED) in {elapsed:.0f}s -> {path}")
        if st["research_failed"]:
            print(f"      WARNING: {st['research_failed']} research call(s) failed. "
                  "Those markets were deferred, not scored. Check credits/network.")


if __name__ == "__main__":
    main()
