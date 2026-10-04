"""Does Kalshi's crypto book lag the coin it settles on? (2026-10-04)

Run:  uv run python scripts/crypto_fair_value.py --fetch     # backfill Coinbase minutes
      uv run python scripts/crypto_fair_value.py             # measure

A MEASUREMENT, not a candidate: nothing here touches the candidate contract,
the fitness or the pipeline. It answers one question before anything is built
on it: if a strategy could see the coin's spot price, would trading the gap
between a fair value and Kalshi's book beat the ~2c cost of trading, on honest
entries?

Every quant candidate so far saw only Kalshi's own prices and all 52 lose about
the cost of trading. These markets settle on an OUTSIDE number (CF Benchmarks'
real-time index, a 60s average at close; Coinbase is the proxy here), so a book
that is slow to follow spot is a mechanism, not a mined pattern.

Fair value: zero-drift lognormal, P(S_T > K) = Phi(ln(S/K) / (sigma sqrt(tau))),
sigma from trailing realised 1-minute volatility. Deliberately naive; if this
cannot find room, nothing fancier is likely to on 15-minute snapshots.

No-lookahead rules (INVARIANT #8 in spirit):
  * A snapshot's observed_at is stamped AFTER Kalshi's reply arrives
    (client.iter_market_pages), so the quote is no newer than observed_at.
  * Spot is the close of the last 1-minute candle that ENDED at least `lag`
    seconds before observed_at -- always older than the quote, which biases
    AGAINST the model (the book may already know a move we do not).
  * Volatility uses only candles up to that same spot candle.
  * Entry is the FIRST snapshot within 72h of close where the edge clears the
    threshold, as `first_signal_within_72h_of_close` does. Never the best one.
  * P&L settles on Kalshi's own recorded outcome, not on the proxy.
A deliberate LEAK CANARY (spot taken 5 min AFTER the quote) is printed last:
it shows what a leak looks like here, so an honest number near it is a red flag.

Costs: cross the spread (buy YES at ask, NO at 1-bid), Kalshi taker fee on 100
contracts, spread <= 8c and >= 4 contracts visible -- simulate_fill's gates.
Intervals resample whole settlement hours: every strike and every coin closing
in the same hour moves together.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pyarrow.dataset as ds

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experiments.kalshi_quant.scoring import trading_fee_dollars

ROOT = Path(__file__).resolve().parents[1]
SNAPS = ROOT / "data/kalshi_quant/snapshots"
RESOLUTIONS = ROOT / "data/kalshi_quant/resolutions"
SPOT_DIR = ROOT / "data/external/coinbase"

COINS = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD", "XRP": "XRP-USD",
         "DOGE": "DOGE-USD"}
# series -> (coin, kind). "above": T<strike>, YES if settle > strike.
# "range": B<centre>, YES if settle inside the bucket. "updown": YES if the
# close is at or above the price at the window's open. Each mapping is checked
# against Kalshi's recorded outcomes before anything is measured.
SERIES = {
    "KXBTCD": ("BTC", "above"), "KXETHD": ("ETH", "above"), "KXSOLD": ("SOL", "above"),
    "KXXRPD": ("XRP", "above"), "KXDOGED": ("DOGE", "above"),
    "KXBTC": ("BTC", "range"), "KXETH": ("ETH", "range"), "KXSOLE": ("SOL", "range"),
    "KXXRP": ("XRP", "range"), "KXDOGE": ("DOGE", "range"),
    "KXBTC15M": ("BTC", "updown"), "KXETH15M": ("ETH", "updown"),
    "KXSOL15M": ("SOL", "updown"), "KXXRP15M": ("XRP", "updown"),
    "KXDOGE15M": ("DOGE", "updown"),
}
CONTRACTS = 100
MAX_SPREAD = 0.08
WINDOW = timedelta(hours=72)
VOL_WINDOWS = {"6h": 360, "24h": 1440}


# --------------------------------------------------------------------- spot

def fetch(start: datetime) -> None:
    """Backfill 1-minute Coinbase candles, resumable. Public, no key."""
    SPOT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    for coin, product in COINS.items():
        path = SPOT_DIR / f"{coin}.parquet"
        old = pd.read_parquet(path) if path.exists() else pd.DataFrame()
        t0 = (old.t.max().to_pydatetime() + timedelta(minutes=1)) if len(old) else start
        rows: list[list[float]] = []
        n = 0
        while t0 < now:
            t1 = min(t0 + timedelta(minutes=300), now)
            for attempt in range(5):
                r = httpx.get(
                    f"https://api.exchange.coinbase.com/products/{product}/candles",
                    params={"granularity": 60, "start": t0.isoformat(), "end": t1.isoformat()},
                    timeout=20)
                if r.status_code == 200:
                    break
                time.sleep(1 + attempt * 2)
            r.raise_for_status()
            rows.extend(r.json())
            n += 1
            t0 = t1
            time.sleep(0.12)
        new = pd.DataFrame(rows, columns=["ts", "low", "high", "open", "close", "volume"])
        new["t"] = pd.to_datetime(new.ts, unit="s", utc=True)
        df = (pd.concat([old, new[["t", "open", "close", "volume"]]])
              .drop_duplicates("t").sort_values("t"))
        df.to_parquet(path, index=False)
        print(f"  {coin}: {n} requests, {len(new):,} new candles, {len(df):,} total "
              f"({df.t.min()} .. {df.t.max()})", flush=True)


def load_spot(coin: str) -> pd.DataFrame:
    """Full minute grid (candle START times), forward-filled, with trailing vol.

    Coinbase omits minutes with no trades; those are carried forward so a
    lookup never silently reaches for a later candle.
    """
    df = pd.read_parquet(SPOT_DIR / f"{coin}.parquet").set_index("t").sort_index()
    grid = pd.date_range(df.index.min(), df.index.max(), freq="1min", tz="UTC")
    df = df.reindex(grid)
    df["close"] = df.close.ffill()
    r = np.log(df.close).diff()
    for name, w in VOL_WINDOWS.items():
        df[f"vol_{name}"] = r.rolling(w, min_periods=w // 2).std()
    return df[["close", *[f"vol_{n}" for n in VOL_WINDOWS]]]


def spot_at(spot: pd.DataFrame, when: pd.Series, lag_s: int) -> pd.DataFrame:
    """Spot and vol as knowable `lag_s` seconds before `when` (negative = LEAK).

    Candle START s covers [s, s+60) and its close is known at s+60, so the
    last candle known at instant x starts at floor(x) - 60s.
    """
    x = (when - pd.Timedelta(seconds=lag_s)).dt.floor("1min") - pd.Timedelta(minutes=1)
    out = spot.reindex(pd.DatetimeIndex(x))
    out.index = when.index
    return out


# ------------------------------------------------------------------ markets

def load_quotes() -> pd.DataFrame:
    cols = ["ticker", "event_ticker", "series_ticker", "observed_at", "close_time",
            "yes_bid", "yes_ask", "yes_bid_size", "yes_ask_size"]
    d = ds.dataset(SNAPS, format="parquet", partitioning="hive")
    t = d.to_table(columns=cols, filter=ds.field("series_ticker").isin(list(SERIES)))
    q = t.to_pandas().drop_duplicates(["ticker", "observed_at"])
    for c in ("observed_at", "close_time"):
        q[c] = pd.to_datetime(q[c], utc=True)
    return q


def load_outcomes() -> pd.Series:
    parts = []
    for f in RESOLUTIONS.rglob("*.parquet"):
        r = pd.read_parquet(f)
        if "outcome" in r:
            parts.append(r[["ticker", "outcome"]])
    r = pd.concat(parts).dropna().drop_duplicates("ticker")
    return r.set_index("ticker").outcome.astype(int)


def parse_strikes(q: pd.DataFrame) -> pd.DataFrame:
    """Add coin, kind, lo, hi (settle in [lo, hi) => YES) from the ticker."""
    q = q.copy()
    q["coin"] = q.series_ticker.map(lambda s: SERIES[s][0])
    q["kind"] = q.series_ticker.map(lambda s: SERIES[s][1])
    leg = q.ticker.str.rsplit("-", n=1).str[-1]
    num = pd.to_numeric(leg.str[1:], errors="coerce")
    q["lo"], q["hi"] = np.nan, np.nan

    above = (q.kind == "above") & leg.str.startswith("T")
    q.loc[above, "lo"], q.loc[above, "hi"] = num[above], np.inf

    rng = (q.kind == "range") & leg.str.startswith("B")
    # Bucket width = spacing of adjacent centres within the event.
    centres = (q[rng].assign(c=num[rng]).groupby("event_ticker").c
               .apply(lambda s: np.median(np.diff(np.sort(s.unique())))
                      if s.nunique() > 1 else np.nan))
    w = q.loc[rng, "event_ticker"].map(centres)
    q.loc[rng, "lo"], q.loc[rng, "hi"] = num[rng] - w / 2, num[rng] + w / 2

    # up/down: the strike is the price at the window's open, filled in later.
    q.loc[q.kind == "updown", "hi"] = np.inf
    return q[(q.kind == "updown") | q.lo.notna()]


def norm_cdf(x: np.ndarray) -> np.ndarray:
    """Standard normal CDF via Abramowitz-Stegun 7.1.26 (|error| < 1.5e-7);
    avoids a scipy dependency for one function."""
    z = np.abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * z)
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741
                + t * (-1.453152027 + t * 1.061405429))))
    erf = 1.0 - poly * np.exp(-z * z)
    return 0.5 * (1.0 + np.sign(x) * erf)


def fair_value(S: np.ndarray, lo: np.ndarray, hi: np.ndarray,
               vol_min: np.ndarray, tau_min: np.ndarray) -> np.ndarray:
    sd = vol_min * np.sqrt(np.maximum(tau_min, 1e-9))
    with np.errstate(divide="ignore", invalid="ignore"):
        p_lo = norm_cdf(np.log(S / lo) / sd)
        p_hi = np.where(np.isinf(hi), 0.0, norm_cdf(np.log(S / hi) / sd))
    return np.clip(p_lo - p_hi, 0.0, 1.0)


# --------------------------------------------------------------- measuring

FEE_GRID = np.round(np.arange(0, 101) / 100, 2)
FEE_PER = np.array([trading_fee_dollars(p, CONTRACTS) / CONTRACTS for p in FEE_GRID])


def fee(price: np.ndarray) -> np.ndarray:
    return FEE_PER[np.clip(np.round(price * 100).astype(int), 0, 100)]


def clustered_ci(x: np.ndarray, cl: np.ndarray, n: int = 2000) -> tuple[float, float]:
    if len(x) < 2:
        return (math.nan, math.nan)
    codes, inv = np.unique(cl, return_inverse=True)
    sums = np.bincount(inv, weights=x)
    cnts = np.bincount(inv)
    k = len(codes)
    if k < 2:
        return (math.nan, math.nan)
    rng = np.random.default_rng(0)
    idx = rng.integers(0, k, size=(n, k))
    m = sums[idx].sum(1) / cnts[idx].sum(1)
    return float(np.quantile(m, 0.025)), float(np.quantile(m, 0.975))


def prepare(q: pd.DataFrame, spots: dict[str, pd.DataFrame], lag_s: int,
            vol: str) -> pd.DataFrame:
    parts = []
    for coin, g in q.groupby("coin"):
        s = spot_at(spots[coin], g.observed_at, lag_s)
        g = g.assign(S=s.close.values, vol=s[f"vol_{vol}"].values)
        ud = g.kind == "updown"
        if ud.any():
            # Reference = price at the window's open: the close of the minute
            # ENDING at close - 15min, known from the open onward.
            open_t = g.close_time - pd.Timedelta(minutes=15)
            ref = spots[coin].close.reindex(pd.DatetimeIndex(open_t - pd.Timedelta(minutes=1))).values
            g.loc[ud, "lo"] = ref[ud.values]
            # Quotes before the reference is knowable are dropped.
            g = g[~ud | (g.observed_at >= open_t + pd.Timedelta(seconds=max(lag_s, 0)))]
        parts.append(g)
    d = pd.concat(parts)
    d = d[d.S.notna() & d.vol.notna() & d.lo.notna()]
    tau = (d.close_time - d.observed_at).dt.total_seconds() / 60.0
    d = d.assign(tau=tau)
    d = d[(d.tau > 0) & (d.observed_at >= d.close_time - WINDOW)]
    d["fair"] = fair_value(d.S.values, d.lo.values, d.hi.values, d.vol.values, d.tau.values)
    d["mid"] = (d.yes_bid + d.yes_ask) / 2
    return d


def validate(q: pd.DataFrame, spots: dict[str, pd.DataFrame], outcome: pd.Series) -> None:
    """Does the ticker parse + proxy settle reproduce Kalshi's outcomes?"""
    m = q.drop_duplicates("ticker").copy()
    m = m[m.ticker.isin(outcome.index)]
    rows = []
    for coin, g in m.groupby("coin"):
        # Proxy settle: the minute ENDING at close (CF's 60s average window).
        settle = spots[coin].close.reindex(
            pd.DatetimeIndex(g.close_time - pd.Timedelta(minutes=1))).values
        lo = g.lo.values.copy()
        ud = (g.kind == "updown").values
        lo[ud] = spots[coin].close.reindex(
            pd.DatetimeIndex(g.close_time[ud] - pd.Timedelta(minutes=16))).values
        pred = (settle >= lo) & (settle < g.hi.values)
        g = g.assign(pred=pred.astype(int), settle=settle, lo2=lo)
        # Markets settling within 0.1% of a boundary are proxy noise, not parse errors.
        near = (np.abs(settle / lo - 1) < 1e-3) | (np.abs(settle / g.hi.values - 1) < 1e-3)
        rows.append(g.assign(near=near))
    v = pd.concat(rows)
    v["y"] = v.ticker.map(outcome)
    v = v[v.settle.notna() & v.lo2.notna()]
    print("\nPARSE CHECK -- proxy settle vs Kalshi's outcome (away from boundaries)")
    for kind, g in v[~v.near].groupby("kind"):
        print(f"  {kind:7s} {len(g):7,d} markets   agree {np.mean(g.pred == g.y):.2%}")


