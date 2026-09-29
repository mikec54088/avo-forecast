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
              series: str = "KXTEST", close: datetime | None = None,
              event: str | None = None, mve: bool = False) -> dict:
    return {
        "ticker": ticker, "event_ticker": event or f"{series}-EV", "series_ticker": series,
        "title": ticker, "observed_at": observed_at,
        "close_time": close or observed_at + timedelta(hours=1),
        "yes_bid": bid, "yes_ask": ask, "last_price": None,
        "volume": 100.0, "open_interest": 50.0,
        "yes_bid_size": 10.0, "yes_ask_size": 10.0, "liquidity": 1.0,
        "status": "active", "price_level_structure": "linear_cent", "is_mve": mve,
    }


H = timedelta(hours=1)
M = timedelta(minutes=1)


@pytest.fixture
def data_root(tmp_path):
    """Markets chosen to exercise every rule of ENTRY_POLICY
    (first_signal_within_72h_of_close) at once.

    m1 IN   - three decision quotes (0.35 -> 0.55 -> 0.65) before it resolves
    m2 IN   - one decision quote, 30 min before resolution
    m5 IN   - its only quote is 2h before resolution. The OLD policy dropped
              it as stale; a pool that depends on how close to the end a
              market was last quoted is chosen by its future.
    m3 OUT  - captured but never resolved
    m4 OUT  - its only quote postdates its resolution (lookahead)
    m6 OUT  - quoted 100h before its close: outside the decision horizon
    m7 OUT  - one-sided book: nothing to trade or score against
    m8 OUT  - a parlay
    m1s     - same event as m1, quoted 5 min before m1's last decision: a sibling
    m1f     - same event, quoted AFTER m1 resolved: must never be a sibling
    """
    c1 = T0 + 4 * H
    snaps = [
        _snap_row("m1", T0, 0.30, 0.40, close=c1),
        _snap_row("m1", T0 + 2 * H + 40 * M, 0.50, 0.60, close=c1),
        _snap_row("m1", T0 + 2 * H + 50 * M, 0.60, 0.70, close=c1),
        _snap_row("m1s", T0 + 2 * H + 45 * M, 0.25, 0.30, close=c1),
        _snap_row("m1f", T0 + 3 * H + 5 * M, 0.01, 0.02, close=c1),
        _snap_row("m2", T0 + 3 * H + 30 * M, 0.10, 0.20, series="KXOTHER", close=T0 + 5 * H),
        _snap_row("m3", T0, 0.45, 0.55),
        _snap_row("m4", T0 + 9 * H, 0.50, 0.60, close=T0 + 10 * H),
        _snap_row("m5", T0 + 4 * H, 0.40, 0.50, close=T0 + 7 * H),
        _snap_row("m6", T0, 0.40, 0.50, close=T0 + 100 * H),
        _snap_row("m7", T0 + H, 0.0, 0.50, close=T0 + 2 * H),
        _snap_row("m8", T0 + H, 0.40, 0.50, series="KXMVE", close=T0 + 2 * H, mve=True),
    ]
    sd = tmp_path / "snapshots" / "date=2026-08-01"
    sd.mkdir(parents=True)
    pd.DataFrame(snaps).to_parquet(sd / "120000.parquet", index=False)

    def res(ticker: str, series: str, outcome: int, at: datetime) -> dict:
        return {"ticker": ticker, "series_ticker": series, "outcome": outcome,
                "resolved_at": at, "settlement_value": float(outcome)}
    rows = [
        res("m1", "KXTEST", 1, T0 + 3 * H),
        res("m2", "KXOTHER", 0, T0 + 4 * H),
        res("m4", "KXTEST", 1, T0 + 5 * H),
        res("m5", "KXTEST", 1, T0 + 6 * H),
        res("m6", "KXTEST", 0, T0 + 5 * H),
        res("m7", "KXTEST", 1, T0 + H + 30 * M),
        res("m8", "KXMVE", 1, T0 + 2 * H),
    ]
    rd = tmp_path / "resolutions" / "date=2026-08-01"
    rd.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(rd / "130000.parquet", index=False)
    return tmp_path
