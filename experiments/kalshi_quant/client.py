"""Kalshi public market-data client.

Market data endpoints are public — no auth, no RSA signing. Signing is only
needed for order placement, which this project never does (paper only).

UNVERIFIED: written from public docs, not run against the live API. Response
field names are the likeliest breakage — current docs show both `yes_bid`/
`volume` and `yes_bid_dollars`/`volume_fp` variants.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Iterator

import httpx

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"


def _iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class KalshiClient:
    def __init__(self, base_url: str = BASE_URL, timeout: float = 20.0) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        for attempt in range(4):
            r = self._http.get(path, params={k: v for k, v in params.items() if v is not None})
            if r.status_code == 429:
                time.sleep(2**attempt)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"rate limited after retries: {path}")

    def iter_markets(self, status: str = "open", limit: int = 200) -> Iterator[dict[str, Any]]:
        """status: open | closed | settled | all."""
        cursor: str | None = None
        while True:
            page = self._get("/markets", status=status, limit=limit, cursor=cursor)
            markets = page.get("markets", [])
            yield from markets
            cursor = page.get("cursor")
            if not cursor or not markets:
                return

    def orderbook(self, ticker: str, depth: int = 1) -> dict[str, Any]:
        return self._get(f"/markets/{ticker}/orderbook", depth=depth)


def parse_market(raw: dict[str, Any], observed_at: datetime | None = None):
    """TODO(claude-code): verify every key against a live response."""
    from experiments.kalshi_quant.types import MarketSnapshot

    def pick(*keys: str, default: Any = None) -> Any:
        for k in keys:
            if raw.get(k) is not None:
                return raw[k]
        return default

    return MarketSnapshot(
        ticker=raw["ticker"],
        event_ticker=pick("event_ticker", default=""),
        series_ticker=pick("series_ticker", default=raw["ticker"].split("-")[0]),
        title=pick("title", default=""),
        observed_at=observed_at or datetime.now(timezone.utc),
        close_time=_iso(pick("close_time", "expiration_time")) or datetime.now(timezone.utc),
        yes_bid=int(pick("yes_bid", default=0)),
        yes_ask=int(pick("yes_ask", default=100)),
        last_price=pick("last_price"),
        volume=int(pick("volume", "volume_fp", default=0)),
        open_interest=int(pick("open_interest", default=0)),
        raw=raw,
    )
