"""IGN API Carto, cadastre module (Parcellaire Express). Read on 2026-10-11.

GET /api/cadastre/parcelle with either geom (GeoJSON point) or code_insee + section + numero.
Answers are GeoJSON FeatureCollections, limited to 1000 features per call.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..errors import InvalidInput
from ..http import Fetcher, FetchResult

SOURCE = "apicarto_cadastre"
BASE = "https://apicarto.ign.fr/api/cadastre"

# INSEE (5 chars, Corsica uses 2A/2B) + absorbed commune (3) + section (2) + number (4) = 14.
PARCEL_ID_RE = re.compile(r"^(\d[0-9AB]\d{3})(\d{3})([0-9A-Z]{2})(\d{4})$")


def parse_parcel_id(parcel_id: str) -> dict[str, str]:
    value = (parcel_id or "").strip().upper()
    match = PARCEL_ID_RE.match(value)
    if not match:
        raise InvalidInput(
            "parcel_id must be the 14-character cadastral identifier, "
            "for example 34172000DS0638 (INSEE code, absorbed commune code, section, number)",
            details={"parcel_id": parcel_id},
        )
    insee, com_abs, section, numero = match.groups()
    return {
        "id": value,
        "code_insee": insee,
        "com_abs": com_abs,
        "section": section,
        "numero": numero,
    }


def point_geojson(lon: float, lat: float) -> str:
    return json.dumps({"type": "Point", "coordinates": [lon, lat]}, separators=(",", ":"))


async def parcels_at_point(
    fetcher: Fetcher, lon: float, lat: float
) -> tuple[list[dict[str, Any]], FetchResult]:
    data, result = await fetcher.get_json(
        f"{BASE}/parcelle", source=SOURCE, params={"geom": point_geojson(lon, lat), "_limit": 5}
    )
    return list(data.get("features") or []), result


async def parcel_by_id(
    fetcher: Fetcher, parcel_id: str
) -> tuple[list[dict[str, Any]], FetchResult]:
    parts = parse_parcel_id(parcel_id)
    data, result = await fetcher.get_json(
        f"{BASE}/parcelle",
        source=SOURCE,
        params={
            "code_insee": parts["code_insee"],
            "com_abs": parts["com_abs"],
            "section": parts["section"],
            "numero": parts["numero"],
            "_limit": 5,
        },
    )
    return list(data.get("features") or []), result


def _ring_centroid(ring: list[list[float]]) -> tuple[float, float, float]:
    """Area and centroid of a closed ring (shoelace), in degrees. Good enough for a parcel."""
    area2 = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(len(ring) - 1):
        x0, y0 = ring[i][0], ring[i][1]
        x1, y1 = ring[i + 1][0], ring[i + 1][1]
        cross = x0 * y1 - x1 * y0
        area2 += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(area2) < 1e-15:
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return 0.0, sum(xs) / len(xs), sum(ys) / len(ys)
    return abs(area2) / 2, cx / (3 * area2), cy / (3 * area2)


def representative_point(geometry: dict[str, Any] | None) -> tuple[float, float] | None:
    """Centroid of the largest exterior ring of a Polygon or MultiPolygon."""
    if not geometry:
        return None
    gtype = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if gtype == "MultiPolygon":
        polygons = coords
    elif gtype == "Polygon":
        polygons = [coords]
    else:
        polygons = []
    best: tuple[float, float, float] | None = None
    for poly in polygons:
        if not poly or not poly[0]:
            continue
        area, cx, cy = _ring_centroid(poly[0])
        if best is None or area > best[0]:
            best = (area, cx, cy)
    if best is None:
        return None
    return round(best[1], 7), round(best[2], 7)


def feature_to_parcel(feature: dict[str, Any]) -> dict[str, Any]:
    props = feature.get("properties") or {}
    geometry = feature.get("geometry")
    center = representative_point(geometry)
    return {
        "id": props.get("idu"),
        "code_insee": props.get("code_insee"),
        "commune": props.get("nom_com"),
        "com_abs": props.get("com_abs"),
        "section": props.get("section"),
        "numero": props.get("numero"),
        "sheet": props.get("feuille"),
        "area_m2": props.get("contenance"),
        "centroid": {"lon": center[0], "lat": center[1]} if center else None,
        "geometry": geometry,
    }
