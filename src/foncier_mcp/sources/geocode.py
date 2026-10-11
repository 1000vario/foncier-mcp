"""IGN Geoplateforme geocoding (Base Adresse Nationale). Read on 2026-10-11.

Endpoints: /search (q, limit, index, citycode, postcode) and /reverse (lon, lat).
The legacy api-adresse.data.gouv.fr is deprecated (sunset announced for 2026-01-31).
"""

from __future__ import annotations

from typing import Any

from ..http import Fetcher, FetchResult

SOURCE = "geoplateforme_geocodage"
BASE = "https://data.geopf.fr/geocodage"


async def search(
    fetcher: Fetcher,
    q: str,
    *,
    limit: int = 1,
    index: str = "address",
    citycode: str | None = None,
    postcode: str | None = None,
) -> tuple[list[dict[str, Any]], FetchResult]:
    data, result = await fetcher.get_json(
        f"{BASE}/search",
        source=SOURCE,
        params={"q": q, "limit": limit, "index": index, "citycode": citycode, "postcode": postcode},
    )
    return list(data.get("features") or []), result


async def reverse(
    fetcher: Fetcher, lon: float, lat: float, *, limit: int = 1, index: str = "address"
) -> tuple[list[dict[str, Any]], FetchResult]:
    data, result = await fetcher.get_json(
        f"{BASE}/reverse",
        source=SOURCE,
        params={"lon": lon, "lat": lat, "limit": limit, "index": index},
    )
    return list(data.get("features") or []), result


def summarize_feature(feature: dict[str, Any]) -> dict[str, Any]:
    props = feature.get("properties") or {}
    coords = (feature.get("geometry") or {}).get("coordinates") or [None, None]
    return {
        "label": props.get("label"),
        "lon": coords[0],
        "lat": coords[1],
        "match_type": props.get("type"),
        "score": props.get("score"),
        "ban_id": props.get("id"),
        "citycode": props.get("citycode"),
        "postcode": props.get("postcode"),
        "city": props.get("city"),
        "street": props.get("street") or props.get("name"),
        "housenumber": props.get("housenumber"),
        "distance_m": props.get("distance"),
    }
