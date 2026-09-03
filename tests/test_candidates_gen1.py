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
from experiments.kalshi_quant.types import (
    ForecastContext,
    MarketSnapshot,
    PricePoint,
    Resolution,
)

HAND_WRITTEN = [
    "favourite_longshot", "longshot_fade", "favourite_boost", "last_trade_blend",
    "tight_book_only", "volume_weighted", "series_base_rate_blend",
    "book_imbalance_tilt", "early_settle_aware", "ensemble_shoulders",
    "control_cost_of_trading", "control_middle_only",
]

# Promoted from the 2026-09-01 generation trials: one representative per
# distinct idea, out of 25 validated candidates that collapsed to ~7 ideas.
# They are generation 1, not 2 -- three claimed generation 2 because the agent
# saw itself as a descendant of favourite_longshot, but generation 2 means
# seeded by generation 1's SCORES, and no scores existed. parent_id is kept
# where the agent set it; that lineage is real even though the round is not.
GENERATED = [
    "logit_midpoint", "tick_grid_conditioned", "open_interest_shoulders",
    "microprice_fair_value", "spread_scaled_shoulder", "longshot_time_decay",
    "last_trade_outside_book", "last_trade_fade",
]

# Generation 2: the first candidates that can see more than one snapshot.
# ForecastContext gained price_history and siblings on 2026-09-02 (G1).
GEN2 = ["price_momentum", "sibling_coherence"]

GEN1 = HAND_WRITTEN + GENERATED + GEN2
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def M(bid=0.20, ask=0.24, *, last=None, volume=500.0, bid_size=10.0,
      ask_size=10.0, hours_to_close=2.0, ticker="KXT-1", open_interest=None,
      structure="linear_cent"):
    """A real MarketSnapshot, not a stub.

    A hand-rolled stub drifts from the dataclass it imitates: this file used
    one until 2026-09-02, and it lacked has_two_sided_book and open_interest,
    so promoted candidates reading those fields failed here while working
    perfectly against real data. The validation probe had the identical defect
    and was fixed the same way. Build the real type and the class of bug is
    gone.
    """
    return MarketSnapshot(
        ticker=ticker, event_ticker="KXT-EV", series_ticker="KXT", title="probe",
        observed_at=NOW, close_time=NOW + timedelta(hours=hours_to_close),
        yes_bid=bid, yes_ask=ask, last_price=last, volume=volume,
        open_interest=volume if open_interest is None else open_interest,
        yes_bid_size=bid_size, yes_ask_size=ask_size, liquidity=0.0,
        status="active", price_level_structure=structure, is_mve=False,
    )


def Ctx(history=None, *, path=None, siblings=None):
    """A real ForecastContext, not a stub.

    Hand-rolled stubs drift from the dataclass they imitate. This file used one
    for the market (fixed 2026-09-02, it lacked has_two_sided_book and
    open_interest) and another for the context (fixed the same day, it lacked
    price_history and siblings). Both times candidates that worked against real
    data failed here. Build the real types and the class of bug is gone.
    """
    return ForecastContext(
        now=NOW,
        series_history=history or {},
        price_history=list(path or []),
        siblings=list(siblings or []),
    )


def _rising_path():
    return [PricePoint(NOW - timedelta(minutes=15 * (6 - i)),
                       0.20 + 0.06 * i, 0.24 + 0.06 * i, 100.0, 50.0)
            for i in range(6)]


def _falling_path():
    return [PricePoint(NOW - timedelta(minutes=15 * (6 - i)),
                       0.95 - 0.06 * i, 0.99 - 0.06 * i, 100.0, 50.0)
            for i in range(6)]


def _ctx_with_path():
    """A rising price path -- what price_momentum reads."""
    return Ctx(path=_rising_path())


def _ctx_with_siblings():
    """Event legs summing well above 1.0 -- what sibling_coherence reads."""
    return Ctx(siblings=[M(bid=0.40, ask=0.44, ticker="KXT-2"),
                         M(bid=0.30, ask=0.34, ticker="KXT-3")])


