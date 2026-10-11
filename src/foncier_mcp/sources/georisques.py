"""Georisques API v1. Endpoints tested live on 2026-10-11.

The documentation page was not readable that day (the site showed an incident banner and a
geographic access restriction), so published rate limits are unknown; the server keeps a
polite interval between calls. The pagination "next" links point to an internal host and are
never followed. The resultats_rapport_risque endpoint returned an empty body and is not used.

By commune (code_insee): gaspar/risques, zonage_sismique, radon, gaspar/catnat, gaspar/tri.
By point (latlon=lon,lat): rga, tri_zonage, cavites, mvt, installations_classees.
"""

from __future__ import annotations

from typing import Any

from ..errors import FoncierError
from ..http import Fetcher, FetchResult

SOURCE = "georisques"
BASE = "https://georisques.gouv.fr/api/v1"

COMMUNE_ENDPOINTS: dict[str, dict[str, Any]] = {
    "risks_inventory": {"path": "gaspar/risques", "params": {}},
    "seismic_zone": {"path": "zonage_sismique", "params": {}},
    "radon_potential": {"path": "radon", "params": {}},
    "natural_disaster_orders": {"path": "gaspar/catnat", "params": {"page_size": 100}},
    "flood_risk_territory": {"path": "gaspar/tri", "params": {}},
}
POINT_ENDPOINTS: dict[str, dict[str, Any]] = {
    "clay_shrink_swell": {"path": "rga", "params": {}},
    "flood_zoning": {"path": "tri_zonage", "params": {"rayon": 100}},
    "underground_cavities_500m": {"path": "cavites", "params": {"rayon": 500, "page_size": 20}},
    "ground_movements_500m": {"path": "mvt", "params": {"rayon": 500, "page_size": 20}},
    "classified_installations_1km": {
        "path": "installations_classees",
        "params": {"rayon": 1000, "page_size": 20},
    },
}


async def _call(
    fetcher: Fetcher, path: str, params: dict[str, Any]
) -> tuple[Any, FetchResult | None, FoncierError | None]:
    try:
        data, result = await fetcher.get_json(f"{BASE}/{path}", source=SOURCE, params=params)
        return data, result, None
    except FoncierError as exc:
        return None, None, exc


async def by_commune(fetcher: Fetcher, insee: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, spec in COMMUNE_ENDPOINTS.items():
        params = {"code_insee": insee, **spec["params"]}
        data, result, error = await _call(fetcher, spec["path"], params)
        out[key] = {"data": data, "result": result, "error": error}
    return out


async def by_point(fetcher: Fetcher, lon: float, lat: float) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, spec in POINT_ENDPOINTS.items():
        params = {"latlon": f"{lon},{lat}", **spec["params"]}
        data, result, error = await _call(fetcher, spec["path"], params)
        out[key] = {"data": data, "result": result, "error": error}
    return out
