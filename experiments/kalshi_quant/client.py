"""Kalshi public market-data client.

Market data endpoints are public — no auth, no RSA signing. Signing is only
needed for order placement, which this project never does (paper only).

VERIFIED against the live API 2026-08-23. Notes that matter:

- The `status=open` universe is ~962k markets across ~962 pages of 1000 (the
  server rejects limit > 1000). A full sweep is minutes, not seconds.
- Sustained throughput without 429s is roughly 3-4 req/s, but the limiter is
  bursty: an unpaced sweep took a 429 after ~20 pages on one run and none after
  61 on another. Hence both a pace floor and backoff.
- Every price/size/volume field is a decimal string. Parsing is strict: a
  missing key raises KeyError rather than silently defaulting, because a
  defaulted price is indistinguishable from a real one downstream. The one
  exception is `title`, which is cosmetic and genuinely absent on a few live
  markets; see parse_market.
"""
from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any, Self

import httpx

from experiments.kalshi_quant.types import MarketSnapshot

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"

MAX_PAGE_LIMIT = 1000
MIN_REQUEST_INTERVAL = 0.22  # seconds; ~4.5 req/s ceiling


def _iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s)  # 3.11+ parses the trailing 'Z' natively


class KalshiClient:
    def __init__(self, base_url: str = BASE_URL, timeout: float = 30.0) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=timeout)
        self._last_request = 0.0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        clean = {k: v for k, v in params.items() if v is not None}
        for attempt in range(7):
            wait = MIN_REQUEST_INTERVAL - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            r = self._http.get(path, params=clean)
            if r.status_code == 429:
                time.sleep(1.5 * 2**attempt)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"rate limited after retries: {path}")

    def iter_market_pages(
        self, status: str = "open", limit: int = MAX_PAGE_LIMIT
    ) -> Iterator[tuple[list[dict[str, Any]], datetime]]:
        """Yield (markets, fetched_at) per page.

        A full sweep spans minutes, so a single wall-clock `now` for the whole
        run would mis-stamp the last markets by ~10 minutes. The temporal
        holdout (INVARIANT #1) keys off observation time, so each page carries
        the instant it was actually fetched.
        """
        cursor: str | None = None
        while True:
            page = self._get("/markets", status=status, limit=limit, cursor=cursor)
            fetched_at = datetime.now(timezone.utc)
            markets = page.get("markets", [])
            if markets:
                yield markets, fetched_at
            cursor = page.get("cursor")
            if not cursor or not markets:
                return

    def iter_markets(self, status: str = "open", limit: int = MAX_PAGE_LIMIT) -> Iterator[dict[str, Any]]:
        """status: open | closed | settled | all."""
        for markets, _ in self.iter_market_pages(status=status, limit=limit):
            yield from markets

    def orderbook(self, ticker: str, depth: int = 1) -> dict[str, Any]:
        return self._get(f"/markets/{ticker}/orderbook", depth=depth)


def _money(raw: dict[str, Any], key: str) -> float:
    """Parse a decimal-string field. Strict: absence is a schema change, not a zero."""
    return float(raw[key])


def _required_iso(raw: dict[str, Any], key: str) -> datetime:
    ts = _iso(raw[key])
    if ts is None:
        raise ValueError(f"{raw.get('ticker')}: empty {key}")
    return ts


def parse_market(raw: dict[str, Any], observed_at: datetime | None = None) -> MarketSnapshot:
    event_ticker = raw["event_ticker"]
    # `series_ticker` is absent from every live market response; the series is
    # the first hyphen-delimited segment of the event ticker.
    series_ticker = event_ticker.split("-")[0]

    # "0.0000" here means "never traded" — the minimum quotable price is 0.0010
    # on deci-cent markets — so it is not a price and must not read as one.
    last = _money(raw, "last_price_dollars")

    return MarketSnapshot(
        ticker=raw["ticker"],
        event_ticker=event_ticker,
        series_ticker=series_ticker,
        # Tolerated deliberately: `title` is absent on a small number of live
        # markets (e.g. KXLLM1-26AUG31-MOON) and is cosmetic. Prices stay strict
        # below -- a defaulted price is indistinguishable from a real one.
        title=raw.get("title", ""),
        observed_at=observed_at or datetime.now(timezone.utc),
        close_time=_required_iso(raw, "close_time"),
        yes_bid=_money(raw, "yes_bid_dollars"),
        yes_ask=_money(raw, "yes_ask_dollars"),
        last_price=last if last > 0.0 else None,
        volume=_money(raw, "volume_fp"),
        open_interest=_money(raw, "open_interest_fp"),
        yes_bid_size=_money(raw, "yes_bid_size_fp"),
        yes_ask_size=_money(raw, "yes_ask_size_fp"),
        liquidity=_money(raw, "liquidity_dollars"),
        status=raw["status"],
        price_level_structure=raw["price_level_structure"],
        is_mve=bool(raw.get("mve_selected_legs")),
        raw=raw,
    )
