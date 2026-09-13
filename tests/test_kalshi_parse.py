"""Parser tests against a captured live payload. No network (see CONVENTIONS).

The fixture is a real `/markets?status=open` page recorded 2026-08-23, trimmed to
one market with a two-sided book, one with an empty book, and one MVE combo.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from experiments.kalshi_quant.client import parse_market

FIXTURE = Path(__file__).parent / "fixtures" / "kalshi_markets_page.json"


@pytest.fixture(scope="module")
def raw_markets() -> list[dict]:
    return json.loads(FIXTURE.read_text())["markets"]


def test_every_fixture_market_parses(raw_markets):
    for raw in raw_markets:
        parse_market(raw)


def test_prices_are_dollars_not_cents(raw_markets):
    """The whole point of the schema fix: yes_bid/yes_ask are already
    probabilities. If these ever exceed 1.0 the API switched back to cents and
    implied_prob's divisor is wrong."""
    for raw in raw_markets:
        m = parse_market(raw)
        assert 0.0 <= m.yes_bid <= 1.0
        assert 0.0 <= m.yes_ask <= 1.0


def test_implied_prob_is_midpoint_in_probability_units(raw_markets):
    quotable = [m for m in map(parse_market, raw_markets) if m.has_two_sided_book]
    assert quotable, "fixture must contain a two-sided market"
    for m in quotable:
        assert m.implied_prob == pytest.approx((m.yes_bid + m.yes_ask) / 2.0)
        assert m.yes_bid < m.implied_prob < m.yes_ask
        assert 0.0 < m.implied_prob < 1.0


def test_cents_views_are_consistent(raw_markets):
    for raw in raw_markets:
        m = parse_market(raw)
        assert m.yes_ask_cents == pytest.approx(m.yes_ask * 100.0)
        assert m.spread_cents == pytest.approx((m.yes_ask - m.yes_bid) * 100.0)


def test_decimal_string_fields_become_floats(raw_markets):
    """volume arrives as '0.00' / '772.09'. int() on that raises; the old int
    schema could not have parsed a single live market."""
    for raw in raw_markets:
        assert isinstance(raw["volume_fp"], str)
        m = parse_market(raw)
        assert isinstance(m.volume, float)
        assert isinstance(m.open_interest, float)


def test_never_traded_market_has_no_last_price(raw_markets):
    for raw in raw_markets:
        m = parse_market(raw)
        if float(raw["last_price_dollars"]) == 0.0:
            assert m.last_price is None
        else:
            assert m.last_price == pytest.approx(float(raw["last_price_dollars"]))


def test_empty_book_is_not_quotable(raw_markets):
    """INVARIANT #2 needs a market probability to score against; an empty book
    has none, and its midpoint must not be mistaken for one."""
    empty = [m for m in map(parse_market, raw_markets) if not m.has_two_sided_book]
    assert empty, "fixture must contain a market with no two-sided book"
    for m in empty:
        assert not (0.0 < m.yes_bid < m.yes_ask < 1.0)


def test_series_ticker_derived_from_event_ticker(raw_markets):
    """The API returns no series_ticker field at all."""
    for raw in raw_markets:
        assert "series_ticker" not in raw
        m = parse_market(raw)
        assert m.series_ticker == raw["event_ticker"].split("-")[0]
        assert m.series_ticker


def test_mve_combos_are_flagged(raw_markets):
    flagged = [m for m in map(parse_market, raw_markets) if m.is_mve]
    assert flagged, "fixture must contain an MVE combo market"


def test_missing_price_field_raises_rather_than_defaulting(raw_markets):
    """A defaulted price is indistinguishable from a real one downstream, so a
    schema change must fail loudly instead of silently quoting 0."""
    broken = dict(raw_markets[0])
    del broken["yes_bid_dollars"]
    with pytest.raises(KeyError):
        parse_market(broken)


def test_observed_at_is_honoured(raw_markets):
    stamp = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    assert parse_market(raw_markets[0], observed_at=stamp).observed_at == stamp


def test_liquidity_is_documented_as_always_zero(raw_markets):
    """Kalshi returns liquidity_dollars on every market and it is 0.0 on every
    one -- 610,077 rows across 40 snapshot files on 2026-09-01, none non-zero.
    A candidate gating on it silently becomes a constant: it passes validation,
    scores exactly 0, and reads as a failed idea rather than a dead field.

    If this ever fails, Kalshi started populating the field and the warnings in
    types.py should come out."""
    from experiments.kalshi_quant import types

    src = Path(types.__file__).read_text()
    assert "ALWAYS 0.0" in src, "the always-zero warning was removed from types.py"
    for raw in raw_markets:
        assert float(raw["liquidity_dollars"]) == 0.0, (
            "liquidity is now populated; update types.py and this test"
        )


# ------------------------------------------- the series-scoped full pass

def test_series_universe_is_derived_from_recent_snapshots(tmp_path):
    """Derived, not configured, so it maintains itself. The near pass is the
    discovery mechanism: unbounded in series and seeing everything closing
    within 24h, so a new series appears there before it can resolve."""
    import pandas as pd

    from experiments.kalshi_quant.capture import series_universe

    old = tmp_path / "snapshots" / "date=2000-01-01"
    new = tmp_path / "snapshots" / "date=2099-01-01"
    old.mkdir(parents=True); new.mkdir(parents=True)
    pd.DataFrame({"series_ticker": ["ANCIENT"]}).to_parquet(old / "a.parquet")
    pd.DataFrame({"series_ticker": ["KXMLBGAME", "KXMLBGAME", "KXNFLGAME"]}).to_parquet(
        new / "b.parquet")

    u = series_universe(days=36500, root=tmp_path)
    assert u == ["ANCIENT", "KXMLBGAME", "KXNFLGAME"]
    assert series_universe(days=1, root=tmp_path) == ["KXMLBGAME", "KXNFLGAME"]
    assert series_universe(days=1, root=tmp_path / "nothing") == []


def test_series_pages_are_walked_per_series_and_stamped_per_page():
    """Same shape as iter_market_pages so the caller does not care which walk
    it got, and fetched_at is per page because a sweep spans minutes and
    INVARIANT #1 keys off observation time."""
    from experiments.kalshi_quant.capture import _iter_series_pages

    calls = []

    class FakeClient:
        def _get(self, path, **kw):
            calls.append((kw["series_ticker"], kw.get("cursor")))
            if kw["series_ticker"] == "A":
                return ({"markets": [{"t": 1}], "cursor": "c1"} if not kw.get("cursor")
                        else {"markets": [{"t": 2}], "cursor": None})
            return {"markets": [], "cursor": None}

    pages = list(_iter_series_pages(FakeClient(), ["A", "B"], 20))
    assert [len(m) for m, _ in pages] == [1, 1]
    assert [c for _, c in calls] == [None, "c1", None]
    assert {s for s, _ in calls} == {"A", "B"}
    assert pages[0][1] <= pages[1][1]


def test_series_walk_respects_its_page_cap():
    from experiments.kalshi_quant.capture import _iter_series_pages

    class Endless:
        def _get(self, path, **kw):
            return {"markets": [{"t": 1}], "cursor": "always-more"}

    assert len(list(_iter_series_pages(Endless(), ["A"], 3))) == 3
