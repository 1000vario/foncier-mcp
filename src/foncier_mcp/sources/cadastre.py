"""IGN API Carto, cadastre module (Parcellaire Express). Read on 2026-10-11.

GET /api/cadastre/parcelle with either geom (GeoJSON point) or code_insee + section + numero.
Answers are GeoJSON FeatureCollections, limited to 1000 features per call.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from ..errors import InvalidInput
from ..http import Fetcher, FetchResult

SOURCE = "apicarto_cadastre"
BASE = "https://apicarto.ign.fr/api/cadastre"

# INSEE (5 chars, Corsica uses 2A/2B) + absorbed commune (3) + section (2) + number (4) = 14.
# Half-side of the square searched when a geocoded point falls outside every parcel.
NEAR_METERS = 10.0
METERS_PER_DEGREE = 111_320.0

PARCEL_ID_RE = re.compile(r"^(\d[0-9AB]\d{3})(\d{3})([0-9A-Z]{2})(\d{4})$")

# Paris, Lyon and Marseille: API Carto keys their parcels by the parent commune in
# code_insee (75056, 69123, 13055) plus a three-digit code_arr (111, 383, 208), while the
# idu keeps the arrondissement INSEE code (75111000AP0059). Read on 2026-10-11.
ARRONDISSEMENTS = {"751": ("75056", 1, 20), "693": ("69123", 81, 89), "132": ("13055", 1, 16)}


def arrondissement_parent(insee: str) -> str | None:
    """Parent commune of an arrondissement INSEE code, None for any other commune."""
    entry = ARRONDISSEMENTS.get(insee[:3])
    if entry is None or not insee[3:].isdigit():
        return None
    parent, low, high = entry
    return parent if low <= int(insee[3:]) <= high else None


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


def square_geojson(lon: float, lat: float, meters: float) -> str:
    """Square of half-side `meters` around the point, as a GeoJSON Polygon."""
    dlat = meters / METERS_PER_DEGREE
    dlon = meters / (METERS_PER_DEGREE * max(math.cos(math.radians(lat)), 1e-6))
    corners = [
        (lon - dlon, lat - dlat),
        (lon + dlon, lat - dlat),
        (lon + dlon, lat + dlat),
        (lon - dlon, lat + dlat),
        (lon - dlon, lat - dlat),
    ]
    ring = [[round(x, 7), round(y, 7)] for x, y in corners]
    return json.dumps({"type": "Polygon", "coordinates": [ring]}, separators=(",", ":"))


async def parcels_near_point(
    fetcher: Fetcher, lon: float, lat: float, meters: float = NEAR_METERS
) -> tuple[list[dict[str, Any]], FetchResult]:
    """Parcels intersecting a small square around the point, for points on the road."""
    data, result = await fetcher.get_json(
        f"{BASE}/parcelle",
        source=SOURCE,
        params={"geom": square_geojson(lon, lat, meters), "_limit": 20},
    )
    return list(data.get("features") or []), result


def parcel_params(parcel_id: str) -> dict[str, str | int]:
    """API Carto query for one parcel id, with the arrondissement translation."""
    parts = parse_parcel_id(parcel_id)
    params: dict[str, str | int] = {
        "code_insee": parts["code_insee"],
        "com_abs": parts["com_abs"],
        "section": parts["section"],
        "numero": parts["numero"],
        "_limit": 5,
    }
    parent = arrondissement_parent(parts["code_insee"])
    if parent is not None:
        params["code_insee"] = parent
        params["code_arr"] = parts["code_insee"][2:]
    return params


async def parcel_by_id(
    fetcher: Fetcher, parcel_id: str
) -> tuple[list[dict[str, Any]], FetchResult]:
    data, result = await fetcher.get_json(
        f"{BASE}/parcelle", source=SOURCE, params=parcel_params(parcel_id)
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


def _rings(geometry: dict[str, Any]) -> list[list[list[float]]]:
    gtype = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if gtype == "MultiPolygon":
        polygons = coords
    elif gtype == "Polygon":
        polygons = [coords]
    else:
        polygons = []
    return [ring for poly in polygons for ring in poly]


def distance_to_boundary_m(geometry: dict[str, Any] | None, lon: float, lat: float) -> float | None:
    """Shortest distance in metres from the point to the boundary of the geometry.

    Local equirectangular approximation: exact enough for a few metres around a parcel.
    """
    if not geometry:
        return None
    ky = METERS_PER_DEGREE
    kx = METERS_PER_DEGREE * max(math.cos(math.radians(lat)), 1e-6)
    qx, qy = lon * kx, lat * ky
    best: float | None = None
    for ring in _rings(geometry):
        for i in range(len(ring) - 1):
            ax, ay = ring[i][0] * kx, ring[i][1] * ky
            bx, by = ring[i + 1][0] * kx, ring[i + 1][1] * ky
            dx, dy = bx - ax, by - ay
            if dx == 0 and dy == 0:
                d = math.hypot(qx - ax, qy - ay)
            else:
                t = ((qx - ax) * dx + (qy - ay) * dy) / (dx * dx + dy * dy)
                t = max(0.0, min(1.0, t))
                d = math.hypot(qx - (ax + t * dx), qy - (ay + t * dy))
            if best is None or d < best:
                best = d
    return best


def nearest_feature(
    features: list[dict[str, Any]], lon: float, lat: float
) -> tuple[dict[str, Any], float | None]:
    """Feature whose boundary is closest to the point; ties keep the API order."""
    best: tuple[dict[str, Any], float | None] | None = None
    for feature in features:
        d = distance_to_boundary_m(feature.get("geometry"), lon, lat)
        if best is None or (d is not None and (best[1] is None or d < best[1])):
            best = (feature, d)
    if best is None:
        raise ValueError("nearest_feature needs at least one feature")
    return best


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
    idu = props.get("idu")
    # The idu starts with the arrondissement INSEE code where API Carto's code_insee holds
    # the parent commune; DVF files and the GPU are keyed by the arrondissement code.
    insee = idu[:5] if isinstance(idu, str) and len(idu) == 14 else props.get("code_insee")
    return {
        "id": idu,
        "code_insee": insee,
        "commune": props.get("nom_com"),
        "com_abs": props.get("com_abs"),
        "section": props.get("section"),
        "numero": props.get("numero"),
        "sheet": props.get("feuille"),
        "area_m2": props.get("contenance"),
        "centroid": {"lon": center[0], "lat": center[1]} if center else None,
        "geometry": geometry,
    }
