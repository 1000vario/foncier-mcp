"""HTTP fetcher: cache first, polite rate limiting per host, retries, typed failures."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .cache import ResponseCache
from .config import Settings
from .errors import OfflineCacheMiss, SourceUnavailable

RETRY_SLEEPS = (0.5, 1.5)
DEFAULT_MIN_INTERVAL = 0.2
MIN_INTERVAL_PER_HOST = {
    # The geocoding service advertises a per-second limit in its response headers.
    "data.geopf.fr": 0.3,
    "apicarto.ign.fr": 0.2,
    "files.data.gouv.fr": 0.2,
    "data.ademe.fr": 0.2,
    "georisques.gouv.fr": 0.2,
}


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    fetched_at: str
    from_cache: bool
    headers: dict[str, str]


class Fetcher:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.cache = ResponseCache(settings.cache_dir, settings.cache_ttl_hours)
        self._client = client
        self._last_call: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.settings.timeout_seconds,
                follow_redirects=True,
                headers={"User-Agent": self.settings.user_agent},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def build_url(url: str, params: dict[str, Any] | None) -> str:
        if not params:
            return url
        clean = {k: v for k, v in params.items() if v is not None}
        return str(httpx.URL(url, params=clean))

    async def _throttle(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            interval = MIN_INTERVAL_PER_HOST.get(host, DEFAULT_MIN_INTERVAL)
            wait = self._last_call.get(host, 0.0) + interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call[host] = time.monotonic()

    async def get(
        self, url: str, *, source: str, params: dict[str, Any] | None = None
    ) -> FetchResult:
        full = self.build_url(url, params)
        cached = self.cache.get(full, ignore_ttl=self.settings.offline)
        if cached is not None:
            return FetchResult(
                full, cached.status, cached.text, cached.fetched_at, True, cached.headers
            )
        if self.settings.offline:
            raise OfflineCacheMiss(
                f"offline mode: no cached answer for {full}", source=source, details={"url": full}
            )
        host = httpx.URL(full).host
        last_error: Exception | None = None
        for attempt in range(len(RETRY_SLEEPS) + 1):
            await self._throttle(host)
            try:
                response = await self.client.get(full)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code < 500 and response.status_code != 429:
                    entry = self.cache.put(
                        full, response.status_code, dict(response.headers), response.text
                    )
                    return FetchResult(
                        full, entry.status, entry.text, entry.fetched_at, False, entry.headers
                    )
                last_error = SourceUnavailable(
                    f"HTTP {response.status_code} from {host}",
                    source=source,
                    details={"url": full, "status": response.status_code},
                )
            if attempt < len(RETRY_SLEEPS):
                await asyncio.sleep(RETRY_SLEEPS[attempt])
        if isinstance(last_error, SourceUnavailable):
            raise last_error
        raise SourceUnavailable(
            f"{host} did not answer: {type(last_error).__name__}",
            source=source,
            details={"url": full},
        ) from last_error

    async def get_json(
        self, url: str, *, source: str, params: dict[str, Any] | None = None
    ) -> tuple[Any, FetchResult]:
        result = await self.get(url, source=source, params=params)
        if result.status >= 400:
            raise SourceUnavailable(
                f"HTTP {result.status} from {source}",
                source=source,
                details={"url": result.url, "status": result.status, "body": result.text[:300]},
            )
        try:
            data = json.loads(result.text)
        except ValueError as exc:
            raise SourceUnavailable(
                f"non-JSON answer from {source}", source=source, details={"url": result.url}
            ) from exc
        return data, result
