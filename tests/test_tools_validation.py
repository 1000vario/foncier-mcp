"""Input validation and typed errors, all offline: nothing here reaches the network."""

import pytest

from foncier_mcp import tools
from foncier_mcp.errors import InvalidInput
from foncier_mcp.http import Fetcher


@pytest.fixture
def fetcher(settings):
    return Fetcher(settings)


async def test_geocode_rejects_short_and_bad_limit(fetcher):
    with pytest.raises(InvalidInput):
        await tools.geocode_address(fetcher, "ab")
    with pytest.raises(InvalidInput):
        await tools.geocode_address(fetcher, "1 Place Georges Freche Montpellier", limit=0)


async def test_exactly_one_locator(fetcher):
    with pytest.raises(InvalidInput):
        await tools.find_parcel(fetcher)
    with pytest.raises(InvalidInput):
        await tools.find_parcel(fetcher, address="x y z", parcel_id="34172000DS0638")
    with pytest.raises(InvalidInput):
        await tools.get_risks(fetcher, insee="34172", lon=3.0, lat=43.0)


async def test_bad_parcel_id(fetcher):
    with pytest.raises(InvalidInput):
        await tools.find_parcel(fetcher, parcel_id="DS0638")


async def test_point_bounds(fetcher):
    with pytest.raises(InvalidInput):
        await tools.find_parcel(fetcher, lon=200, lat=43)
    with pytest.raises(InvalidInput):
        await tools.find_parcel(fetcher, lon=3.0)


async def test_sales_radius_and_years(fetcher):
    with pytest.raises(InvalidInput):
        await tools.get_parcel_sales(fetcher, parcel_id="34172000DS0638", radius_m=5000)
    with pytest.raises(InvalidInput):
        await tools.get_parcel_sales(fetcher, parcel_id="34172000DS0638", years=["abcd"])
    with pytest.raises(InvalidInput):
        await tools.summarize_parcel(fetcher, parcel_id="34172000DS0638", radius_m=-1)


async def test_insee_and_ban_id(fetcher):
    with pytest.raises(InvalidInput):
        await tools.get_risks(fetcher, insee="1")
    with pytest.raises(InvalidInput):
        await tools.get_energy_diagnostics(fetcher, ban_id="x")
    with pytest.raises(InvalidInput):
        await tools.get_energy_diagnostics(fetcher)


async def test_run_tool_envelopes_unknown_argument(fetcher):
    out = await tools.run_tool(fetcher, "find_parcel", {"parcel": "34172000DS0638"})
    assert out["ok"] is False
    assert out["errors"][0]["code"] == "invalid_input"
    assert "parcel_id" in out["errors"][0]["details"]["accepted"]
    with pytest.raises(KeyError):
        await tools.run_tool(fetcher, "nope", {})


async def test_offline_cache_miss_is_typed(fetcher):
    out = await tools.run_tool(fetcher, "find_parcel", {"lon": 3.8946, "lat": 43.5986})
    assert out["ok"] is False
    assert out["errors"][0]["code"] == "offline_cache_miss"
    assert out["data"] is None
