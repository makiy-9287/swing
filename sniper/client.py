"""Async Binance USDT-M Futures REST client.

Robustness first: shared session, bounded concurrency, weight-aware throttling,
exponential backoff on 429/418/5xx and transient network errors. A single failing
symbol never takes down a scan.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, List, Optional

import aiohttp

from .candles import Candles

log = logging.getLogger("sniper.client")

BASE = "https://fapi.binance.com"


class BinanceFutures:
    def __init__(self, concurrency: int = 12, timeout: float = 15.0):
        self._sem = asyncio.Semaphore(concurrency)
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None
        # simple cooperative throttle: pause new requests until this monotonic time
        self._pause_until = 0.0

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={"User-Agent": "sniper-signals/1.0"},
            )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _throttle(self) -> None:
        loop = asyncio.get_event_loop()
        delta = self._pause_until - loop.time()
        if delta > 0:
            await asyncio.sleep(delta)

    async def _get(self, path: str, params: Optional[dict] = None,
                   retries: int = 4) -> Any:
        assert self._session is not None, "client not started"
        url = BASE + path
        backoff = 1.0
        loop = asyncio.get_event_loop()
        for attempt in range(retries + 1):
            await self._throttle()
            try:
                async with self._sem:
                    async with self._session.get(url, params=params) as resp:
                        # Weight management: if we're getting close to the cap, ease off.
                        used = resp.headers.get("X-MBX-USED-WEIGHT-1M")
                        if used and used.isdigit() and int(used) > 2000:
                            self._pause_until = loop.time() + 10.0
                            log.warning("Weight high (%s/2400) — easing off 10s", used)

                        if resp.status == 200:
                            return await resp.json()

                        if resp.status in (429, 418):
                            retry_after = float(resp.headers.get("Retry-After", "5"))
                            self._pause_until = loop.time() + retry_after + 1.0
                            log.warning("Rate limited (%s) on %s, sleeping %.1fs",
                                        resp.status, path, retry_after)
                            await asyncio.sleep(retry_after + 1.0)
                            continue

                        if resp.status >= 500:
                            await asyncio.sleep(backoff)
                            backoff *= 2
                            continue

                        text = await resp.text()
                        log.error("HTTP %s on %s params=%s: %s",
                                  resp.status, path, params, text[:200])
                        return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt >= retries:
                    log.error("Network error on %s after %d tries: %s", path, attempt, e)
                    return None
                await asyncio.sleep(backoff)
                backoff *= 2
        return None

    # ----------------------------------------------------------------- endpoints
    async def ping(self) -> bool:
        return (await self._get("/fapi/v1/ping")) is not None

    async def exchange_info(self) -> Optional[dict]:
        return await self._get("/fapi/v1/exchangeInfo")

    async def ticker_24h(self) -> Optional[List[dict]]:
        data = await self._get("/fapi/v1/ticker/24hr")
        return data if isinstance(data, list) else None

    async def klines(self, symbol: str, interval: str, limit: int = 300) -> Optional[Candles]:
        raw = await self._get(
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )
        if not isinstance(raw, list) or not raw:
            return None
        try:
            return Candles.from_klines(symbol, interval, raw)
        except Exception as e:  # defensive: never let one symbol break the scan
            log.error("Failed parsing klines %s %s: %s", symbol, interval, e)
            return None