def trade(d: pd.DataFrame, outcome: pd.Series, theta: float) -> pd.DataFrame:
    """First snapshot per market where the fee-adjusted edge clears theta."""
    d = d[(d.yes_ask - d.yes_bid <= MAX_SPREAD + 1e-9) & d.ticker.isin(outcome.index)]
    buy_yes = d.fair - d.yes_ask - fee(d.yes_ask.values)
    no_price = 1 - d.yes_bid
    buy_no = (1 - d.fair) - no_price - fee(no_price.values)
    side = np.where(buy_yes >= buy_no, "yes", "no")
    edge = np.maximum(buy_yes, buy_no)
    depth = np.where(side == "yes", d.yes_ask_size, d.yes_bid_size)
    ok = (edge > theta) & (np.nan_to_num(depth, nan=1e9) * 0.25 >= 1)
    t = d.assign(side=side, edge=edge)[ok].sort_values("observed_at")
    t = t.drop_duplicates("ticker", keep="first")
    y = t.ticker.map(outcome).values
    price = np.where(t.side == "yes", t.yes_ask, 1 - t.yes_bid)
    win = np.where(t.side == "yes", y == 1, y == 0)
    return t.assign(y=y, price=price, pnl=win.astype(float) - price - fee(price),
                    hour=t.close_time.dt.strftime("%Y-%m-%dT%H"))


