"""The seven tools, as plain async functions over a Fetcher.

The MCP layer (server.py) and the command line (cli.py) only wrap these. Every function
returns a ToolResult whose data holds sourced fields only; a FoncierError raised here becomes
a typed error envelope in the wrappers.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .errors import FoncierError, InvalidInput, NotCovered, NotFound
from .http import Fetcher
from .models import envelope, error_envelope, missing, sourced
from .sources import cadastre, dpe, dvf, geocode, georisques, gpu

INSEE_RE = re.compile(r"^(\d{5}|2[AB]\d{3})$")
BAN_ID_RE = re.compile(r"^[0-9A-Za-z_]{8,40}$")
YEAR_RE = re.compile(r"^20\d{2}$")
MAX_RADIUS_M = 2000
MAX_NEARBY = 200
DPE_REVERSE_MAX_DISTANCE_M = 30.0
# A point query on some Geoportail de l urbanisme layers returns whole polygons: in Lyon,
# prescription-surf was measured at 45 s on 2026-10-13. Those layers get their own deadline so
# the zone, which answers in under a second, still reaches the caller. The fetch keeps running
# past the deadline and fills the cache for the next request.
SLOW_LAYER_TIMEOUT_S = 25.0
_LATE_FETCHES: set[asyncio.Future[Any]] = set()
RADON_MEANING = {"1": "low potential", "2": "medium potential", "3": "high potential"}


@dataclass
class ToolResult:
    data: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Subject:
    """What a tool is about once the locator (address, point, parcel id) is resolved."""

    parcel: dict[str, Any] | None
    parcel_retrieved_at: str | None
    geocoding: dict[str, Any] | None
    geocoding_retrieved_at: str | None
    point: tuple[float, float] | None
    insee: str | None
    warnings: list[str] = field(default_factory=list)

    @property
    def parcel_id(self) -> str | None:
        return self.parcel["id"] if self.parcel else None

    def parcel_field(self) -> dict[str, Any]:
        if self.parcel is None:
            return missing("apicarto_cadastre", "no cadastral parcel found at this point")
        light = {k: v for k, v in self.parcel.items() if k != "geometry"}
        return sourced(light, "apicarto_cadastre", self.parcel_retrieved_at)

    def geocoding_field(self) -> dict[str, Any]:
        if self.geocoding is None:
            return missing("geoplateforme_geocodage", "no address was geocoded for this request")
        return sourced(self.geocoding, "geoplateforme_geocodage", self.geocoding_retrieved_at)


# ----------------------------------------------------------------------------- validation


def _check_address(address: str) -> str:
    value = (address or "").strip()
    if len(value) < 3:
        raise InvalidInput("address must have at least 3 characters", details={"address": address})
    if len(value) > 200:
        raise InvalidInput("address is longer than 200 characters")
    return value


def _check_point(lon: Any, lat: Any) -> tuple[float, float]:
    try:
        flon, flat = float(lon), float(lat)
    except (TypeError, ValueError) as exc:
        raise InvalidInput("lon and lat must be numbers (WGS84 degrees)") from exc
    if not (-180.0 <= flon <= 180.0 and -90.0 <= flat <= 90.0):
        raise InvalidInput("lon must be within [-180, 180] and lat within [-90, 90]")
    return flon, flat


def _check_insee(insee: str) -> str:
    value = (insee or "").strip().upper()
    if not INSEE_RE.match(value):
        raise InvalidInput("insee must be a 5-character INSEE commune code, for example 34172")
    return value


def _check_radius(radius_m: Any) -> int:
    try:
        value = int(radius_m or 0)
    except (TypeError, ValueError) as exc:
        raise InvalidInput("radius_m must be an integer number of metres") from exc
    if value < 0 or value > MAX_RADIUS_M:
        raise InvalidInput(f"radius_m must be between 0 and {MAX_RADIUS_M}")
    return value


def _check_years(years: list[str] | None) -> tuple[str, ...]:
    if not years:
        return dvf.DEFAULT_YEARS
    clean: list[str] = []
    for year in years:
        text = str(year).strip()
        if not YEAR_RE.match(text):
            raise InvalidInput(
                "years must be four-digit years such as 2024", details={"year": year}
            )
        clean.append(text)
    if len(clean) > dvf.MAX_YEARS:
        raise InvalidInput(f"at most {dvf.MAX_YEARS} years per call")
    return tuple(sorted(set(clean)))


def _one_locator(**locators: Any) -> str:
    given = [name for name, value in locators.items() if value not in (None, "")]
    if len(given) != 1:
        names = ", ".join(locators)
        raise InvalidInput(f"give exactly one locator among: {names}", details={"given": given})
    return given[0]


# ----------------------------------------------------------------------------- resolution


async def resolve_subject(
    fetcher: Fetcher,
    *,
    address: str | None = None,
    lon: float | None = None,
    lat: float | None = None,
    parcel_id: str | None = None,
    insee: str | None = None,
    allow_insee: bool = False,
) -> Subject:
    point_given = lon is not None or lat is not None
    locators: dict[str, Any] = {
        "address": address,
        "lon_lat": True if point_given else None,
        "parcel_id": parcel_id,
    }
    if allow_insee:
        locators["insee"] = insee
    elif insee not in (None, ""):
        raise InvalidInput("insee is not a valid locator for this tool")
    which = _one_locator(**locators)
    warnings: list[str] = []

    if which == "insee":
        return Subject(None, None, None, None, None, _check_insee(insee or ""), warnings)

    if which == "parcel_id":
        features, result = await cadastre.parcel_by_id(fetcher, parcel_id or "")
        if not features:
            raise NotFound(
                "no parcel with this identifier in the cadastre", source="apicarto_cadastre"
            )
        parcel = cadastre.feature_to_parcel(features[0])
        center = parcel["centroid"]
        point = (center["lon"], center["lat"]) if center else None
        return Subject(parcel, result.fetched_at, None, None, point, parcel["code_insee"], warnings)

    geocoding: dict[str, Any] | None = None
    geocoding_at: str | None = None
    if which == "address":
        clean = _check_address(address or "")
        features, result = await geocode.search(fetcher, clean, limit=1)
        if not features:
            raise NotFound("no address matched", source="geoplateforme_geocodage")
        geocoding = geocode.summarize_feature(features[0])
        geocoding_at = result.fetched_at
        point = (geocoding["lon"], geocoding["lat"])
        if geocoding["match_type"] != "housenumber":
            warnings.append(
                f"address resolved at {geocoding['match_type']} level, not at a house number: "
                "the parcel below is the one under that centroid and may not be the intended one"
            )
    else:
        point = _check_point(lon, lat)

    features, result = await cadastre.parcels_at_point(fetcher, point[0], point[1])
    if not features and which == "address":
        # BAN points often sit on the road axis: look a few metres around first.
        near, near_result = await cadastre.parcels_near_point(fetcher, point[0], point[1])
        if near:
            chosen, distance = cadastre.nearest_feature(near, point[0], point[1])
            ids = ", ".join(str((f.get("properties") or {}).get("idu")) for f in near)
            dist_txt = f"{distance:.1f} m away" if distance is not None else "distance unknown"
            warnings.append(
                "the geocoded point lies outside every parcel, typically on the road: "
                f"the nearest parcel within {cadastre.NEAR_METERS:g} m was taken ({dist_txt}); "
                f"{len(near)} candidates: {ids}"
            )
            features, result = [chosen], near_result
    if not features:
        return Subject(
            None,
            None,
            geocoding,
            geocoding_at,
            point,
            geocoding["citycode"] if geocoding else None,
            warnings,
        )
    if len(features) > 1:
        warnings.append(f"{len(features)} parcels touch this point; the first one is used")
    parcel = cadastre.feature_to_parcel(features[0])
    return Subject(
        parcel, result.fetched_at, geocoding, geocoding_at, point, parcel["code_insee"], warnings
    )


def _require_parcel(subject: Subject) -> dict[str, Any]:
    if subject.parcel is None:
        raise NotFound("no cadastral parcel at this location", source="apicarto_cadastre")
    return subject.parcel


# ----------------------------------------------------------------------------- tools


async def geocode_address(fetcher: Fetcher, address: str, limit: int = 1) -> ToolResult:
    clean = _check_address(address)
    if not isinstance(limit, int) or limit < 1 or limit > 10:
        raise InvalidInput("limit must be an integer between 1 and 10")
    features, result = await geocode.search(fetcher, clean, limit=limit)
    if not features:
        raise NotFound("no address matched", source="geoplateforme_geocodage")
    matches = [geocode.summarize_feature(f) for f in features]
    best = matches[0]
    src = geocode.SOURCE
    warnings: list[str] = []
    if best["match_type"] != "housenumber":
        warnings.append(f"best match is at {best['match_type']} level, not a house number")
    data = {
        "best_match": sourced(best, src, result.fetched_at),
        "coordinates": sourced({"lon": best["lon"], "lat": best["lat"]}, src, result.fetched_at),
        "citycode": sourced(best["citycode"], src, result.fetched_at),
        "candidates": sourced(matches, src, result.fetched_at),
    }
    return ToolResult(data, warnings)


async def find_parcel(
    fetcher: Fetcher,
    address: str | None = None,
    lon: float | None = None,
    lat: float | None = None,
    parcel_id: str | None = None,
) -> ToolResult:
    subject = await resolve_subject(fetcher, address=address, lon=lon, lat=lat, parcel_id=parcel_id)
    parcel = _require_parcel(subject)
    data = {
        "parcel": subject.parcel_field(),
        "geometry": sourced(parcel["geometry"], cadastre.SOURCE, subject.parcel_retrieved_at),
        "geocoding": subject.geocoding_field(),
        "lookup_point": sourced(
            {"lon": subject.point[0], "lat": subject.point[1]} if subject.point else None,
            "foncier_mcp",
            subject.parcel_retrieved_at,
        ),
    }
    return ToolResult(data, list(subject.warnings))


async def _sales_for_subject(
    fetcher: Fetcher, subject: Subject, radius_m: int, years: tuple[str, ...]
) -> ToolResult:
    parcel = _require_parcel(subject)
    insee = parcel["code_insee"]
    result = ToolResult({"parcel": subject.parcel_field()}, list(subject.warnings))
    if not dvf.is_covered(insee):
        reason = (
            f"DVF does not cover department {dvf.department_of(insee)} "
            "(Alsace-Moselle keeps the Livre foncier; Mayotte is not published)"
        )
        result.data["sales_on_parcel"] = missing(dvf.SOURCE, reason)
        result.data["sales_nearby"] = missing(dvf.SOURCE, reason)
        result.data["files"] = missing(dvf.SOURCE, reason)
        result.errors.append(NotCovered(reason, source=dvf.SOURCE).to_dict())
        return result

    rows, files = await dvf.commune_rows(fetcher, insee, years)
    ok_files = {y: f for y, f in files.items() if f["status"] == "ok"}
    failed = {y: f for y, f in files.items() if f["status"].startswith("http_")}
    retrieved_at = max((f["fetched_at"] for f in files.values()), default=None)
    if failed:
        result.warnings.append(
            "DVF files could not be read for years: " + ", ".join(sorted(failed))
        )
    if not ok_files and not failed:
        result.warnings.append(
            "no DVF file exists for this commune over the requested years (no recorded sale)"
        )
    result.data["files"] = sourced(files, dvf.SOURCE, retrieved_at)
    result.data["years"] = sourced(list(years), "foncier_mcp", retrieved_at)

    on_parcel = dvf.group_mutations(dvf.rows_for_parcel(rows, parcel["id"]))
    result.data["sales_on_parcel"] = sourced(on_parcel, dvf.SOURCE, retrieved_at)
    result.data["sales_on_parcel_count"] = sourced(len(on_parcel), "foncier_mcp", retrieved_at)

    if radius_m > 0:
        center = parcel.get("centroid")
        if not center:
            result.data["sales_nearby"] = missing(
                "foncier_mcp", "the parcel has no usable geometry, no centroid for the radius"
            )
        else:
            nearby_rows = dvf.rows_within(rows, center["lon"], center["lat"], radius_m)
            distance: dict[str, float] = {}
            for row, dist in nearby_rows:
                mid = row["id_mutation"]
                distance[mid] = min(dist, distance.get(mid, dist))
            nearby = dvf.group_mutations([r for r, _ in nearby_rows])
            for m in nearby:
                m["distance_m"] = round(distance[m["mutation_id"]], 1)
            nearby.sort(key=lambda m: (m["distance_m"], m["date"] or ""))
            if len(nearby) > MAX_NEARBY:
                result.warnings.append(
                    f"{len(nearby)} mutations within {radius_m} m, only the {MAX_NEARBY} nearest"
                    " are returned"
                )
                nearby = nearby[:MAX_NEARBY]
            result.data["sales_nearby"] = sourced(nearby, dvf.SOURCE, retrieved_at)
            result.data["sales_nearby_radius_m"] = sourced(radius_m, "foncier_mcp", retrieved_at)
    return result


async def get_parcel_sales(
    fetcher: Fetcher,
    parcel_id: str | None = None,
    address: str | None = None,
    lon: float | None = None,
    lat: float | None = None,
    radius_m: int = 0,
    years: list[str] | None = None,
) -> ToolResult:
    radius = _check_radius(radius_m)
    clean_years = _check_years(years)
    subject = await resolve_subject(fetcher, address=address, lon=lon, lat=lat, parcel_id=parcel_id)
    return await _sales_for_subject(fetcher, subject, radius, clean_years)


def _zone_record(props: dict[str, Any]) -> dict[str, Any]:
    typezone = props.get("typezone")
    return {
        "label": props.get("libelle"),
        "long_label": props.get("libelong"),
        "type": typezone,
        "type_meaning": gpu.TYPEZONE_MEANING.get(typezone or "", None),
        "document_id": props.get("idurba"),
        "validated_on": gpu.format_gpu_date(props.get("datvalid")),
        "approved_on": gpu.format_gpu_date(props.get("datappro")),
        "regulation_file": props.get("nomfic") or None,
        "regulation_url": gpu.regulation_url(props) or None,
        "gpu_status": props.get("gpu_status"),
        "gpu_published_at": props.get("gpu_timestamp"),
    }


def _document_record(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": props.get("name") or props.get("gpu_doc_id"),
        "type": props.get("du_type"),
        "title": props.get("grid_title"),
        "authority_code": props.get("grid_name"),
        "partition": props.get("partition"),
        "gpu_status": props.get("gpu_status"),
        "gpu_published_at": props.get("gpu_timestamp"),
    }


def _prescription_record(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": props.get("libelle"),
        "code": props.get("txt") or None,
        "type_code": props.get("typepsc"),
        "subtype_code": props.get("stypepsc"),
        "document_id": props.get("idurba"),
        "regulation_file": props.get("nomfic") or None,
        "regulation_url": gpu.regulation_url(props) or None,
    }


def _easement_record(props: dict[str, Any]) -> dict[str, Any]:
    suptype = props.get("suptype")
    return {
        "category": suptype.upper() if isinstance(suptype, str) else suptype,
        "name": props.get("nomsuplitt"),
        "footprint_type": props.get("typeass"),
        "footprint_id": props.get("idass"),
        "generator_id": props.get("idgen"),
        "file": props.get("fichier") or None,
        "regulation_url": props.get("urlreg") or None,
        "partition": props.get("partition"),
        "gpu_published_at": props.get("gpu_timestamp"),
    }


def _forget_late_fetch(task: asyncio.Future[Any]) -> None:
    _LATE_FETCHES.discard(task)
    if not task.cancelled():
        task.exception()  # retrieved, so a late failure is not logged as unhandled


async def _slow_layer(fetcher: Fetcher, endpoint: str, lon: float, lat: float) -> Any:
    """Features and fetch result of a slow GPU layer, or the reason it is absent."""
    task = asyncio.ensure_future(gpu.intersecting(fetcher, endpoint, lon, lat))
    try:
        return await asyncio.wait_for(asyncio.shield(task), SLOW_LAYER_TIMEOUT_S)
    except TimeoutError:
        _LATE_FETCHES.add(task)
        task.add_done_callback(_forget_late_fetch)
        return (
            f"the Geoportail de l urbanisme did not answer within {SLOW_LAYER_TIMEOUT_S:.0f} s "
            f"for {endpoint}; the answer is being cached, retry in a minute"
        )
    except FoncierError as exc:
        return f"{endpoint} unavailable: {exc.message}"


def _slow_layer_field(
    found: Any, record: Callable[[dict[str, Any]], dict[str, Any]], result: ToolResult
) -> dict[str, Any]:
    if isinstance(found, str):
        result.warnings.append(found)
        return missing(gpu.SOURCE, found)
    features, res = found
    return sourced(
        [record(f.get("properties") or {}) for f in features], gpu.SOURCE, res.fetched_at
    )


async def _zoning_for_subject(fetcher: Fetcher, subject: Subject) -> ToolResult:
    parcel = _require_parcel(subject)
    insee = parcel["code_insee"]
    center = parcel.get("centroid") or (
        {"lon": subject.point[0], "lat": subject.point[1]} if subject.point else None
    )
    result = ToolResult({"parcel": subject.parcel_field()}, list(subject.warnings))
    if center is None:
        raise NotFound("the parcel has no usable geometry to query the zoning", source=gpu.SOURCE)
    lon, lat = center["lon"], center["lat"]
    src = gpu.SOURCE

    muni, muni_res = await gpu.municipality(fetcher, insee)
    if muni is None:
        result.data["municipality"] = missing(
            src, "commune unknown to the Geoportail de l urbanisme"
        )
        is_rnu = False
    else:
        result.data["municipality"] = sourced(
            {
                "insee": muni.get("insee"),
                "name": muni.get("name"),
                "is_rnu": muni.get("is_rnu"),
                "is_coastline": muni.get("is_coastline"),
                "is_deleted": muni.get("is_deleted"),
            },
            src,
            muni_res.fetched_at,
        )
        is_rnu = bool(muni.get("is_rnu"))

    if is_rnu:
        reason = (
            "commune under the RNU (reglement national d urbanisme): no local planning document "
            "is in force, so there is no zoning to return"
        )
        result.data["planning_document"] = missing(src, reason)
        result.data["zones"] = missing(src, reason)
        result.data["prescriptions"] = missing(src, reason)
        easements = await _slow_layer(fetcher, "assiette-sup-s", lon, lat)
    else:
        (docs, docs_res), (zones, zones_res), prescriptions, easements = await asyncio.gather(
            gpu.intersecting(fetcher, "document", lon, lat),
            gpu.intersecting(fetcher, "zone-urba", lon, lat),
            _slow_layer(fetcher, "prescription-surf", lon, lat),
            _slow_layer(fetcher, "assiette-sup-s", lon, lat),
        )
        if docs:
            result.data["planning_document"] = sourced(
                [_document_record(f.get("properties") or {}) for f in docs],
                src,
                docs_res.fetched_at,
            )
        else:
            result.data["planning_document"] = missing(
                src, "no planning document published on the Geoportail de l urbanisme at this point"
            )
        if zones:
            result.data["zones"] = sourced(
                [_zone_record(f.get("properties") or {}) for f in zones], src, zones_res.fetched_at
            )
        else:
            sectors, sectors_res = await gpu.intersecting(fetcher, "secteur-cc", lon, lat)
            if sectors:
                result.data["zones"] = missing(
                    src,
                    "no PLU zone here; the commune has a carte communale, "
                    "see carte_communale_sectors",
                )
                result.data["carte_communale_sectors"] = sourced(
                    [
                        {
                            "type_code": (f.get("properties") or {}).get("typesect"),
                            "meaning": gpu.SECTEUR_CC_MEANING.get(
                                str((f.get("properties") or {}).get("typesect")), None
                            ),
                            "label": (f.get("properties") or {}).get("libelle"),
                            "document_id": (f.get("properties") or {}).get("idurba"),
                        }
                        for f in sectors
                    ],
                    src,
                    sectors_res.fetched_at,
                )
            else:
                result.data["zones"] = missing(
                    src,
                    "no zoning feature at this point on the Geoportail de l urbanisme: the "
                    "document may not be digitised, not yet published, or the point is outside it",
                )
        result.data["prescriptions"] = _slow_layer_field(
            prescriptions, _prescription_record, result
        )

    result.data["public_utility_easements"] = _slow_layer_field(easements, _easement_record, result)
    result.data["lookup_point"] = sourced({"lon": lon, "lat": lat}, "foncier_mcp", None)
    return result


async def get_zoning(
    fetcher: Fetcher,
    parcel_id: str | None = None,
    address: str | None = None,
    lon: float | None = None,
    lat: float | None = None,
) -> ToolResult:
    subject = await resolve_subject(fetcher, address=address, lon=lon, lat=lat, parcel_id=parcel_id)
    return await _zoning_for_subject(fetcher, subject)


async def _dpe_for_ban_id(fetcher: Fetcher, ban_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    records, results, totals = await dpe.diagnostics_for_ban_id(fetcher, ban_id)
    retrieved_at = max(r.fetched_at for r in results)
    return (
        sourced(records, dpe.SOURCE, retrieved_at),
        sourced(totals, dpe.SOURCE, retrieved_at),
    )


async def get_energy_diagnostics(
    fetcher: Fetcher, address: str | None = None, ban_id: str | None = None
) -> ToolResult:
    which = _one_locator(address=address, ban_id=ban_id)
    result = ToolResult({})
    if which == "ban_id":
        clean_id = (ban_id or "").strip()
        if not BAN_ID_RE.match(clean_id):
            raise InvalidInput("ban_id must look like 34172_1485_00012 (BAN address identifier)")
        result.data["address"] = missing(geocode.SOURCE, "lookup by BAN identifier, no geocoding")
    else:
        clean = _check_address(address or "")
        features, geo_res = await geocode.search(fetcher, clean, limit=1)
        if not features:
            raise NotFound("no address matched", source=geocode.SOURCE)
        match = geocode.summarize_feature(features[0])
        result.data["address"] = sourced(match, geocode.SOURCE, geo_res.fetched_at)
        if match["match_type"] != "housenumber":
            result.data["diagnostics"] = missing(
                dpe.SOURCE,
                f"the address resolved at {match['match_type']} level; DPE records are keyed by a "
                "house-number BAN identifier, so none can be attached without guessing",
            )
            result.data["counts"] = missing(dpe.SOURCE, "no house-number match")
            return result
        clean_id = match["ban_id"]
    result.data["diagnostics"], result.data["counts"] = await _dpe_for_ban_id(fetcher, clean_id)
    result.data["ban_id"] = sourced(
        clean_id, geocode.SOURCE if which == "address" else "foncier_mcp", None
    )
    return result


AT_POINT = "Georisques returned nothing at this point"


def _first(data: Any) -> dict[str, Any] | None:
    items = (data or {}).get("data") if isinstance(data, dict) else None
    if isinstance(items, list) and items:
        return items[0] if isinstance(items[0], dict) else None
    return None


def _items(data: Any) -> list[dict[str, Any]]:
    items = (data or {}).get("data") if isinstance(data, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _strip_geometry(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k not in ("geometry", "geom", "the_geom")}


def _hazard_labels(raw: Any) -> list[str] | None:
    if not isinstance(raw, list):
        return None
    labels: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            label = item.get("libelle_risque_long") or item.get("libelle_risque")
            if label:
                labels.append(str(label))
        elif isinstance(item, str):
            labels.append(item)
    return labels


def _risk_field(
    call: dict[str, Any], transform: Callable[[Any], Any], absent: str
) -> dict[str, Any]:
    """A sourced risk field; `absent` is the reason written when the source holds nothing."""
    if call["error"] is not None:
        err: FoncierError = call["error"]
        return missing(georisques.SOURCE, f"Georisques did not answer: {err.message}")
    value = transform(call["data"])
    if value is None:
        return missing(georisques.SOURCE, absent)
    return sourced(value, georisques.SOURCE, call["result"].fetched_at)


async def _risks_for_subject(fetcher: Fetcher, subject: Subject) -> ToolResult:
    insee = subject.insee
    if insee is None:
        raise NotFound("no commune could be determined for this location", source=georisques.SOURCE)
    result = ToolResult({"insee": sourced(insee, "foncier_mcp", None)}, list(subject.warnings))
    if subject.parcel is not None:
        result.data["parcel"] = subject.parcel_field()
    commune = await georisques.by_commune(fetcher, insee)
    parents = {c["insee"] for c in commune.values() if c.get("insee") not in (None, insee)}
    if parents:
        result.warnings.append(
            f"Georisques does not know the arrondissement code {insee}: commune-level risks "
            f"were read for the parent commune {sorted(parents)[0]}"
        )

    def none_for(what: str) -> str:
        return f"Georisques returned no {what} for commune {insee}"

    def inventory(data: Any) -> list[dict[str, Any]]:
        first = _first(data) or {}
        return [
            {
                "code": r.get("num_risque"),
                "label": r.get("libelle_risque_long") or r.get("libelle_risque"),
            }
            for r in first.get("risques_detail") or []
            if isinstance(r, dict)
        ]

    def seismic(data: Any) -> dict[str, Any] | None:
        first = _first(data)
        if not first:
            return None
        return {"zone_code": first.get("code_zone"), "label": first.get("zone_sismicite")}

    def radon(data: Any) -> dict[str, Any] | None:
        first = _first(data)
        if not first:
            return None
        cls = first.get("classe_potentiel")
        return {"class": cls, "meaning": RADON_MEANING.get(str(cls), None)}

    def catnat(data: Any) -> dict[str, Any]:
        items = _items(data)
        orders = [
            {
                "code": i.get("code_national_catnat"),
                "hazard": i.get("libelle_risque_jo"),
                "event_start": i.get("date_debut_evt"),
                "event_end": i.get("date_fin_evt"),
                "order_published_on": i.get("date_publication_arrete"),
            }
            for i in items
        ]
        return {"count": (data or {}).get("results", len(orders)), "orders": orders}

    def tri(data: Any) -> list[dict[str, Any]]:
        return [
            {
                "code": i.get("code_national_tri"),
                "label": i.get("libelle_tri"),
                "basin": i.get("libelle_bassin_risques"),
                "hazards": _hazard_labels(i.get("liste_libelle_risque")),
                "approved_on": i.get("date_arrete_approbation"),
            }
            for i in _items(data)
        ]

    result.data["commune"] = {
        "risks_inventory": _risk_field(
            commune["risks_inventory"], inventory, none_for("risk inventory")
        ),
        "seismic_zone": _risk_field(commune["seismic_zone"], seismic, none_for("seismic zone")),
        "radon_potential": _risk_field(
            commune["radon_potential"], radon, none_for("radon potential")
        ),
        "natural_disaster_orders": _risk_field(
            commune["natural_disaster_orders"], catnat, none_for("natural disaster order")
        ),
        "flood_risk_territories": _risk_field(
            commune["flood_risk_territory"], tri, none_for("flood risk territory")
        ),
    }

    if subject.point is None:
        result.data["point"] = missing(
            georisques.SOURCE, "no point was given, point-level risks need coordinates or a parcel"
        )
        return result
    lon, lat = subject.point
    point = await georisques.by_point(fetcher, lon, lat)

    def rga(data: Any) -> dict[str, Any] | None:
        if not isinstance(data, dict):
            return None
        return {"exposure_code": data.get("codeExposition"), "label": data.get("exposition")}

    def listing(data: Any) -> dict[str, Any]:
        items = [_strip_geometry(i) for i in _items(data)]
        return {"count": (data or {}).get("results", len(items)), "items": items[:20]}

    def flood_zoning(data: Any) -> dict[str, Any]:
        bad_dash = chr(0x96)
        items = [
            {
                "tri_code": i.get("code_national_tri"),
                "tri_label": (i.get("libelle_tri") or "").replace(bad_dash, "-") or None,
                "river": i.get("cours_deau"),
                "flood_type": (i.get("typeInondation") or {}).get("libelle"),
                "scenario_code": (i.get("scenario") or {}).get("code"),
                "scenario": (i.get("scenario") or {}).get("libelle"),
                "approved_on": i.get("date_arrete_approbation"),
            }
            for i in _items(data)
        ]
        return {"count": (data or {}).get("results", len(items)), "items": items}

    def icpe(data: Any) -> dict[str, Any]:
        items = [
            {
                "name": i.get("raisonSociale"),
                "regime": i.get("regime"),
                "seveso_status": i.get("statutSeveso"),
                "activity_state": i.get("etatActivite"),
                "commune": i.get("commune"),
                "lon": i.get("longitude"),
                "lat": i.get("latitude"),
            }
            for i in _items(data)
        ]
        return {"count": (data or {}).get("results", len(items)), "items": items[:20]}

    result.data["point"] = {
        "lookup_point": sourced({"lon": lon, "lat": lat}, "foncier_mcp", None),
        "clay_shrink_swell": _risk_field(
            point["clay_shrink_swell"], rga, "Georisques returned no clay zone at this point"
        ),
        "flood_zoning_100m": _risk_field(point["flood_zoning"], flood_zoning, AT_POINT),
        "underground_cavities_500m": _risk_field(
            point["underground_cavities_500m"], listing, AT_POINT
        ),
        "ground_movements_500m": _risk_field(point["ground_movements_500m"], listing, AT_POINT),
        "classified_installations_1km": _risk_field(
            point["classified_installations_1km"], icpe, AT_POINT
        ),
    }
    return result


async def get_risks(
    fetcher: Fetcher,
    parcel_id: str | None = None,
    address: str | None = None,
    lon: float | None = None,
    lat: float | None = None,
    insee: str | None = None,
) -> ToolResult:
    subject = await resolve_subject(
        fetcher,
        address=address,
        lon=lon,
        lat=lat,
        parcel_id=parcel_id,
        insee=insee,
        allow_insee=True,
    )
    return await _risks_for_subject(fetcher, subject)


async def _dpe_for_subject(fetcher: Fetcher, subject: Subject) -> ToolResult:
    result = ToolResult({})
    geo = subject.geocoding
    if geo and geo.get("match_type") == "housenumber" and geo.get("ban_id"):
        ban_id = geo["ban_id"]
        result.data["address"] = sourced(geo, geocode.SOURCE, subject.geocoding_retrieved_at)
    else:
        if subject.point is None:
            raise NotFound("no point to look up an address from", source=geocode.SOURCE)
        features, rev = await geocode.reverse(fetcher, subject.point[0], subject.point[1], limit=1)
        match = geocode.summarize_feature(features[0]) if features else None
        if (
            match is None
            or match.get("match_type") != "housenumber"
            or match.get("distance_m") is None
            or match["distance_m"] > DPE_REVERSE_MAX_DISTANCE_M
        ):
            why = (
                "no house-number address within "
                f"{int(DPE_REVERSE_MAX_DISTANCE_M)} m of the parcel centroid; DPE records are "
                "keyed by a BAN address identifier, so none is attached"
            )
            result.data["address"] = missing(geocode.SOURCE, why)
            result.data["diagnostics"] = missing(dpe.SOURCE, why)
            result.data["counts"] = missing(dpe.SOURCE, why)
            return result
        ban_id = match["ban_id"]
        result.data["address"] = sourced(match, geocode.SOURCE, rev.fetched_at)
        result.warnings.append(
            f"energy diagnostics are attached through the nearest BAN address, {match['label']}, "
            f"{match['distance_m']} m from the parcel centroid; check it is the right building"
        )
    result.data["diagnostics"], result.data["counts"] = await _dpe_for_ban_id(fetcher, ban_id)
    result.data["ban_id"] = sourced(ban_id, geocode.SOURCE, None)
    return result


async def summarize_parcel(
    fetcher: Fetcher,
    parcel_id: str | None = None,
    address: str | None = None,
    lon: float | None = None,
    lat: float | None = None,
    radius_m: int = 0,
) -> ToolResult:
    radius = _check_radius(radius_m)
    subject = await resolve_subject(fetcher, address=address, lon=lon, lat=lat, parcel_id=parcel_id)
    parcel = _require_parcel(subject)
    result = ToolResult(
        {
            "parcel": subject.parcel_field(),
            "geocoding": subject.geocoding_field(),
            "centroid": sourced(parcel.get("centroid"), "foncier_mcp", subject.parcel_retrieved_at),
        },
        list(subject.warnings),
    )

    sections: list[tuple[str, str, Callable[[], Awaitable[ToolResult]]]] = [
        (
            "sales",
            dvf.SOURCE,
            lambda: _sales_for_subject(fetcher, subject, radius, dvf.DEFAULT_YEARS),
        ),
        ("zoning", gpu.SOURCE, lambda: _zoning_for_subject(fetcher, subject)),
        ("energy_diagnostics", dpe.SOURCE, lambda: _dpe_for_subject(fetcher, subject)),
        ("risks", georisques.SOURCE, lambda: _risks_for_subject(fetcher, subject)),
    ]
    # The sections hit different hosts: run side by side, the slowest one sets the time.
    parts = await asyncio.gather(*(run() for _, _, run in sections), return_exceptions=True)
    for (name, source, _), part in zip(sections, parts, strict=True):
        if isinstance(part, FoncierError):
            result.data[name] = missing(source, f"section unavailable: {part.message}")
            result.errors.append(part.to_dict())
            continue
        if isinstance(part, BaseException):
            raise part
        part.data.pop("parcel", None)
        result.data[name] = part.data
        result.warnings.extend(w for w in part.warnings if w not in result.warnings)
        result.errors.extend(part.errors)
    return result


# ----------------------------------------------------------------------------- wrapper

TOOL_FUNCTIONS: dict[str, Callable[..., Awaitable[ToolResult]]] = {
    "geocode_address": geocode_address,
    "find_parcel": find_parcel,
    "get_parcel_sales": get_parcel_sales,
    "get_zoning": get_zoning,
    "get_energy_diagnostics": get_energy_diagnostics,
    "get_risks": get_risks,
    "summarize_parcel": summarize_parcel,
}


async def run_tool(fetcher: Fetcher, name: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Run a tool by name and return the JSON envelope, typed errors included."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        raise KeyError(name)
    shown = {k: v for k, v in inputs.items() if v is not None}
    started = time.perf_counter()
    try:
        allowed = set(inspect.signature(fn).parameters) - {"fetcher"}
        unknown = sorted(set(inputs) - allowed)
        if unknown:
            raise InvalidInput(
                f"unknown argument(s) for {name}: {', '.join(unknown)}",
                details={"accepted": sorted(allowed)},
            )
        result = await fn(fetcher, **inputs)
    except FoncierError as exc:
        duration = int((time.perf_counter() - started) * 1000)
        return error_envelope(name, shown, exc, duration_ms=duration)
    duration = int((time.perf_counter() - started) * 1000)
    return envelope(
        name,
        shown,
        result.data,
        warnings=result.warnings,
        errors=result.errors,
        duration_ms=duration,
    )
