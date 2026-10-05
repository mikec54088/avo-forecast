"""Would a resting order have filled, and was the fill worth having? (2026-10-05)

Run:  uv run python scripts/maker_fill_model.py [--horizon 15 60] [--size 10]

MEASUREMENT ONLY. INVARIANT #4 stays in force for every candidate; nothing here
may be used to assume maker fills until this model has been read and approved.

Setup, signal-free on purpose: at each hourly near-pass quote, rest an order at
the touch and wait. Buy YES at the bid (queue ahead = yes_bid_size), or buy NO
at 1 - ask (queue ahead = yes_ask_size). Hold to settlement. Compare with
crossing the spread at the same instant, and with the book's own midpoint, so
the answer separates three things: the spread saved, the maker fee, and
ADVERSE SELECTION (resting orders fill most when the price is moving through
them, i.e. when you are wrong).

Pessimistic passive-fill rules (a resting order is never filled on hope):
  * Queue: everyone resting at our price at the snapshot is ahead of us, and
    cancellations ahead of us are IGNORED (they would only help us).
  * We fill only when taker volume AT our price since the snapshot reaches
    queue_ahead + size -- all of our size, no partials -- or when any trade
    prints THROUGH our price (the level was fully consumed).
  * Buy-yes side counts takers who sold yes (hit the bid); buy-no side counts
    takers who bought yes (lifted the ask). Trades before the snapshot never
    count; the order is cancelled at the horizon.
  * Orders are skipped when the market closes inside the horizon, when the
    book is not two-sided or the spread exceeds 8c (simulate_fill's gate), or when the price is under 3c / over 97c.
  * Maker fee: 0.0175 * p * (1-p) per contract, rounded UP to the cent per
    order -- applied to EVERY market. Kalshi charges it only on some, so this
    is pessimistic; a no-fee column is printed for scale.
  * Quotes from the last 3 days are dropped (resolution backfill lags ~2 days
    and the missing ones skew short-lived), as are unresolved markets.

No lookahead in the DECISION: the quote and its queue come from the snapshot;
the tape after it only decides fills; the outcome only prices them.
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experiments.kalshi_quant.scoring import trading_fee_dollars

ROOT = Path(__file__).resolve().parents[1]
SNAPS = ROOT / "data/kalshi_quant/snapshots"
TAPE = ROOT / "data/kalshi_quant/trades"
RESOLUTIONS = ROOT / "data/kalshi_quant/resolutions"
MAKER_RATE = 0.0175


def maker_fee(price: float, n: int) -> float:
    """Per contract, dollars; rounded up to the cent on the whole order."""
    return math.ceil(round(MAKER_RATE * n * price * (1 - price) * 100, 6)) / 100 / n


def taker_fee(price: float, n: int) -> float:
    return trading_fee_dollars(price, n) / n


def category(series: str) -> str:
    s = series[2:]
    if s.startswith(("BTC", "ETH", "SOL", "XRP", "DOGE")):
        return "crypto"
    if s.startswith(("TEMP", "HIGH", "LOWT", "RAIN")):
        return "weather"
    if s.startswith(("NASDAQ", "INX", "WTI", "GOLD", "SILVER", "NATGAS", "EURUSD", "USDJPY")):
        return "indices/commodities/fx"
    if s.endswith(("GAME", "MATCH")) or "MATCH" in s or s.startswith(
            ("NFL", "NBA", "MLB", "NHL", "UFC", "ATP", "WTA", "ITF", "CS2", "LOL", "NPB", "KBO")):
        return "sports"
    return "other"


def load_outcomes() -> pd.Series:
    parts = []
    for f in RESOLUTIONS.rglob("*.parquet"):
        r = pd.read_parquet(f)
        if "outcome" in r:
            parts.append(r[["ticker", "outcome"]])
    r = pd.concat(parts).dropna().drop_duplicates("ticker")
    return r.set_index("ticker").outcome.astype(int)


def load_quotes(first: str, last_excl: str) -> pd.DataFrame:
    """Hourly (:00) near-pass quotes, two-sided, inside the pessimistic filters."""
    files = [f for f in SNAPS.rglob("*-near.parquet") if f.name[2:4] == "00"
             and first <= f.parent.name[5:] < last_excl]
    cols = ["ticker", "series_ticker", "observed_at", "close_time", "yes_bid", "yes_ask",
            "yes_bid_size", "yes_ask_size"]
    q = pd.concat([ds.dataset(f).to_table(columns=cols).to_pandas() for f in files],
                  ignore_index=True)
    for c in ("observed_at", "close_time"):
        q[c] = pd.to_datetime(q[c], utc=True)
    q["bid"] = np.round(q.yes_bid * 100).astype(int)
    q["ask"] = np.round(q.yes_ask * 100).astype(int)
    q = q[(q.bid >= 3) & (q.ask <= 97) & (q.ask > q.bid) & (q.ask - q.bid <= 8)
          & q.yes_bid_size.notna() & q.yes_ask_size.notna()]
    return q.drop_duplicates(["ticker", "observed_at"]).reset_index(drop=True)


def load_tape(day: pd.Timestamp, span_h: int, tickers: set[str]) -> pd.DataFrame:
    parts = []
    for k in range(24 + span_h + 1):
        h = day + timedelta(hours=k)
        p = TAPE / f"date={h:%Y-%m-%d}" / f"{h:%H}.parquet"
        if p.exists():
            t = pd.read_parquet(p)
            parts.append(t[t.ticker.astype(str).isin(tickers)])
    t = pd.concat(parts, ignore_index=True)
    t["ticker"] = t.ticker.astype(str)
    t["t"] = t.ts.dt.as_unit("ns").astype("int64")       # ns since epoch
    return t.sort_values(["ticker", "t"], kind="stable").reset_index(drop=True)


def simulate(q: pd.DataFrame, tape: pd.DataFrame, horizon_min: int, size: int) -> pd.DataFrame:
    """Per quote and side: fill (bool) and fill time (minutes after the quote)."""
    tk = tape.ticker.values
    starts = {k: i for i, k in reversed(list(enumerate(tk)))}  # first row per ticker
    first = pd.Series(starts).sort_values()               # tape is sorted by ticker
    last = first.shift(-1).fillna(len(tk)).astype(int)
    t_ns, price, cnt = tape.t.values, tape.yes_cents.values, tape["count"].values.astype(float)
    taker_yes = tape.taker_yes.values.astype(bool)
    out_fill = np.zeros((len(q), 2), dtype=bool)
    out_min = np.full((len(q), 2), np.nan)
    h_ns = horizon_min * 60 * 10**9
    for i, (tkr, ts, bid, ask, qb, qa) in enumerate(zip(
            q.ticker.values, q.observed_at.dt.as_unit("ns").astype("int64").values, q.bid.values, q.ask.values,
            q.yes_bid_size.values, q.yes_ask_size.values, strict=True)):
        if tkr not in first.index:
            continue
        a, b = first[tkr], last[tkr]
        lo = a + np.searchsorted(t_ns[a:b], ts, side="right")
        hi = a + np.searchsorted(t_ns[a:b], ts + h_ns, side="right")
        if hi <= lo:
            continue
        p, c, ty, tt = price[lo:hi], cnt[lo:hi], taker_yes[lo:hi], t_ns[lo:hi]
        for side, (px, ahead, through, hit) in enumerate((
                (bid, qb, p < bid, (p == bid) & ~ty),         # buy yes at the bid
                (ask, qa, p > ask, (p == ask) & ty))):         # buy no at 1 - ask
            ft = math.inf
            if through.any():
                ft = tt[np.argmax(through)]
            if hit.any():
                cum = np.cumsum(c[hit])
                j = np.searchsorted(cum, ahead + size, side="left")
                if j < len(cum):
                    ft = min(ft, tt[hit][j])
            if ft < math.inf:
                out_fill[i, side] = True
                out_min[i, side] = (ft - ts) / 6e10
    return pd.DataFrame({"fill_yes": out_fill[:, 0], "fill_no": out_fill[:, 1],
                         "min_yes": out_min[:, 0], "min_no": out_min[:, 1]}, index=q.index)


def clustered_ci(x: np.ndarray, cl: np.ndarray, n: int = 2000) -> tuple[float, float]:
    if len(x) < 2:
        return (math.nan, math.nan)
    codes, inv = np.unique(cl, return_inverse=True)
    sums, cnts = np.bincount(inv, weights=x), np.bincount(inv)
    if len(codes) < 2:
        return (math.nan, math.nan)
    idx = np.random.default_rng(0).integers(0, len(codes), size=(n, len(codes)))
    m = sums[idx].sum(1) / cnts[idx].sum(1)
    return float(np.quantile(m, 0.025)), float(np.quantile(m, 0.975))


def report(trades: pd.DataFrame, label: str) -> str:
    if len(trades) < 20:
        return f"  {label:28s} {len(trades):7,d} fills"
    lo, hi = clustered_ci(trades.maker_pnl.values, trades.cluster.values)
    return (f"  {label:28s} {len(trades):7,d} fills  maker {trades.maker_pnl.mean():+.4f} "
            f"[{lo:+.4f},{hi:+.4f}]  no-fee {trades.nofee_pnl.mean():+.4f}  "
            f"adverse sel. {trades.adverse.mean():+.4f}  "
            f"median fill {trades.minutes.median():4.1f} min")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, nargs="+", default=[15, 60])
    ap.add_argument("--size", type=int, default=10)
    ap.add_argument("--first", default="2026-09-20")
    a = ap.parse_args()

    outcome = load_outcomes()
    tape_days = sorted(p.name[5:] for p in TAPE.glob("date=*") if p.name[5:] >= a.first)
    last_day = (pd.Timestamp.now(tz="UTC") - timedelta(days=3)).strftime("%Y-%m-%d")
    days = [d for d in tape_days if d < last_day]
    q_all = load_quotes(days[0], days[-1] + "~")
    q_all = q_all[q_all.ticker.isin(outcome.index)]
    print(f"{len(q_all):,} hourly quotes on {q_all.ticker.nunique():,} resolved markets, "
          f"{days[0]} .. {days[-1]}; taker/maker sizes {a.size}")

    for H in a.horizon:
        rows = []
        for d in days:
            day = pd.Timestamp(d, tz="UTC")
            q = q_all[(q_all.observed_at >= day) & (q_all.observed_at < day + timedelta(days=1))]
            q = q[q.close_time > q.observed_at + timedelta(minutes=H + 5)]
            q = q[q.close_time <= q.observed_at + timedelta(hours=72)]
            if q.empty:
                continue
            tape = load_tape(day, int(math.ceil(H / 60)), set(q.ticker))
            sim = simulate(q, tape, H, a.size)
            rows.append(pd.concat([q, sim], axis=1))
            print(f"  H={H}: {d} {len(q):,} quotes, filled yes {sim.fill_yes.mean():.1%} "
                  f"no {sim.fill_no.mean():.1%}", flush=True)
        r = pd.concat(rows, ignore_index=True)
        r["y"] = r.ticker.map(outcome).values
        r["cat"] = r.series_ticker.map(category)
        r["cluster"] = r.series_ticker + r.close_time.dt.strftime("%Y%m%d%H")

        n = a.size
        yes, no = r[r.fill_yes].copy(), r[r.fill_no].copy()
        yes["px"], no["px"] = yes.bid / 100, 1 - no.ask / 100
        yes["win"], no["win"] = yes.y, 1 - no.y
        yes["minutes"], no["minutes"] = yes.min_yes, no.min_no
        # fair = the book midpoint at the quote, as a probability of THIS side.
        yes["fair"] = (yes.bid + yes.ask) / 200
        no["fair"] = 1 - (no.bid + no.ask) / 200
        both = pd.concat([yes.assign(side="buy yes @ bid"), no.assign(side="buy no @ 1-ask")])
        mf = np.array([maker_fee(p, n) for p in both.px.values])
        both["nofee_pnl"] = both.win - both.px
        both["maker_pnl"] = both.nofee_pnl - mf
        both["adverse"] = both.win - both.fair            # what the fill actually earned vs the mid

        # The same quote crossed instead (taker), and the mid itself, for context.
        cross = pd.concat([
            r.assign(px=r.ask / 100, win=r.y), r.assign(px=1 - r.bid / 100, win=1 - r.y)])
        tf = np.array([taker_fee(p, n) for p in cross.px.values])
        cross["pnl"] = cross.win - cross.px - tf
        print(f"\n===== resting {H} min, {n} contracts, hold to settlement =====")
        print(f"  CONTEXT crossing the spread at the same quotes, both sides: "
              f"{cross.pnl.mean():+.4f} per contract over {len(cross):,} quotes")
        print(f"  fill rate: buy-yes {r.fill_yes.mean():.1%}, buy-no {r.fill_no.mean():.1%}")
        print(report(both, "ALL fills"))
        for s, g in both.groupby("side"):
            print(report(g, s))
        for c, g in both.groupby("cat"):
            print(report(g, f"cat={c}"))
        for c, g in r.assign(fr=(r.fill_yes | r.fill_no)).groupby("cat"):
            print(f"    fill rate cat={c:24s} {g.fr.mean():6.1%} of {len(g):,}")
        spread = (both.ask - both.bid)
        for lo_, hi_, nm in ((1, 1, "1c"), (2, 2, "2c"), (3, 99, ">=3c")):
            g = both[(spread >= lo_) & (spread <= hi_)]
            print(report(g, f"spread {nm}"))
        half = both.observed_at.quantile(0.5)
        print(report(both[both.observed_at < half], "first half by date"))
        print(report(both[both.observed_at >= half], "second half by date"))
        # Does being filled quickly mean being wrong? (adverse selection by speed)
        for lo_, hi_ in ((0, 2), (2, 10), (10, 10_000)):
            g = both[(both.minutes >= lo_) & (both.minutes < hi_)]
            print(report(g, f"filled in {lo_}-{hi_} min"))


if __name__ == "__main__":
    main()