def summarise(label: str, t: pd.DataFrame) -> str:
    if len(t) < 2:
        return f"  {label:34s} {len(t):6d} fills"
    lo, hi = clustered_ci(t.pnl.values, t.hour.values)
    return (f"  {label:34s} {len(t):6,d} fills {t.hour.nunique():5,d} hours  "
            f"{t.pnl.mean():+.4f} [{lo:+.4f},{hi:+.4f}]  edge-claimed {t.edge.mean():+.3f}")


def skill(d: pd.DataFrame, outcome: pd.Series) -> None:
    x = d[d.ticker.isin(outcome.index)]
    y = x.ticker.map(outcome).values
    bm, bf = (x.mid - y) ** 2, (x.fair - y) ** 2
    print(f"  Brier skill of fair value vs the mid, all {len(x):,} quotes: "
          f"{1 - bf.sum() / bm.sum():+.4f}   (positive = model closer to the truth)")
    for kind, g in x.assign(bm=bm, bf=bf).groupby("kind"):
        print(f"    {kind:7s} {len(g):9,d}  {1 - g.bf.sum() / g.bm.sum():+.4f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="backfill Coinbase candles first")
    ap.add_argument("--start", default="2026-08-20")
    a = ap.parse_args()
    if a.fetch:
        fetch(datetime.fromisoformat(a.start).replace(tzinfo=timezone.utc))

    spots = {c: load_spot(c) for c in COINS}
    outcome = load_outcomes()
    q = parse_strikes(load_quotes())
    print(f"{len(q):,} crypto quotes on {q.ticker.nunique():,} markets, "
          f"{q.observed_at.min():%Y-%m-%d} .. {q.observed_at.max():%Y-%m-%d}; "
          f"{q.ticker.isin(outcome.index).mean():.0%} of quote rows resolved")
    validate(q, spots, outcome)

    split = q.observed_at.quantile(0.5)
    for vol in VOL_WINDOWS:
        print(f"\n===== vol window {vol}, spot >= 1 min older than the quote =====")
        d = prepare(q, spots, lag_s=60, vol=vol)
        skill(d, outcome)
        for theta in (0.0, 0.02, 0.05, 0.10):
            t = trade(d, outcome, theta)
            print(f" threshold {theta:.2f}")
            print(summarise("all", t))
            print(summarise("first half (by quote date)", t[t.observed_at < split]))
            print(summarise("second half", t[t.observed_at >= split]))
            if theta == 0.02:
                for kind, g in t.groupby("kind"):
                    print(summarise(f"  kind={kind}", g))
                for coin, g in t.groupby("coin"):
                    print(summarise(f"  coin={coin}", g))
                bands = pd.cut(t.tau, [0, 15, 60, 360, 1440, 4320],
                               labels=["<15m", "15-60m", "1-6h", "6-24h", "1-3d"])
                for b, g in t.groupby(bands, observed=True):
                    print(summarise(f"  time-to-close {b}", g))

    print("\n===== latency: same rule, threshold 0.02, vol 6h =====")
    for lag in (60, 300, 900):
        print(summarise(f"spot {lag // 60:2d}+ min older than quote",
                        trade(prepare(q, spots, lag, "6h"), outcome, 0.02)))
    print(summarise("LEAK CANARY: spot 5 min AFTER quote",
                    trade(prepare(q, spots, -300, "6h"), outcome, 0.02)))


if __name__ == "__main__":
    main()