def _mod(name):
    return importlib.import_module(f"experiments.kalshi_quant.candidates.{name}")


@pytest.mark.parametrize("name", GEN1)
def test_registered_with_matching_manifest(name):
    seeds = {c.candidate_id: c for c in registry.load("kalshi_quant").seed_candidates()}
    assert name in seeds
    assert seeds[name].generation >= 1, "must not claim generation 0"
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
    hist = [Resolution(f"x{i}", NOW - timedelta(hours=i + 1), 1) for i in range(80)]
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
        (M(bid=0.60, ask=0.64), _ctx_with_path()),
        (M(bid=0.30, ask=0.34), _ctx_with_siblings()),
    ]
    assert any(abs(f(m, c) - m.implied_prob) > 1e-9 for m, c in probes), (
        f"{name} never deviates from the market price"
    )


def test_last_trade_pair_predicts_opposite_directions():
    """last_trade_outside_book and last_trade_fade were promoted together on
    purpose: given the same outside print they disagree about which way to
    lean. One says the quote is stale so follow the shoulder, the other says
    the print is the older price so fade it. They cannot both be right, which
    is worth more than either alone -- the same structure as the
    longshot_fade / favourite_boost partition."""
    a = _mod("last_trade_outside_book").forecast
    b = _mod("last_trade_fade").forecast
    disagreed = False
    for bid, ask, last in [(0.20, 0.24, 0.05), (0.20, 0.24, 0.95),
                           (0.70, 0.74, 0.55), (0.70, 0.74, 0.95),
                           (0.10, 0.13, 0.02), (0.85, 0.89, 0.99)]:
        m = M(bid=bid, ask=ask, last=last)
        da = a(m, Ctx()) - m.implied_prob
        db = b(m, Ctx()) - m.implied_prob
        if da * db < 0:
            disagreed = True
    assert disagreed, "the pair never disagrees; it is not a falsification pair"


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
    hist = [Resolution(f"x{i}", NOW - timedelta(hours=i + 1), 1) for i in range(200)]
    assert f(m, Ctx({"KXT": hist})) > empty, "history did not move the forecast"


def test_price_momentum_reads_the_path_not_just_the_quote():
    """The whole reason price_history was added. Identical current quote, two
    different histories, must give two different forecasts -- and it must lean
    with the drift, not against it."""
    f = _mod("price_momentum").forecast
    m = M(bid=0.60, ask=0.64)
    rising, falling = Ctx(path=_rising_path()), Ctx(path=_falling_path())
    assert f(m, rising) > m.implied_prob, "did not lean with an upward drift"
    assert f(m, falling) < m.implied_prob, "did not lean with a downward drift"
    assert f(m, Ctx()) == pytest.approx(m.implied_prob), "acted with no history"


def test_sibling_coherence_reads_the_event_not_just_the_leg():
    """Legs summing above 1.0 mean the event is collectively overpriced, so
    this leg probably is too. Abstains below MIN_SIBLINGS, because a partial
    set sums low for a boring reason."""
    f = _mod("sibling_coherence").forecast
    m = M(bid=0.30, ask=0.34)
    over = Ctx(siblings=[M(bid=0.40, ask=0.44, ticker="a"),
                         M(bid=0.30, ask=0.34, ticker="b")])
    under = Ctx(siblings=[M(bid=0.20, ask=0.24, ticker="a"),
                          M(bid=0.20, ask=0.24, ticker="b")])
    assert f(m, over) < m.implied_prob, "did not fade an over-priced event"
    assert f(m, under) > m.implied_prob, "did not lift an under-priced event"
    one = Ctx(siblings=[M(bid=0.40, ask=0.44, ticker="a")])
    assert f(m, one) == pytest.approx(m.implied_prob), "acted on a partial leg set"
