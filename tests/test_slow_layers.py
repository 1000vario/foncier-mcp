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
