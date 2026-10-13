"""MCP wiring: one MCPServer exposing the tools over stdio or streamable HTTP.

Tool logic lives in tools.py. This module registers the signatures and descriptions the
agent sees, applies the API-key guard to HTTP callers and serves /health, plus the optional
public demo route (demo.py).
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import __version__
from .config import Settings
from .demo import DEMO_PATH, DemoEndpoint
from .errors import FeatureDisabled, FoncierError
from .http import Fetcher
from .models import error_envelope
from .tiers import QuotaGuard
from .tools import run_tool

log = logging.getLogger("foncier_mcp")

REPO_URL = "https://github.com/1000vario/foncier-mcp"
MCP_PATH = "/mcp"

INSTRUCTIONS = (
    "French public land data (cadastre, property sales, planning zoning, energy diagnostics, "
    "natural and technological risks) for a parcel, an address or a WGS84 point. Every field "
    "is wrapped as {value, source, retrieved_at, reason}: a null value always carries a reason "
    "and nothing is ever guessed. Call summarize_parcel for the whole picture, or one specific "
    "tool. Coordinates are longitude then latitude in degrees. A parcel identifier has 14 "
    "characters, for example 34172000DS0638. Give exactly one locator per call."
)

DESCRIPTIONS: dict[str, str] = {
    "geocode_address": (
        "Geocode a French postal address through the IGN Geoplateforme (Base Adresse "
        "Nationale index). Returns the best match with WGS84 coordinates, the INSEE commune "
        "code, the BAN identifier, the match level (housenumber, street, municipality) and up "
        "to `limit` candidates (1 to 10)."
    ),
    "find_parcel": (
        "Find the cadastral parcel at an address, at a WGS84 point (lon, lat) or by its "
        "14-character identifier. Give exactly one locator. Returns the parcel attributes "
        "(identifier, commune, section, number, area in m2, centroid) and its GeoJSON geometry. "
        "When the geocoded address point falls outside every parcel, typically on the road, "
        "the nearest parcel within 10 m is returned with a warning naming the candidates."
    ),
    "get_parcel_sales": (
        "Recorded property sales (DVF, years 2021 to 2025 by default) on a parcel located by "
        "parcel_id, address or point. Optional radius_m (0 to 2000) also returns the sales "
        "around the parcel centroid with their distance. DVF does not cover departments 57, 67, "
        "68 and Mayotte: the tool says so instead of returning an empty list."
    ),
    "get_zoning": (
        "Planning zoning at a parcel from the Geoportail de l urbanisme: planning document "
        "(PLU, PLUi, carte communale), zone label and type with its meaning, surface "
        "prescriptions and public utility easements. States when the commune is under the "
        "national rules (RNU) or when no digitised document exists at that point."
    ),
    "get_energy_diagnostics": (
        "Energy performance diagnostics (DPE, ADEME) attached to a house-number address or to "
        "a BAN identifier. Returns energy and greenhouse gas classes, consumption, building "
        "type, living area and validity dates. Refuses to attach records when the address is "
        "not resolved at house-number level, rather than guessing a building."
    ),
    "get_risks": (
        "Natural and technological risks from Georisques for a commune (insee) or a location "
        "(parcel_id, address or point). Commune level: risk inventory, seismic zone, radon "
        "potential, natural disaster orders (CatNat), flood risk territories. Point level: clay "
        "shrink-swell exposure, flood zoning within 100 m, underground cavities and ground "
        "movements within 500 m, classified installations within 1 km."
    ),
    "summarize_parcel": (
        "One call for the whole picture of a parcel located by parcel_id, address or point: "
        "parcel, sales (DVF), zoning, energy diagnostics and risks. Each section is "
        "independent: a failing source leaves a null section with a reason and a typed error, "
        "the other sections still come back. Optional radius_m (0 to 2000) adds nearby sales."
    ),
    "export_parcel_pdf": (
        "Paid tier (disabled in this release): export a parcel summary as a PDF document."
    ),
    "watch_parcel": (
        "Paid tier (disabled in this release): watch a parcel and get an alert when a new sale "
        "or a new planning document appears."
    ),
}

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)


class FoncierServer:
    """Owns the settings, the HTTP fetcher, the quota guard and the MCPServer instance."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self.fetcher = Fetcher(self.settings)
        self.guard = QuotaGuard(self.settings)
        self.mcp = MCPServer(
            name="foncier-mcp", version=__version__, instructions=INSTRUCTIONS, website_url=REPO_URL
        )
        self._register_tools()
        self._register_routes()

    # ------------------------------------------------------------------ dispatch

    async def call(self, ctx: Context | None, name: str, inputs: dict[str, Any]) -> dict[str, Any]:
        headers = ctx.headers if ctx is not None else None
        shown = {k: v for k, v in inputs.items() if v is not None}
        try:
            access = self.guard.check(headers)
        except FoncierError as exc:
            return error_envelope(name, shown, exc)
        result = await run_tool(self.fetcher, name, inputs)
        if access.key_id is not None:
            result["quota"] = {
                "tier": access.tier,
                "used_today": access.used_today,
                "daily_quota": access.daily_quota,
                "remaining_today": access.remaining,
            }
            if access.downgraded:
                result["warnings"].append(
                    "this key belongs to the paid tier, which is disabled; served with the "
                    "free quota"
                )
        return result

    async def paid(
        self, ctx: Context | None, name: str, inputs: dict[str, Any], feature: str
    ) -> dict[str, Any]:
        headers = ctx.headers if ctx is not None else None
        try:
            access = self.guard.check(headers)
            if not self.guard.feature_allowed(access, feature):
                raise FeatureDisabled(f"{feature} needs an active paid tier key")
            raise FeatureDisabled(
                f"{feature} is designed but not implemented in release {__version__}"
            )
        except FoncierError as exc:
            return error_envelope(name, inputs, exc)

    # ------------------------------------------------------------------ registration

    def _register_tools(self) -> None:
        mcp = self.mcp
        call = self.call

        @mcp.tool(annotations=READ_ONLY, description=DESCRIPTIONS["geocode_address"])
        async def geocode_address(
            address: str, limit: int = 1, ctx: Context = None
        ) -> dict[str, Any]:
            return await call(ctx, "geocode_address", {"address": address, "limit": limit})

        @mcp.tool(annotations=READ_ONLY, description=DESCRIPTIONS["find_parcel"])
        async def find_parcel(
            address: str | None = None,
            lon: float | None = None,
            lat: float | None = None,
            parcel_id: str | None = None,
            ctx: Context = None,
        ) -> dict[str, Any]:
            return await call(
                ctx,
                "find_parcel",
                {"address": address, "lon": lon, "lat": lat, "parcel_id": parcel_id},
            )

        @mcp.tool(annotations=READ_ONLY, description=DESCRIPTIONS["get_parcel_sales"])
        async def get_parcel_sales(
            parcel_id: str | None = None,
            address: str | None = None,
            lon: float | None = None,
            lat: float | None = None,
            radius_m: int = 0,
            years: list[str] | None = None,
            ctx: Context = None,
        ) -> dict[str, Any]:
            return await call(
                ctx,
                "get_parcel_sales",
                {
                    "parcel_id": parcel_id,
                    "address": address,
                    "lon": lon,
                    "lat": lat,
                    "radius_m": radius_m,
                    "years": years,
                },
            )

        @mcp.tool(annotations=READ_ONLY, description=DESCRIPTIONS["get_zoning"])
        async def get_zoning(
            parcel_id: str | None = None,
            address: str | None = None,
            lon: float | None = None,
            lat: float | None = None,
            ctx: Context = None,
        ) -> dict[str, Any]:
            return await call(
                ctx,
                "get_zoning",
                {"parcel_id": parcel_id, "address": address, "lon": lon, "lat": lat},
            )

        @mcp.tool(annotations=READ_ONLY, description=DESCRIPTIONS["get_energy_diagnostics"])
        async def get_energy_diagnostics(
            address: str | None = None, ban_id: str | None = None, ctx: Context = None
        ) -> dict[str, Any]:
            return await call(ctx, "get_energy_diagnostics", {"address": address, "ban_id": ban_id})

        @mcp.tool(annotations=READ_ONLY, description=DESCRIPTIONS["get_risks"])
        async def get_risks(
            parcel_id: str | None = None,
            address: str | None = None,
            lon: float | None = None,
            lat: float | None = None,
            insee: str | None = None,
            ctx: Context = None,
        ) -> dict[str, Any]:
            return await call(
                ctx,
                "get_risks",
                {
                    "parcel_id": parcel_id,
                    "address": address,
                    "lon": lon,
                    "lat": lat,
                    "insee": insee,
                },
            )

        @mcp.tool(annotations=READ_ONLY, description=DESCRIPTIONS["summarize_parcel"])
        async def summarize_parcel(
            parcel_id: str | None = None,
            address: str | None = None,
            lon: float | None = None,
            lat: float | None = None,
            radius_m: int = 0,
            ctx: Context = None,
        ) -> dict[str, Any]:
            return await call(
                ctx,
                "summarize_parcel",
                {
                    "parcel_id": parcel_id,
                    "address": address,
                    "lon": lon,
                    "lat": lat,
                    "radius_m": radius_m,
                },
            )

        if not self.settings.paid_tier_enabled:
            return

        paid = self.paid

        @mcp.tool(annotations=READ_ONLY, description=DESCRIPTIONS["export_parcel_pdf"])
        async def export_parcel_pdf(parcel_id: str, ctx: Context = None) -> dict[str, Any]:
            return await paid(ctx, "export_parcel_pdf", {"parcel_id": parcel_id}, "pdf_export")

        @mcp.tool(description=DESCRIPTIONS["watch_parcel"])
        async def watch_parcel(parcel_id: str, email: str, ctx: Context = None) -> dict[str, Any]:
            return await paid(
                ctx, "watch_parcel", {"parcel_id": parcel_id, "email": email}, "parcel_watch"
            )

    def _register_routes(self) -> None:
        settings = self.settings

        @self.mcp.custom_route("/health", methods=["GET"])
        async def health(request: Request) -> Response:
            return JSONResponse(
                {
                    "status": "ok",
                    "name": "foncier-mcp",
                    "version": __version__,
                    "mcp_endpoint": MCP_PATH,
                    "paid_tier_enabled": settings.paid_tier_enabled,
                    "demo_enabled": settings.demo_enabled,
                }
            )

        @self.mcp.custom_route("/", methods=["GET"])
        async def root(request: Request) -> Response:
            return JSONResponse(
                {
                    "name": "foncier-mcp",
                    "version": __version__,
                    "mcp_endpoint": MCP_PATH,
                    "docs": REPO_URL,
                    "auth": "x-api-key header, free key on request (see README)",
                }
            )

        if not settings.demo_enabled:
            return
        demo = DemoEndpoint(settings, self.fetcher)
        self.demo = demo

        @self.mcp.custom_route(DEMO_PATH, methods=["POST"])
        async def demo_summary(request: Request) -> Response:
            return await demo.summary(request)

        @self.mcp.custom_route(DEMO_PATH, methods=["OPTIONS"])
        async def demo_preflight(request: Request) -> Response:
            return await demo.preflight(request)

    # ------------------------------------------------------------------ running

    def transport_security(self) -> TransportSecuritySettings:
        hosts = list(self.settings.allowed_hosts)
        if not hosts:
            return TransportSecuritySettings(enable_dns_rebinding_protection=False)
        origins = [f"https://{h}" for h in hosts] + [f"http://{h}" for h in hosts]
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
        )

    def run_stdio(self) -> None:
        self.mcp.run(transport="stdio")

    def run_http(self, host: str = "127.0.0.1", port: int = 8080) -> None:
        log.info("foncier-mcp %s listening on http://%s:%s%s", __version__, host, port, MCP_PATH)
        self.mcp.run(
            transport="streamable-http",
            host=host,
            port=port,
            streamable_http_path=MCP_PATH,
            json_response=True,
            stateless_http=True,
            transport_security=self.transport_security(),
        )

    def http_app(self, host: str = "127.0.0.1"):
        """The Starlette app, for tests and ASGI servers."""
        return self.mcp.streamable_http_app(
            streamable_http_path=MCP_PATH,
            json_response=True,
            stateless_http=True,
            transport_security=self.transport_security(),
            host=host,
        )


def build_server(settings: Settings | None = None) -> FoncierServer:
    return FoncierServer(settings)
