"""Public demo endpoint: one summarize_parcel call per request, no key, tightly capped.

Off by default (FONCIER_MCP_DEMO). It exists so that people who do not use MCP clients can
try the service from a web form. The address travels in a POST body, never in a URL, and is
not logged. Limits live in memory: the container runs one instance at most, so a count kept
here is the global count, and it resets when the instance scales to zero.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .config import Settings
from .http import Fetcher
from .tools import run_tool

DEMO_PATH = "/demo/summary"
DEMO_TOOL = "summarize_parcel"
DEMO_RADIUS_M = 200
MAX_ADDRESS_CHARS = 200
MAX_BODY_BYTES = 2048
MAX_IN_FLIGHT = 3
DEMO_MAX_NEARBY = 15

BASE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow",
    "X-Content-Type-Options": "nosniff",
}


def trim_for_demo(result: dict[str, Any]) -> dict[str, Any]:
    """Keep the nearest sales only: a web card needs a handful, a city block has hundreds."""
    sales = (result.get("data") or {}).get("sales")
    nearby = sales.get("sales_nearby") if isinstance(sales, dict) else None
    items = nearby.get("value") if isinstance(nearby, dict) else None
    if isinstance(items, list) and len(items) > DEMO_MAX_NEARBY:
        nearby["value"] = items[:DEMO_MAX_NEARBY]
        result["warnings"].append(
            f"demo: only the {DEMO_MAX_NEARBY} nearest sales are shown; the MCP tool returns "
            "up to 200"
        )
    return result


class DemoLimiter:
    """Sliding hourly window per client address, plus a cap per UTC day for everyone."""

    def __init__(self, hourly_per_ip: int, daily_cap: int, clock=time.time) -> None:
        self.hourly_per_ip = hourly_per_ip
        self.daily_cap = daily_cap
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._day = ""
        self._day_count = 0

    def _today(self) -> str:
        return datetime.fromtimestamp(self.clock(), tz=UTC).strftime("%Y-%m-%d")

    def check(self, client: str) -> str | None:
        """Count the request and return None, or return why it is refused."""
        now = self.clock()
        today = self._today()
        if today != self._day:
            self._day, self._day_count = today, 0
            self._hits.clear()
        if self._day_count >= self.daily_cap:
            return "daily_cap"
        hits = self._hits.setdefault(client, deque())
        while hits and now - hits[0] >= 3600:
            hits.popleft()
        if len(hits) >= self.hourly_per_ip:
            return "hourly_limit"
        hits.append(now)
        self._day_count += 1
        return None


def client_address(request: Request) -> str:
    """Best effort: the first X-Forwarded-For entry set by the platform proxy.

    A caller can forge that header, so the per-address limit only slows casual reuse; the
    daily cap is the real protection.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    first = forwarded.split(",")[0].strip()
    if first:
        return first
    return request.client.host if request.client else "unknown"


class DemoEndpoint:
    def __init__(self, settings: Settings, fetcher: Fetcher) -> None:
        self.settings = settings
        self.fetcher = fetcher
        self.limiter = DemoLimiter(settings.demo_hourly_per_ip, settings.demo_daily_cap)
        self.in_flight = 0

    def _headers(self, request: Request) -> dict[str, str]:
        headers = dict(BASE_HEADERS)
        origin = request.headers.get("origin")
        if origin and origin in self.settings.demo_origins:
            headers["Access-Control-Allow-Origin"] = origin
            headers["Vary"] = "Origin"
        return headers

    def _refuse(self, request: Request, status: int, code: str, message: str) -> Response:
        body = {"ok": False, "errors": [{"code": code, "message": message}]}
        return JSONResponse(body, status_code=status, headers=self._headers(request))

    async def preflight(self, request: Request) -> Response:
        headers = self._headers(request)
        if "Access-Control-Allow-Origin" in headers:
            headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            headers["Access-Control-Allow-Headers"] = "Content-Type"
            headers["Access-Control-Max-Age"] = "600"
        return Response(status_code=204, headers=headers)

    async def summary(self, request: Request) -> Response:
        origin = request.headers.get("origin")
        if origin and origin not in self.settings.demo_origins:
            return self._refuse(request, 403, "origin_refused", "this origin may not call the demo")
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return self._refuse(request, 413, "invalid_input", "request body too large")
        try:
            payload: Any = json.loads(raw or b"{}")
        except ValueError:
            return self._refuse(request, 400, "invalid_input", "the body must be JSON")
        address = payload.get("address") if isinstance(payload, dict) else None
        if not isinstance(address, str) or not address.strip():
            return self._refuse(request, 400, "invalid_input", "give a French postal address")
        address = " ".join(address.split())
        if len(address) > MAX_ADDRESS_CHARS:
            return self._refuse(
                request, 400, "invalid_input", f"address longer than {MAX_ADDRESS_CHARS} characters"
            )
        refused = self.limiter.check(client_address(request))
        if refused == "daily_cap":
            return self._refuse(
                request,
                429,
                "quota_exceeded",
                "the demo has reached its daily limit, retry tomorrow",
            )
        if refused == "hourly_limit":
            return self._refuse(
                request, 429, "quota_exceeded", "too many demo requests from you, retry in an hour"
            )
        if self.in_flight >= MAX_IN_FLIGHT:
            return self._refuse(request, 503, "busy", "the demo is busy, retry in a few seconds")
        self.in_flight += 1
        try:
            result = await asyncio.wait_for(
                run_tool(self.fetcher, DEMO_TOOL, {"address": address, "radius_m": DEMO_RADIUS_M}),
                timeout=self.settings.timeout_seconds * 2,
            )
        except TimeoutError:
            return self._refuse(request, 504, "timeout", "the public sources took too long")
        finally:
            self.in_flight -= 1
        return JSONResponse(trim_for_demo(result), headers=self._headers(request))
