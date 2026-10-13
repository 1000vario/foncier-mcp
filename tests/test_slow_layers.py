"""Slow Geoportail de l urbanisme layers: a deadline, a missing field, the fetch kept going."""

import asyncio

from foncier_mcp import tools
from foncier_mcp.errors import SourceUnavailable
from foncier_mcp.tools import ToolResult


class FakeResult:
    fetched_at = "2026-10-13T10:00:00Z"


def test_slow_layer_returns_reason_and_keeps_fetching(monkeypatch):
    finished = []

    async def slow_intersecting(fetcher, endpoint, lon, lat):
        await asyncio.sleep(0.2)
        finished.append(endpoint)
        return [], FakeResult()

    monkeypatch.setattr(tools.gpu, "intersecting", slow_intersecting)
    monkeypatch.setattr(tools, "SLOW_LAYER_TIMEOUT_S", 0.05)

    async def scenario():
        found = await tools._slow_layer(None, "prescription-surf", 0.0, 0.0)
        assert isinstance(found, str) and "did not answer" in found
        assert finished == []
        await asyncio.sleep(0.3)
        return found

    found = asyncio.run(scenario())
    assert finished == ["prescription-surf"]
    assert not tools._LATE_FETCHES
    result = ToolResult({})
    field = tools._slow_layer_field(found, tools._prescription_record, result)
    assert field["value"] is None and field["reason"] == found
    assert result.warnings == [found]


def test_slow_layer_failure_becomes_reason(monkeypatch):
    async def failing(fetcher, endpoint, lon, lat):
        raise SourceUnavailable("HTTP 502 from apicarto.ign.fr", source="apicarto_gpu")

    monkeypatch.setattr(tools.gpu, "intersecting", failing)
    found = asyncio.run(tools._slow_layer(None, "assiette-sup-s", 0.0, 0.0))
    assert found == "assiette-sup-s unavailable: HTTP 502 from apicarto.ign.fr"


def test_slow_layer_in_time_is_sourced(monkeypatch):
    async def fast(fetcher, endpoint, lon, lat):
        return [{"properties": {"idass": "A1"}}], FakeResult()

    monkeypatch.setattr(tools.gpu, "intersecting", fast)
    found = asyncio.run(tools._slow_layer(None, "assiette-sup-s", 0.0, 0.0))
    field = tools._slow_layer_field(found, tools._easement_record, ToolResult({}))
    assert field["value"][0]["footprint_id"] == "A1"
    assert field["retrieved_at"] == FakeResult.fetched_at


class FakeSubject:
    warnings: list[str] = []
    parcel_retrieved_at = None

    def parcel_field(self):
        return {"value": {"id": "P"}}

    def geocoding_field(self):
        return {"value": None}


def test_late_section_is_missing_and_the_others_are_returned(monkeypatch):
    finished = []

    async def subject(fetcher, **kwargs):
        return FakeSubject()

    async def slow_sales(fetcher, subject, radius, years):
        await asyncio.sleep(0.2)
        finished.append("sales")
        return ToolResult({"files": {}})

    async def fast(fetcher, subject):
        return ToolResult({"ok": True})

    monkeypatch.setattr(tools, "resolve_subject", subject)
    monkeypatch.setattr(tools, "_require_parcel", lambda s: {"id": "P", "centroid": None})
    monkeypatch.setattr(tools, "_sales_for_subject", slow_sales)
    for name in ("_zoning_for_subject", "_dpe_for_subject", "_risks_for_subject"):
        monkeypatch.setattr(tools, name, fast)
    monkeypatch.setattr(tools, "SECTION_TIMEOUT_S", 0.05)

    async def scenario():
        result = await tools.summarize_parcel(None, address="x", radius_m=200)
        assert finished == []
        await asyncio.sleep(0.3)
        return result

    result = asyncio.run(scenario())
    assert finished == ["sales"]
    assert not tools._LATE_FETCHES
    assert result.data["sales"]["value"] is None
    assert "did not answer within" in result.data["sales"]["reason"]
    assert result.data["zoning"] == {"ok": True} and result.data["risks"] == {"ok": True}
    assert result.errors[0]["source"] == "dvf_geo" and result.errors[0]["retryable"] is True
