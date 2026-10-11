from foncier_mcp.server import DESCRIPTIONS, FoncierServer
from foncier_mcp.tiers import KeyStore, tiers_for
from foncier_mcp.tools import TOOL_FUNCTIONS
from helpers import FakeContext, make_settings


async def test_seven_read_only_tools_registered(settings):
    server = FoncierServer(settings)
    listed = await server.mcp.list_tools()
    assert [t.name for t in listed] == list(TOOL_FUNCTIONS)
    for tool in listed:
        assert tool.description == DESCRIPTIONS[tool.name]
        assert tool.annotations is not None and tool.annotations.read_only_hint is True
        assert "ctx" not in tool.input_schema["properties"]


async def test_paid_tools_only_with_flag(tmp_path):
    server = FoncierServer(make_settings(tmp_path, paid_tier_enabled=True))
    names = {t.name for t in await server.mcp.list_tools()}
    assert {"export_parcel_pdf", "watch_parcel"} <= names
    off = FoncierServer(make_settings(tmp_path / "off"))
    assert "watch_parcel" not in {t.name for t in await off.mcp.list_tools()}


async def test_stdio_needs_no_key(settings):
    server = FoncierServer(settings)
    out = await server.call(None, "find_parcel", {})
    assert out["ok"] is False and out["errors"][0]["code"] == "invalid_input"
    assert "quota" not in out


async def test_http_requires_key(settings):
    server = FoncierServer(settings)
    out = await server.call(FakeContext({}), "find_parcel", {"parcel_id": "34172000DS0638"})
    assert out["ok"] is False and out["errors"][0]["code"] == "unauthorized"


async def test_http_quota_counted_per_key(settings):
    server = FoncierServer(settings)
    plain, _ = server.guard.keys.issue("a@b.cd", "free", server.guard.tiers)
    ctx = FakeContext({"x-api-key": plain})
    for used in range(1, settings.free_daily_quota + 1):
        out = await server.call(ctx, "find_parcel", {})
        assert out["quota"]["used_today"] == used
        assert out["quota"]["remaining_today"] == settings.free_daily_quota - used
    out = await server.call(ctx, "find_parcel", {})
    assert out["errors"][0]["code"] == "quota_exceeded"


async def test_paid_key_served_as_free_when_flag_off(tmp_path):
    on = make_settings(tmp_path, paid_tier_enabled=True)
    plain, _ = KeyStore(on.keys_file).issue("a@b.cd", "paid", tiers_for(on))
    server = FoncierServer(make_settings(tmp_path, paid_tier_enabled=False))
    out = await server.call(FakeContext({"x-api-key": plain}), "find_parcel", {})
    assert out["quota"]["tier"] == "free"
    assert any("paid tier" in w for w in out["warnings"])


async def test_paid_tools_refuse_cleanly(tmp_path):
    on = make_settings(tmp_path, paid_tier_enabled=True)
    server = FoncierServer(on)
    free_key, _ = server.guard.keys.issue("f@b.cd", "free", server.guard.tiers)
    paid_key, _ = server.guard.keys.issue("p@b.cd", "paid", server.guard.tiers)
    out = await server.paid(
        FakeContext({"x-api-key": free_key}), "export_parcel_pdf", {}, "pdf_export"
    )
    assert out["errors"][0]["code"] == "feature_disabled"
    assert "paid tier" in out["errors"][0]["message"]
    out = await server.paid(
        FakeContext({"x-api-key": paid_key}), "export_parcel_pdf", {}, "pdf_export"
    )
    assert out["errors"][0]["code"] == "feature_disabled"
    assert "not implemented" in out["errors"][0]["message"]


def test_health_route(settings):
    from starlette.testclient import TestClient

    server = FoncierServer(settings)
    with TestClient(server.http_app()) as client:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok" and body["paid_tier_enabled"] is False
        assert client.get("/").json()["mcp_endpoint"] == "/mcp"


def test_transport_security_follows_allowed_hosts(tmp_path):
    open_server = FoncierServer(make_settings(tmp_path))
    assert open_server.transport_security().enable_dns_rebinding_protection is False
    closed = FoncierServer(make_settings(tmp_path, allowed_hosts=("foncier.example.org",)))
    security = closed.transport_security()
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["foncier.example.org"]
    assert "https://foncier.example.org" in security.allowed_origins
