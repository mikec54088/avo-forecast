"""The four controls. A control that silently breaks is worse than no control,
so their defining properties are asserted rather than assumed.

No network: these exercise pure functions over synthetic prices.
"""
from __future__ import annotations

import importlib

import pytest

from avo.core import registry

CONTROLS = [
    "baseline_market",
    "baseline_base_rate",
    "baseline_shrunk",
    "baseline_sharpened",
]
PROBS = [0.001, 0.02, 0.2, 0.4, 0.5, 0.6, 0.8, 0.98, 0.999]


class FakeMarket:
    """Only what a control may look at."""

    def __init__(self, p: float, series_ticker: str = "KXTEST") -> None:
        self.implied_prob = p
        self.series_ticker = series_ticker


class FakeContext:
    def __init__(self) -> None:
        self.series_history: dict[str, list] = {}


def _mod(name: str):
    return importlib.import_module(f"experiments.kalshi_quant.candidates.{name}")


@pytest.mark.parametrize("name", CONTROLS)
def test_control_is_discoverable_and_conforms(name):
    seeds = {c.candidate_id for c in registry.load("kalshi_quant").seed_candidates()}
    assert name in seeds
    m = _mod(name)
    assert callable(m.forecast)
    assert m.MANIFEST["candidate_id"] == name


@pytest.mark.parametrize("name", CONTROLS)
def test_control_returns_a_probability(name):
    m = _mod(name)
    for p in PROBS:
        out = m.forecast(FakeMarket(p), FakeContext())
        assert 0.0 <= out <= 1.0, f"{name} returned {out} for {p}"


def test_controls_share_created_at():
    """INVARIANT #1 scores each candidate only on truth resolving after its
    created_at. Controls with different stamps would be scored on different
    observation sets and stop being comparable, which is their entire purpose."""
    seeds = {c.candidate_id: c for c in registry.load("kalshi_quant").seed_candidates()}
    stamps = {seeds[n].created_at for n in CONTROLS}
    assert len(stamps) == 1, f"controls disagree on created_at: {stamps}"


def test_market_control_is_the_identity():
    """If this is ever not exact, skill is not 0.000 by construction and the
    scorer's zero point has moved."""
    m = _mod("baseline_market")
    for p in PROBS:
        assert m.forecast(FakeMarket(p), FakeContext()) == p


def test_shrunk_and_sharpened_are_mirrors():
    """They must move in opposite directions, or the control set is asymmetric
    again — which is the flaw baseline_sharpened was added to catch."""
    shrunk, sharp = _mod("baseline_shrunk"), _mod("baseline_sharpened")
    ctx = FakeContext()
    for p in PROBS:
        if p == 0.5:
            continue
        s, k = shrunk.forecast(FakeMarket(p), ctx), sharp.forecast(FakeMarket(p), ctx)
        if p < 0.5:
            assert s > p, f"shrunk should raise {p} toward 0.5, got {s}"
            assert k < p, f"sharpened should lower {p} away from 0.5, got {k}"
        else:
            assert s < p, f"shrunk should lower {p} toward 0.5, got {s}"
            assert k > p, f"sharpened should raise {p} away from 0.5, got {k}"


@pytest.mark.parametrize("name", ["baseline_shrunk", "baseline_sharpened"])
def test_half_is_a_fixed_point(name):
    assert _mod(name).forecast(FakeMarket(0.5), FakeContext()) == pytest.approx(0.5)


def test_sharpened_is_monotone():
    """A non-monotone transform would reorder markets and stop being a pure
    sharpening of the market's own ranking."""
    sharp = _mod("baseline_sharpened")
    ctx = FakeContext()
    out = [sharp.forecast(FakeMarket(p), ctx) for p in PROBS]
    assert out == sorted(out)
