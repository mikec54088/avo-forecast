"""One live paper-trading pass: decide on open markets, write the decision down.

Reads the freshest NEAR-PASS snapshot -- at most 15 minutes old, and it is the
pass that carries every market approaching resolution. Deliberately NOT the
full pass: this is about deciding on a quote you could actually have traded,
and an hour-old book is not one.

Which candidates trade here is a deliberate whitelist, not "everything in the
registry". Paper trading is a claim that a specific strategy is worth real
money; running 42 candidates through it would reproduce exactly the
multiple-comparison problem the confirmation split exists to control.

Under launchd like everything else (cron is TCC-blocked on this Mac).
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Self

import pandas as pd

from avo.core import registry
from experiments.kalshi_quant import paper_log
from experiments.kalshi_quant.observations import _row_to_snapshot
from experiments.kalshi_quant.types import MarketSnapshot

SNAPSHOT_ROOT = Path(__file__).resolve().parents[2] / "data" / "kalshi_quant" / "snapshots"
LOCK_STALE_SECONDS = 20 * 60
MAX_QUOTE_AGE_MIN = 20.0

# Exposure is capped per EVENT, not per market, because a nested ladder is one
# bet wearing many hats. Measured 2026-09-20: of 296 paper trades, 80 were rungs
# of five Nasdaq-100 events -- "above 29409.99", "above 29399.99", "above
# 29389.99" ... sixteen strikes ten points apart on one index at one instant.
# The index closing above the top strike settles every rung, so at 100 contracts
# a signal that is 1,600 contracts riding on a single close, not sixteen
# diversified positions. The edge may be real and the sizing would still be
# wrong.
#
# Equal to the per-market size by default: one event, one position's worth of
# risk, however many rungs it offers.
MAX_CONTRACTS_PER_EVENT = 100

# Only these trade on paper. Adding one is a decision that a strategy has
# earned a live test, and it should be recorded in the commit that adds it.
# unclimbed_favourite (2026-09-17): the first candidate to pass the P&L gate on
# BOTH selection and confirmation series, and to survive the series, weekend,
# staleness and matched-control checks.
PAPER_CANDIDATES = ("unclimbed_favourite",)


class _Lock:
    def __init__(self, root: Path) -> None:
        self.path = root / ".paper.lock"

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            age = datetime.now(timezone.utc).timestamp() - self.path.stat().st_mtime
            if age < LOCK_STALE_SECONDS:
                raise SystemExit(f"another pass holds {self.path.name} ({age:.0f}s); skipping")
            print(f"clearing stale lock ({age:.0f}s old)")
        self.path.write_text(str(os.getpid()))
        return self

    def __exit__(self, *exc: object) -> None:
        self.path.unlink(missing_ok=True)


def latest_near_snapshot(root: Path = SNAPSHOT_ROOT) -> Path | None:
    import glob
    files = glob.glob(str(root / "date=*" / "*-near.parquet"))
    return Path(max(files, key=os.path.getmtime)) if files else None


def _history_for(df: pd.DataFrame, ticker: str, before: datetime) -> list:
    """This market's own earlier quotes from the same day's near passes.

    A candidate keying on price_history needs the path, and the live pass has
    only one snapshot. Rebuilt from the near-pass files on disk, which is the
    same source observations.py uses.
    """
    from experiments.kalshi_quant.types import PricePoint
    # Empty on the day's first pass, before any near-pass file exists for it.
    # A candidate keying on price_history then correctly sees no path and
    # abstains, rather than the runner dying on a missing column.
    if df.empty or "ticker" not in df.columns:
        return []
    rows = df[(df["ticker"] == ticker)].sort_values("observed_at")
    return [
        PricePoint(r.observed_at.to_pydatetime(), float(r.yes_bid), float(r.yes_ask),
                   float(r.volume), float(r.open_interest))
        for r in rows.itertuples(index=False)
        if r.observed_at.to_pydatetime() < before
    ]


def run_pass(
    snapshot: pd.DataFrame,
    history_frame: pd.DataFrame,
    now: datetime | None = None,
    root: Path | None = None,
    candidates: tuple[str, ...] = PAPER_CANDIDATES,
    desired_contracts: int = 100,
    experiment: object = None,
    max_contracts_per_event: int = MAX_CONTRACTS_PER_EVENT,
) -> tuple[Path | None, dict[str, int]]:
    from experiments.kalshi_quant.types import ForecastContext

    now = now or datetime.now(timezone.utc)
    root = root or paper_log.DATA_ROOT
    exp = experiment or registry.load("kalshi_quant")
    by = {c.candidate_id: c for c in exp.seed_candidates()}
    loaded = []
    for cid in candidates:
        if cid not in by:
            print(f"  skip {cid}: not in the registry", flush=True)
            continue
        try:
            loaded.append((by[cid], exp.load_candidate(by[cid])))
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {cid}: {exc!r}", flush=True)

    already = paper_log.decided(root)
    stats = {"markets": len(snapshot), "asked": 0, "acted": 0,
             "skipped_seen": 0, "errors": 0, "contracts": 0, "capped": 0}
    rows: list[dict[str, object]] = []
    # Contracts already committed per event, including earlier passes: a ladder
    # can appear across several passes as successive rungs enter the window.
    committed: dict[tuple[str, str], int] = {}
    prior = paper_log.read(root)
    if not prior.empty and "event_ticker" in prior.columns:
        for cid, ev, ct in zip(prior["candidate_id"], prior["event_ticker"],
                               prior["contracts"], strict=True):
            committed[(cid, ev)] = committed.get((cid, ev), 0) + int(ct)

    for r in snapshot.itertuples(index=False):
        m: MarketSnapshot = _row_to_snapshot(r)
        if not m.has_two_sided_book:
            continue
        hist = None
        for c, mod in loaded:
            if (c.candidate_id, m.ticker) in already:
                stats["skipped_seen"] += 1
                continue
            if hist is None:
                hist = _history_for(history_frame, m.ticker, m.observed_at)
            ctx = ForecastContext(now=now, series_history={}, price_history=hist, siblings=[])
            err = ""
            try:
                p = float(mod.forecast(m, ctx))
                if not (0.0 <= p <= 1.0):
                    err, p = f"forecast {p!r} outside [0,1]", m.implied_prob
            except Exception as exc:  # noqa: BLE001
                err, p = f"raised {exc!r}"[:200], m.implied_prob
            stats["asked"] += 1
            stats["errors"] += bool(err)
            key = (c.candidate_id, m.event_ticker)
            room = max(0, max_contracts_per_event - committed.get(key, 0))
            want = min(desired_contracts, room)
            row = paper_log.row(c.candidate_id, m, p, now, want, err)
            if row["acted"]:
                stats["acted"] += 1
                got = int(row["contracts"])
                if room < desired_contracts:
                    stats["capped"] += 1
                    row["fill_reason"] = (row["fill_reason"] or
                                          f"event exposure cap ({room} of "
                                          f"{desired_contracts} left)")
                committed[key] = committed.get(key, 0) + got
                stats["contracts"] += got
                rows.append(row)          # the decision is recorded even at size 0

    return paper_log.append(rows, root), stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["run"])
    ap.add_argument("--contracts", type=int, default=100)
    ap.add_argument("--max-contracts-per-event", type=int,
                    default=MAX_CONTRACTS_PER_EVENT,
                    help="total contracts per EVENT per candidate (default "
                         "%(default)s). A nested ladder is one bet with many "
                         "rungs; sizing per market turns it into 16x the risk.")
    ap.add_argument("--max-quote-age-min", type=float, default=MAX_QUOTE_AGE_MIN)
    args = ap.parse_args()

    snap = latest_near_snapshot()
    if snap is None:
        raise SystemExit("no near-pass snapshot; capture must be running")
    age_min = (time.time() - snap.stat().st_mtime) / 60
    if age_min > args.max_quote_age_min:
        raise SystemExit(f"latest near snapshot is {age_min:.0f} min old (limit "
                         f"{args.max_quote_age_min:g}); refusing to 'trade' a stale book")

    with _Lock(paper_log.DATA_ROOT):
        started = datetime.now(timezone.utc)
        df = pd.read_parquet(snap)
        # today's near passes, for price_history
        import glob
        day = Path(snap).parent
        hist_df = pd.concat(
            [pd.read_parquet(f) for f in sorted(glob.glob(str(day / "*-near.parquet")))],
            ignore_index=True)
        path, st = run_pass(df, hist_df, now=started, desired_contracts=args.contracts,
                            max_contracts_per_event=args.max_contracts_per_event)
        el = (datetime.now(timezone.utc) - started).total_seconds()
        print(f"[paper] {st['markets']} markets from {snap.name} ({age_min:.0f} min old); "
              f"asked {st['asked']}, ACTED {st['acted']} ({st['contracts']} contracts, "
              f"{st['capped']} capped by event exposure), "
              f"{st['skipped_seen']} decided earlier, {st['errors']} errors "
              f"in {el:.0f}s -> {path}")


if __name__ == "__main__":
    main()
