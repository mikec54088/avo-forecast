"""The research track's control. A control that silently breaks is worse than
no control, and this one exists to decide whether kalshi_research's acted set
means anything, so its gate is asserted rather than assumed.

No network: pure functions over synthetic markets.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from experiments.kalshi_quant.candidates import control_news_fade_band as C
from experiments.kalshi_quant.types import ForecastContext, MarketSnapshot

GAME_DAY = datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc)
TICKER = "KXMLBGAME-26SEP081940PITCWS-PIT"


def mk(bid=0.68, ask=0.70, series="KXMLBGAME", title="Pittsburgh wins",
       ticker=TICKER) -> MarketSnapshot:
    return MarketSnapshot(
        ticker=ticker, event_ticker="E", series_ticker=series, title=title,
        observed_at=GAME_DAY, close_time=GAME_DAY, yes_bid=bid, yes_ask=ask,
        last_price=None, volume=0.0, open_interest=0.0)


def fc(m: MarketSnapshot, now: datetime = GAME_DAY) -> float:
    return C.forecast(m, ForecastContext(now=now))


def test_it_fades_inside_the_band():
    assert fc(mk()) == pytest.approx(0.69 - C.SHIFT)


@pytest.mark.parametrize("m, why", [
    (mk(series="KXNASDAQ100U"), "series outside the research allowlist"),
    (mk(title="Tie"), "tie leg, excluded by its subject too"),
    (mk(bid=0.66, ask=0.72), "six-cent book, above the cost gate"),
    (mk(bid=0.30, ask=0.32), "below MIN_PRICE: not a favourite"),
    (mk(bid=0.94, ask=0.96), "above MAX_PRICE"),
    (mk(ticker="NODATE-XYZ"), "no parseable game date in the ticker"),
])
def test_it_abstains_outside_the_gate(m, why):
    assert fc(m) == m.implied_prob, why


def test_it_abstains_off_the_game_day():
    """Kalshi sets close_time two to three days AFTER the game, so the ticker
    date is the only honest source and the window has to be tested on it."""
    assert fc(mk(), now=GAME_DAY.replace(day=7)) == 0.69
    assert fc(mk(), now=GAME_DAY.replace(day=9)) == 0.69
    # The window opens at 06:00 UTC, not midnight.
    assert fc(mk(), now=GAME_DAY.replace(hour=3)) == 0.69
    assert fc(mk(), now=GAME_DAY.replace(hour=6)) == pytest.approx(0.65)


def test_the_four_cent_book_is_not_dropped_by_float_error():
    """A nominal four-cent book parses as 0.040000000000000036. A bare
    `> MAX_SPREAD` drops a third of them, and an instrument that gated
    differently from its subject would not be measuring the same set."""
    m = mk(bid=0.66, ask=0.7000000000000001)
    assert m.spread > C.MAX_SPREAD
    assert fc(m) == pytest.approx(0.68 - C.SHIFT)


def test_the_clamp_matches_its_subject_near_the_boundary():
    """roster_news_favourite ends `max(p - SHIFT, 0.5 + EPS)`, which truncates
    the fade near 0.54 -- that is the -0.014/-0.024/-0.034 tail in the forecast
    log. A control that faded the full four cents there would be measuring a
    different rule."""
    assert fc(mk(bid=0.52, ask=0.54)) == pytest.approx(0.5 + C.EPS)


def test_it_is_registered_as_a_control_not_a_candidate():
    """Controls are instruments, not candidates to improve: SelectionPolicy is
    built with them excluded, so breeding can never spend a generation
    optimising toward one."""
    from avo.core import registry
    exp = registry.load("kalshi_quant")
    c = next(c for c in exp.seed_candidates()
             if c.candidate_id == "control_news_fade_band")
    assert c.meta["role"] == "control"


def test_its_gate_still_matches_the_candidate_it_controls_for():
    """The constants are copied, deliberately, so that kalshi_research widening
    its gates again cannot silently change what this measures. That trade is
    only safe if the drift is visible, so assert it here: if this fails, decide
    whether to re-match or to record that they have diverged."""
    from experiments.kalshi_research.candidates import roster_news_favourite as R
    assert C.RESEARCH_SERIES == R.RESEARCH_SERIES
    assert (C.MIN_PRICE, C.MAX_PRICE) == (R.MIN_PRICE, R.MAX_PRICE)
    assert C.MAX_SPREAD == R.MAX_SPREAD
    assert C.SHIFT == R.SHIFT
    assert C.GAME_DAY_START_UTC == R.GAME_DAY_START_UTC
    assert C.EPS == R.EPS
