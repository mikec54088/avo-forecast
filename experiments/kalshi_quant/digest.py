"""Dataset statistics computed ONCE and handed to every generating agent.

Measured 2026-09-11 on the one generation-4 invocation that completed before the
account's rolling window ran out: 87 assistant turns, 43 tool calls, 36 of them
Bash, 27 minutes, ~1 MB of accumulated context -- for ONE candidate. The prompt
told it to "measure on the data", so it loaded and characterised ~102k
observations from scratch. Eight agents would have done that same work eight
times, each rediscovering the same dataset shape before getting to its own idea.

So the shape is computed here, once per run, and pasted into the prompt. Two
gains, and the second matters more than the cost:

  - the expensive half of an invocation disappears;
  - every candidate's claims become mutually comparable, because they are
    measured against the same numbers rather than each agent's own ad-hoc
    pandas. Generation 3 quoted edges of 6-12 points computed privately and
    inconsistently, and none of them could be checked against another's.

This is DESCRIPTION, not fitness. Nothing here is scored and nothing selects on
it; INVARIANT #2 is untouched. It is also all in-sample by construction -- it
describes observations that have already resolved -- which is exactly why the
prompt must keep telling agents that a number measured here is a hypothesis,
not a result.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Sequence

from experiments.kalshi_quant.observations import DATA_ROOT, Entry
from experiments.kalshi_quant.scoring import FEE_PER_CONTRACT_CENTS, simulate_fill

CACHE = DATA_ROOT / "digest.json"
BANDS = [(0.0, 0.05), (0.05, 0.20), (0.20, 0.35), (0.35, 0.50),
         (0.50, 0.65), (0.65, 0.80), (0.80, 0.95), (0.95, 1.0)]


def _pcts(xs: Sequence[float], qs=(0.1, 0.5, 0.9)) -> list[float]:
    if not xs:
        return [float("nan")] * len(qs)
    s = sorted(xs)
    return [s[min(len(s) - 1, int(q * len(s)))] for q in qs]


def _naive_pnl(entries: Sequence[Entry], shift: float) -> tuple[float, int]:
    """P&L of nudging every market in this set by `shift` toward its outcome
    side. Makes the cost hurdle concrete instead of asserted."""
    pnl = []
    for e in entries:
        mid = e.market.implied_prob
        side = "yes" if shift > 0 else "no"
        f = simulate_fill(e.market, side, 100)
        if not f.filled:
            continue
        price = f.price_cents / 100.0
        won = (e.outcome == 1) if side == "yes" else (e.outcome == 0)
        pnl.append((1.0 - price) if won else -price)
    return (sum(pnl) / len(pnl) if pnl else float("nan")), len(pnl)


@dataclass(frozen=True)
class Digest:
    text: str
    n_observations: int

    def to_json(self) -> str:
        return json.dumps({"text": self.text, "n_observations": self.n_observations})


def build(entries: Sequence[Entry]) -> Digest:
    n = len(entries)
    series = Counter(e.market.series_ticker for e in entries)
    mkt_brier = sum((e.market.implied_prob - e.outcome) ** 2 for e in entries) / n
    L: list[str] = []
    a = L.append

    a(f"DATASET, measured over all {n:,} resolved observations in "
      f"{len(series)} series. In-sample by construction: INVARIANT #1 will "
      f"exclude every one of them from your candidate's score, so treat any "
      f"number here as a hypothesis to test forward, never as a result.")
    a(f"  market Brier {mkt_brier:.4f}   outcome base rate "
      f"{sum(e.outcome for e in entries) / n:.3f}")
    top20 = sum(v for _, v in series.most_common(20)) / n
    a(f"  top 20 series are {top20:.0%} of observations -- this is why every "
      f"interval in this project is series-clustered")
    a("")

    a("PRICE BANDS. actual-mid is how far the outcome sat above the market's "
      "own price; it is the whole favourite-longshot story and it is already "
      "spent. P&L is what a naive 'buy this band' would have returned after "
      f"crossing the spread and paying {FEE_PER_CONTRACT_CENTS:.0f}c:")
    a(f"  {'band':<14}{'n':>8}{'actual-mid':>12}{'mkt Brier':>11}{'naive P&L':>11}{'fills':>8}")
    for lo, hi in BANDS:
        es = [e for e in entries if lo <= e.market.implied_prob < hi]
        if len(es) < 50:
            continue
        bias = sum(e.outcome - e.market.implied_prob for e in es) / len(es)
        mb = sum((e.market.implied_prob - e.outcome) ** 2 for e in es) / len(es)
        pl, nf = _naive_pnl(es, +1)
        a(f"  {f'{lo:.2f}-{hi:.2f}':<14}{len(es):>8,}{bias:>+12.4f}{mb:>11.4f}"
          f"{pl:>+11.4f}{nf:>8,}")
    a("")

    depth = [min(e.market.yes_bid_size or 0, e.market.yes_ask_size or 0) for e in entries]
    spread = [e.market.spread for e in entries]
    horizon = [(e.market.close_time - e.market.observed_at).total_seconds() / 3600
               for e in entries]
    hist = [len(e.price_history) for e in entries]
    sibs = [len(e.siblings) for e in entries]
    stale = [e.staleness_minutes for e in entries]

    a("FIELD COVERAGE AND SHAPE (p10 / median / p90):")
    for label, xs, fmt in [
        ("top-of-book depth (min side)", depth, "{:.0f}"),
        ("spread", spread, "{:.3f}"),
        ("hours to close", horizon, "{:.1f}"),
        ("price_history points", hist, "{:.0f}"),
        ("quoted siblings", sibs, "{:.0f}"),
        ("entry staleness (min)", stale, "{:.0f}"),
    ]:
        p10, p50, p90 = _pcts(xs)
        a(f"  {label:<30}" + " / ".join(fmt.format(v) for v in (p10, p50, p90)))
    a(f"  observations with >=2 quoted siblings: "
      f"{sum(1 for s in sibs if s >= 2) / n:.0%}")
    a(f"  observations with >=8 price_history points: "
      f"{sum(1 for h in hist if h >= 8) / n:.0%}")
    a("")

    a("SIBLING SUMS, for event-coherence ideas. Legs of one event that are "
      "mutually exclusive should sum near 1.0; nested ladders (over 1.5 / 2.5 "
      "/ 3.5) correctly sum ABOVE it, so check before assuming:")
    by_event: dict[str, list[Entry]] = defaultdict(list)
    for e in entries:
        by_event[e.market.event_ticker].append(e)
    sums = []
    for es in by_event.values():
        if len(es) >= 2:
            sums.append(sum(x.market.implied_prob for x in es))
    if sums:
        p10, p50, p90 = _pcts(sums)
        a(f"  {len(sums):,} multi-leg events; sum of leg prices "
          f"p10/median/p90 = {p10:.2f} / {p50:.2f} / {p90:.2f}")
        a(f"  events summing above 1.05: {sum(1 for s in sums if s > 1.05) / len(sums):.0%}"
          f"   below 0.95: {sum(1 for s in sums if s < 0.95) / len(sums):.0%}")
    a("")

    a("WEEKEND EFFECT, because it has fooled this project twice. Entry "
      "staleness by day of week; staler entries inflate Brier skill without "
      "inflating P&L, so a candidate whose skill concentrates here has found "
      "the artifact, not an edge:")
    by_dow: dict[int, list[float]] = defaultdict(list)
    for e in entries:
        by_dow[e.resolved_at.weekday()].append(e.staleness_minutes)
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    a("  " + "  ".join(
        f"{names[d]} {statistics.median(v):.0f}m" for d, v in sorted(by_dow.items()) if v))
    return Digest("\n".join(L), n)


def load_or_build(entries: Sequence[Entry] | None = None) -> Digest | None:
    """Cached digest, rebuilt when the observation count has moved materially.

    Run artifact, never committed (INVARIANT #5).
    """
    cached = None
    if CACHE.exists():
        try:
            d = json.loads(CACHE.read_text())
            cached = Digest(d["text"], int(d["n_observations"]))
        except (OSError, ValueError, KeyError):
            cached = None
    if entries is None:
        return cached
    if cached and abs(len(entries) - cached.n_observations) < 0.02 * cached.n_observations:
        return cached
    fresh = build(entries)
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(fresh.to_json())
    except OSError:
        pass
    return fresh
