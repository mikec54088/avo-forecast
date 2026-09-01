"""Generation 1 candidates. Properties, not scores.

Scores come from the scorer on out-of-sample data; what these assert is that
each candidate is well-formed, deterministic, and actually does what its
docstring claims. A candidate that silently returns the market price is not
a strategy, and a candidate that returns 1.4 crashes a generation.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import pytest

from avo.core import registry

GEN1 = [
    "favourite_longshot", "longshot_fade", "favourite_boost", "last_trade_blend",
    "tight_book_only", "volume_weighted", "series_base_rate_blend",
    "book_imbalance_tilt", "early_settle_aware", "ensemble_shoulders",
    "control_cost_of_trading", "control_middle_only",
]
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


class M:
    """A market a candidate may look at."""

    def __init__(self, bid=0.20, ask=0.24, *, last=None, volume=500.0,
                 bid_size=10.0, ask_size=10.0, hours_to_close=2.0, ticker="KXT-1"):
        self.ticker = ticker
        self.series_ticker = "KXT"
        self.yes_bid, self.yes_ask = bid, ask
        self.last_price = last
        self.volume = volume
        self.yes_bid_size, self.yes_ask_size = bid_size, ask_size
        self.close_time = NOW + timedelta(hours=hours_to_close)

    @property
    def implied_prob(self):
        return (self.yes_bid + self.yes_ask) / 2.0

    @property
    def spread(self):
        return self.yes_ask - self.yes_bid


class Ctx:
    def __init__(self, history=None):
        self.now = NOW
        self.series_history = history or {}


def _mod(name):
    return importlib.import_module(f"experiments.kalshi_quant.candidates.{name}")


@pytest.mark.parametrize("name", GEN1)
def test_registered_with_matching_manifest(name):
    seeds = {c.candidate_id: c for c in registry.load("kalshi_quant").seed_candidates()}
    assert name in seeds
    assert seeds[name].generation == 1, "gen1 candidates must not claim generation 0"
    assert _mod(name).MANIFEST["candidate_id"] == name


@pytest.mark.parametrize("name", GEN1)
def test_returns_a_probability_over_the_whole_price_range(name):
    """Includes the boundaries, where an additive shift would escape [0, 1]."""
    f = _mod(name).forecast
    for bid in [0.0005, 0.01, 0.05, 0.2, 0.5, 0.8, 0.95, 0.99]:
        m = M(bid=bid, ask=min(bid + 0.02, 0.999), last=0.5)
        out = f(m, Ctx())
        assert 0.0 < out < 1.0, f"{name} returned {out} at mid {m.implied_prob}"


@pytest.mark.parametrize("name", GEN1)
def test_is_deterministic(name):
    """A rescore must reproduce. control_cost_of_trading hashes rather than
    randomises for exactly this reason."""
    f = _mod(name).forecast
    m, c = M(last=0.3), Ctx()
    assert f(m, c) == f(m, c)


@pytest.mark.parametrize("name", GEN1)
def test_actually_deviates_from_the_market_somewhere(name):
    """A candidate that always returns implied_prob is baseline_market wearing
    a different name -- it would take no position and score exactly 0."""
    f = _mod(name).forecast
    hist = [type("R", (), {"outcome": 1, "ticker": "x"})() for _ in range(80)]
    probes = [
        (M(bid=0.02, ask=0.04), Ctx()),
        (M(bid=0.20, ask=0.24), Ctx()),
        (M(bid=0.48, ask=0.52), Ctx()),
        (M(bid=0.74, ask=0.78), Ctx()),
        (M(bid=0.96, ask=0.98), Ctx()),
        (M(bid=0.20, ask=0.24, last=0.05), Ctx()),
        (M(bid=0.20, ask=0.24, bid_size=90.0, ask_size=1.0), Ctx()),
        (M(bid=0.20, ask=0.24, hours_to_close=72.0), Ctx()),
        (M(bid=0.20, ask=0.24), Ctx({"KXT": hist})),
    ]
    assert any(abs(f(m, c) - m.implied_prob) > 1e-9 for m, c in probes), (
        f"{name} never deviates from the market price"
    )


def test_shoulder_family_leaves_the_calibrated_middle_alone():
    """The measured gap in 0.35-0.65 was 0.0 pts. Trading it pays the spread
    for nothing, so these three must decline it."""
    mid = M(bid=0.49, ask=0.51)
    for name in ("favourite_longshot", "tight_book_only", "ensemble_shoulders"):
        assert _mod(name).forecast(mid, Ctx()) == pytest.approx(mid.implied_prob)


def test_longshot_fade_and_favourite_boost_partition_favourite_longshot():
    """Together they must touch exactly what favourite_longshot touches, and
    neither may touch the other's half."""
    fl, lo, hi = (_mod(n).forecast for n in
                  ("favourite_longshot", "longshot_fade", "favourite_boost"))
    for bid in [0.01, 0.10, 0.25, 0.45, 0.55, 0.70, 0.90, 0.97]:
        m = M(bid=bid, ask=min(bid + 0.02, 0.999))
        p = m.implied_prob
        halves = {lo(m, Ctx()), hi(m, Ctx())}
        assert fl(m, Ctx()) in halves, f"mismatch at mid {p}"
        assert lo(m, Ctx()) == p or hi(m, Ctx()) == p, f"both halves moved at {p}"


def test_tight_book_only_declines_wide_books():
    wide = M(bid=0.20, ask=0.40)
    assert _mod("tight_book_only").forecast(wide, Ctx()) == pytest.approx(wide.implied_prob)


def test_control_middle_only_is_the_inverse_of_the_shoulder_family():
    """It must move the middle and leave the shoulders alone -- the exact
    opposite of favourite_longshot. That is what makes it falsifiable."""
    f = _mod("control_middle_only").forecast
    middle = M(bid=0.55, ask=0.57)
    assert f(middle, Ctx()) != pytest.approx(middle.implied_prob)
    for bid in (0.10, 0.80):
        shoulder = M(bid=bid, ask=bid + 0.02)
        assert f(shoulder, Ctx()) == pytest.approx(shoulder.implied_prob)


def test_control_cost_of_trading_carries_no_market_information():
    """Its output must depend on the ticker and nothing else about the market.
    That is what makes positive skill from it proof of a harness leak."""
    f = _mod("control_cost_of_trading").forecast
    a = M(ticker="SAME", bid=0.20, ask=0.22, volume=1.0, last=0.9)
    b = M(ticker="SAME", bid=0.20, ask=0.22, volume=99999.0, last=0.1,
          bid_size=500.0, ask_size=1.0, hours_to_close=99.0)
    assert f(a, Ctx()) == f(b, Ctx())
    # ...and different tickers must not all move the same way.
    dirs = {f(M(ticker=f"T{i}"), Ctx()) > M(ticker=f"T{i}").implied_prob
            for i in range(20)}
    assert dirs == {True, False}, "perturbation direction is not varying"


def test_series_base_rate_blend_is_the_only_one_using_context():
    """If it ignores history it is not testing what it claims to test."""
    f = _mod("series_base_rate_blend").forecast
    m = M(bid=0.20, ask=0.24)
    empty = f(m, Ctx())
    hist = [type("R", (), {"outcome": 1, "ticker": f"x{i}"})() for i in range(200)]
    assert f(m, Ctx({"KXT": hist})) > empty, "history did not move the forecast"
