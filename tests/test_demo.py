from starlette.testclient import TestClient

from foncier_mcp import demo as demo_module
from foncier_mcp.demo import DEMO_PATH, DemoLimiter
from foncier_mcp.server import FoncierServer
from helpers import make_settings

SITE = "https://guillaume-couillard.com"


def demo_client(tmp_path, monkeypatch, **over):
    calls = []

    async def fake_run_tool(fetcher, name, inputs):
        calls.append((name, inputs))
        return {"ok": True, "tool": name, "input": inputs, "data": {}, "errors": []}

    monkeypatch.setattr(demo_module, "run_tool", fake_run_tool)
    settings = make_settings(tmp_path, demo_enabled=True, **over)
    return TestClient(FoncierServer(settings).http_app()), calls


def test_demo_route_absent_by_default(settings):
    with TestClient(FoncierServer(settings).http_app()) as client:
        assert client.post(DEMO_PATH, json={"address": "x"}).status_code in (404, 405)
        assert client.get("/health").json()["demo_enabled"] is False


def test_demo_runs_summary_without_key(tmp_path, monkeypatch):
    client, calls = demo_client(tmp_path, monkeypatch)
    with client:
        response = client.post(
            DEMO_PATH,
            json={"address": "  Place Pey Berland   33000 Bordeaux "},
            headers={"Origin": SITE},
        )
    assert response.status_code == 200
    assert calls == [
        ("summarize_parcel", {"address": "Place Pey Berland 33000 Bordeaux", "radius_m": 200})
    ]
    assert response.headers["access-control-allow-origin"] == SITE
    assert response.headers["cache-control"] == "no-store"
    assert "noindex" in response.headers["x-robots-tag"]


def test_demo_preflight_and_foreign_origin(tmp_path, monkeypatch):
    client, calls = demo_client(tmp_path, monkeypatch)
    with client:
        pre = client.options(DEMO_PATH, headers={"Origin": SITE})
        assert pre.status_code == 204
        assert pre.headers["access-control-allow-methods"] == "POST, OPTIONS"
        foreign = client.post(
            DEMO_PATH, json={"address": "a"}, headers={"Origin": "https://example.org"}
        )
        assert foreign.status_code == 403
        assert "access-control-allow-origin" not in foreign.headers
    assert calls == []


def test_demo_rejects_bad_input(tmp_path, monkeypatch):
    client, calls = demo_client(tmp_path, monkeypatch)
    with client:
        assert client.post(DEMO_PATH, content=b"not json").status_code == 400
        assert client.post(DEMO_PATH, json={"address": ""}).status_code == 400
        assert client.post(DEMO_PATH, json={"address": 12}).status_code == 400
        assert client.post(DEMO_PATH, json={"address": "a" * 201}).status_code == 400
        assert client.post(DEMO_PATH, content=b"x" * 5000).status_code == 413
    assert calls == []


def test_demo_hourly_limit_per_client(tmp_path, monkeypatch):
    client, calls = demo_client(tmp_path, monkeypatch, demo_hourly_per_ip=2)
    with client:
        first = {"X-Forwarded-For": "203.0.113.5, 10.0.0.1"}
        assert client.post(DEMO_PATH, json={"address": "a"}, headers=first).status_code == 200
        assert client.post(DEMO_PATH, json={"address": "a"}, headers=first).status_code == 200
        third = client.post(DEMO_PATH, json={"address": "a"}, headers=first)
        assert third.status_code == 429
        assert third.json()["errors"][0]["code"] == "quota_exceeded"
        other = {"X-Forwarded-For": "198.51.100.7"}
        assert client.post(DEMO_PATH, json={"address": "a"}, headers=other).status_code == 200
    assert len(calls) == 3


def test_trim_keeps_nearest_sales_only():
    result = {
        "warnings": [],
        "data": {"sales": {"sales_nearby": {"value": list(range(40)), "source": "dvf_geo"}}},
    }
    trimmed = demo_module.trim_for_demo(result)
    assert trimmed["data"]["sales"]["sales_nearby"]["value"] == list(range(15))
    assert "15 nearest" in trimmed["warnings"][0]
    missing = {"warnings": [], "data": {"sales": {"value": None, "reason": "not covered"}}}
    assert demo_module.trim_for_demo(missing)["warnings"] == []
    assert demo_module.trim_for_demo({"ok": False, "data": None, "warnings": []})["data"] is None


def test_limiter_daily_cap_and_reset():
    now = [1_800_000_000.0]
    limiter = DemoLimiter(hourly_per_ip=100, daily_cap=2, clock=lambda: now[0])
    assert limiter.check("a") is None
    assert limiter.check("b") is None
    assert limiter.check("c") == "daily_cap"
    now[0] += 86_400
    assert limiter.check("c") is None


def test_limiter_hourly_window_slides():
    now = [1_800_000_000.0]
    limiter = DemoLimiter(hourly_per_ip=1, daily_cap=100, clock=lambda: now[0])
    assert limiter.check("a") is None
    assert limiter.check("a") == "hourly_limit"
    now[0] += 3600
    assert limiter.check("a") is None
