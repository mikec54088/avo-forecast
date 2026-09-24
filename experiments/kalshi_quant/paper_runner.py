"""One live paper-trading pass: decide on open markets, write the decision down.

Reads the freshest NEAR-PASS snapshot for markets inside 24h -- at most 15
minutes old -- and the freshest HOURLY FULL PASS for markets beyond it.

The full pass was excluded until 2026-09-24 on the grounds that an hour-old
book is not one you could have traded. That was right about freshness and
wrong about coverage, and the coverage error was the larger one. The near pass
is bounded to markets closing within 24h, so paper trading could only ever act
inside that window -- while unclimbed_favourite, the only whitelisted
candidate, earns its money almost entirely outside it. Measured 2026-09-23
over its replay fills, by hours-to-close at entry:

    >48h     863 fills (48.8%)   +0.0923 / contract
    24-48h   837 fills (47.3%)   +0.0307
    12-24h     1 fill  ( 0.1%)
    <12h      68 fills ( 3.8%)   +0.0144

96% of the evidence sits beyond 24h. Every one of the 524 paper trades sat
inside it. Three consecutive losing days read as the edge decaying; they were
a different population, in the band replay had already measured at roughly
zero. The instrument meant to validate the candidate had never once tested it.

Widening capture instead was measured and rejected: a 72h bounded sweep walks
251+ pages against the near pass's 9, because auto-generated parlay combos are
densest in exactly that 1-7 day window, and a series-by-series sweep costs one
request per series across 4,096 of them. The hourly full pass ALREADY captures
this band -- 2,786 quotable markets 24-72h out, every hour -- so the fix needs
no new requests, no new job, and no change to capture at all.

The cost is quote age: up to ~85 min on the full-pass side against ~2 min on
the near side. That is not a lower standard than the evidence we have, since
ENTRY_POLICY caps replay staleness at 60 minutes and the edge was measured
under it. Every decision records `quote_source` so the cost can be measured
rather than argued about.

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

# The full pass runs hourly and takes ~24 min, so its freshest file can be well
# over an hour old by the time a paper pass reads it. 95 min accepts the normal
# worst case and still refuses a pass that has genuinely stopped -- the point
# of a staleness guard is to catch a dead capture, not to re-litigate the
# freshness trade-off this source was added to make.
MAX_FULL_QUOTE_AGE_MIN = 95.0

# Below this, use the near pass; above it, the full pass. Equal to the near
# pass's own --max-close-hours bound, so the two sources partition the universe
# and no market can be decided twice from two different books.
NEAR_HORIZON_HOURS = 24.0

# ...and no further than this. The full pass has no horizon bound at all -- one
# sweep carries 64,962 markets closing beyond 72h, out to 638,608 hours -- so
# an upper bound is not a tuning knob, it is the difference between 2,786
# markets and 67,748.
#
# 72h because that is where the evidence is. unclimbed_favourite's replay
# fills, by hours-to-close at entry: p10 45.6, median 47.9, p90 54.3. A 72h
# ceiling covers the whole measured band with margin and buys nothing by going
# further, while everything past it is a market nobody has evidence about
# priced days or years early.
FULL_HORIZON_HOURS = 72.0

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


def latest_full_snapshot(root: Path = SNAPSHOT_ROOT) -> Path | None:
    """The freshest hourly full-pass file. Full-pass files carry no suffix."""
    import glob
    files = [f for f in glob.glob(str(root / "date=*" / "*.parquet"))
             if not f.endswith("-near.parquet")]
    return Path(max(files, key=os.path.getmtime)) if files else None


def hours_to_close(df: pd.DataFrame, now: datetime) -> pd.Series:
    return (pd.to_datetime(df["close_time"], utc=True)
            - pd.Timestamp(now)).dt.total_seconds() / 3600.0


def _history_index(df: pd.DataFrame,
                   keep: set[str] | None = None) -> dict[str, list]:
    """ticker -> its earlier quotes, oldest first, built in ONE pass.

    This used to be a filter per ticker, which is fine over the day's near
    files (~38k rows, ~1,600 tickers) and quadratic over the full pass. Adding
    the hourly source on 2026-09-24 made the history frame ~1.7M rows and the
    pass stopped finishing inside its 15-minute slot -- the first version of
    this change hung for over two minutes and was killed before it ever ran
    live. `keep` narrows the frame to the tickers actually being asked about
    before any of that work happens.
    """
    from experiments.kalshi_quant.types import PricePoint
    # Empty on the day's first pass, before any snapshot file exists for it.
    # A candidate keying on price_history then correctly sees no path and
    # abstains, rather than the runner dying on a missing column.
    if df.empty or "ticker" not in df.columns:
        return {}
    if keep is not None:
        df = df[df["ticker"].isin(keep)]
        if df.empty:
            return {}
    out: dict[str, list] = {}
    for r in df.sort_values("observed_at").itertuples(index=False):
        out.setdefault(r.ticker, []).append(
            PricePoint(r.observed_at.to_pydatetime(), float(r.yes_bid),
                       float(r.yes_ask), float(r.volume), float(r.open_interest)))
    return out


def _history_for(df: pd.DataFrame, ticker: str, before: datetime) -> list:
    """One market's path. Kept for callers that want a single lookup."""
    return [p for p in _history_index(df, {ticker}).get(ticker, [])
            if p.observed_at < before]


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

    # One pass over the history frame, narrowed to the tickers in play.
    hidx = _history_index(
        history_frame,
        set(snapshot["ticker"]) if "ticker" in snapshot.columns else None)

    already = paper_log.decided(root)
    stats = {"markets": len(snapshot), "asked": 0, "acted": 0,
             "skipped_seen": 0, "errors": 0, "contracts": 0, "capped": 0,
             "acted_near": 0, "acted_full": 0}
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
                hist = [pt for pt in hidx.get(m.ticker, ())
                        if pt.observed_at < m.observed_at]
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
            row = paper_log.row(c.candidate_id, m, p, now, want, err,
                                quote_source=getattr(r, "quote_source", "near"))
            if row["acted"]:
                stats["acted"] += 1
                stats["acted_" + str(row["quote_source"])] = (
                    stats.get("acted_" + str(row["quote_source"]), 0) + 1)
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
    ap.add_argument("--max-full-quote-age-min", type=float,
                    default=MAX_FULL_QUOTE_AGE_MIN,
                    help="max age of the hourly full-pass file before its "
                         "markets are skipped (default %(default)s). The near "
                         "pass is unaffected and the pass still runs.")
    ap.add_argument("--full-horizon-hours", type=float, default=FULL_HORIZON_HOURS,
                    help="ignore full-pass markets closing beyond this many "
                         "hours (default %(default)s). Without a ceiling the "
                         "full pass contributes ~65,000 markets closing months "
                         "out, none of which any evidence covers.")
    ap.add_argument("--near-horizon-hours", type=float, default=NEAR_HORIZON_HOURS,
                    help="markets closing within this many hours come from the "
                         "near pass, beyond it from the full pass (default "
                         "%(default)s). Must match the near pass's own "
                         "--max-close-hours or the two sources overlap.")
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
        import glob
        day = Path(snap).parent

        # NEAR side: markets inside 24h, exactly as before.
        df = pd.read_parquet(snap)
        df["quote_source"] = "near"
        hist_df = pd.concat(
            [pd.read_parquet(f) for f in sorted(glob.glob(str(day / "*-near.parquet")))],
            ignore_index=True)

        # FULL side: markets beyond 24h, from the hourly sweep. Added strictly
        # as an EXTRA source -- every failure here degrades to the near-only
        # behaviour that has been running since 2026-09-17 rather than taking
        # the pass down. The near path is the proven one; a new source must not
        # be able to break it.
        full_note = "full pass not used"
        fsnap = latest_full_snapshot()
        if fsnap is not None:
            fage = (time.time() - fsnap.stat().st_mtime) / 60
            if fage > args.max_full_quote_age_min:
                full_note = (f"full pass {fage:.0f} min old > "
                             f"{args.max_full_quote_age_min:g}, skipped")
            else:
                try:
                    fdf = pd.read_parquet(fsnap)
                    fh = hours_to_close(fdf, started)
                    fdf = fdf[(fh > args.near_horizon_hours)
                              & (fh <= args.full_horizon_hours)].copy()
                    fdf["quote_source"] = "full"
                    fday = Path(fsnap).parent
                    fhist = pd.concat(
                        [pd.read_parquet(f)
                         for f in sorted(glob.glob(str(fday / "*.parquet")))
                         if not f.endswith("-near.parquet")],
                        ignore_index=True)
                    df = pd.concat([df, fdf], ignore_index=True)
                    # One frame: a market beyond 24h never appears in a near
                    # file, so the two histories cannot collide on a ticker.
                    hist_df = pd.concat([hist_df, fhist], ignore_index=True)
                    full_note = (f"+{len(fdf)} markets "
                                 f"{args.near_horizon_hours:g}-"
                                 f"{args.full_horizon_hours:g}h "
                                 f"from {fsnap.name} ({fage:.0f} min old)")
                except Exception as exc:  # noqa: BLE001
                    full_note = f"full pass unusable ({exc!r}); near only"

        path, st = run_pass(df, hist_df, now=started, desired_contracts=args.contracts,
                            max_contracts_per_event=args.max_contracts_per_event)
        el = (datetime.now(timezone.utc) - started).total_seconds()
        print(f"[paper] {st['markets']} markets from {snap.name} ({age_min:.0f} min old), "
              f"{full_note}; asked {st['asked']}, ACTED {st['acted']} "
              f"({st['acted_near']} near / {st['acted_full']} full, "
              f"{st['contracts']} contracts, "
              f"{st['capped']} capped by event exposure), "
              f"{st['skipped_seen']} decided earlier, {st['errors']} errors "
              f"in {el:.0f}s -> {path}")


if __name__ == "__main__":
    main()
