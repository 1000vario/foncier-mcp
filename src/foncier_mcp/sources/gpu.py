"""IGN API Carto, urbanisme module, a proxy of the Geoportail de l urbanisme. Read 2026-10-11.

Endpoints used: /municipality (insee), and point intersections on /zone-urba, /secteur-cc,
/document, /prescription-surf, /assiette-sup-s.
"""

from __future__ import annotations

from typing import Any

from ..http import Fetcher, FetchResult
from .cadastre import point_geojson

SOURCE = "apicarto_gpu"
BASE = "https://apicarto.ign.fr/api/gpu"

TYPEZONE_MEANING = {
    "U": "urban zone (zone urbaine)",
    "AU": "zone to be urbanised (zone a urbaniser)",
    "AUc": "zone to be urbanised, constructible (zone a urbaniser constructible)",
    "AUs": "zone to be urbanised, strict (zone a urbaniser stricte)",
    "A": "agricultural zone (zone agricole)",
    "N": "natural zone (zone naturelle)",
    "Ah": "agricultural zone with a limited-size sector (zone agricole, STECAL)",
    "Nh": "natural zone with a limited-size sector (zone naturelle, STECAL)",
    "Nd": "natural zone with a specific sector",
}

SECTEUR_CC_MEANING = {
    "01": "constructible sector of a carte communale",
    "02": "sector reserved for activities of a carte communale",
    "03": "non constructible sector of a carte communale",
}


async def municipality(fetcher: Fetcher, insee: str) -> tuple[dict[str, Any] | None, FetchResult]:
    data, result = await fetcher.get_json(
        f"{BASE}/municipality", source=SOURCE, params={"insee": insee}
    )
    features = data.get("features") or []
    return (features[0].get("properties") if features else None), result


async def intersecting(
    fetcher: Fetcher, endpoint: str, lon: float, lat: float
) -> tuple[list[dict[str, Any]], FetchResult]:
    data, result = await fetcher.get_json(
        f"{BASE}/{endpoint}", source=SOURCE, params={"geom": point_geojson(lon, lat)}
    )
    return list(data.get("features") or []), result


def regulation_url(props: dict[str, Any]) -> str | None:
    urlfic = props.get("urlfic")
    nomfic = props.get("nomfic") or ""
    if urlfic and "#page=" in nomfic and "#page=" not in urlfic:
        return urlfic + nomfic[nomfic.index("#page=") :]
    return urlfic


def format_gpu_date(raw: Any) -> str | None:
    """GPU dates come as YYYYMMDD strings."""
    if not raw:
        return None
    text = str(raw)
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text
