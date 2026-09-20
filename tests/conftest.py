"""Shared fixtures.

`data_root` lives here rather than in one test module because more than one
suite needs the same synthetic archive, and two copies of a fixture that
encodes the entry rules would be free to drift apart.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

T0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _snap_row(ticker: str, observed_at: datetime, bid: float, ask: float,
              series: str = "KXTEST") -> dict:
    return {
        "ticker": ticker, "event_ticker": f"{series}-EV", "series_ticker": series,
        "title": ticker, "observed_at": observed_at,
        "close_time": observed_at + timedelta(hours=1),
        "yes_bid": bid, "yes_ask": ask, "last_price": None,
        "volume": 100.0, "open_interest": 50.0,
        "yes_bid_size": 10.0, "yes_ask_size": 10.0, "liquidity": 1.0,
        "status": "active", "price_level_structure": "linear_cent", "is_mve": False,
    }


@pytest.fixture
def data_root(tmp_path):
    """Markets chosen to exercise every entry rule at once.

    m1 kept  - has a stale snapshot AND a fresh one; the fresh one must win
    m2 kept  - single snapshot, 30 min before resolution
    m3 gone  - captured but never resolved
    m4 gone  - its only snapshot postdates its resolution (lookahead)
    m5 gone  - resolved, but its freshest snapshot is 120 min stale (over cap)
    """
    snaps = [
        _snap_row("m1", T0, 0.30, 0.40),                                  # 180 min stale
        _snap_row("m1", T0 + timedelta(hours=2, minutes=40), 0.50, 0.60),  # history
        _snap_row("m1", T0 + timedelta(hours=2, minutes=50), 0.60, 0.70),  # 10 min stale
        # same event as m1, quoted just before the entry -> a sibling
        _snap_row("m1s", T0 + timedelta(hours=2, minutes=45), 0.25, 0.30),
        # same event, quoted AFTER m1 resolved -> must never be a sibling
        _snap_row("m1f", T0 + timedelta(hours=3, minutes=5), 0.01, 0.02),
        _snap_row("m2", T0 + timedelta(hours=3, minutes=30), 0.10, 0.20, series="KXOTHER"),
        _snap_row("m3", T0, 0.45, 0.55),
        _snap_row("m4", T0 + timedelta(hours=9), 0.50, 0.60),
        _snap_row("m5", T0 + timedelta(hours=4), 0.40, 0.50),              # 120 min stale
    ]
    sd = tmp_path / "snapshots" / "date=2026-08-01"
    sd.mkdir(parents=True)
    pd.DataFrame(snaps).to_parquet(sd / "120000.parquet", index=False)

    res = [
        {"ticker": "m1", "series_ticker": "KXTEST", "outcome": 1,
         "resolved_at": T0 + timedelta(hours=3), "settlement_value": 1.0},
        {"ticker": "m2", "series_ticker": "KXOTHER", "outcome": 0,
         "resolved_at": T0 + timedelta(hours=4), "settlement_value": 0.0},
        {"ticker": "m4", "series_ticker": "KXTEST", "outcome": 1,
         "resolved_at": T0 + timedelta(hours=5), "settlement_value": 1.0},
        {"ticker": "m5", "series_ticker": "KXTEST", "outcome": 1,
         "resolved_at": T0 + timedelta(hours=6), "settlement_value": 1.0},
    ]
    rd = tmp_path / "resolutions" / "date=2026-08-01"
    rd.mkdir(parents=True)
    pd.DataFrame(res).to_parquet(rd / "130000.parquet", index=False)
    return tmp_path


