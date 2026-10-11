"""DVF, demandes de valeurs foncieres, geolocated files from Etalab on data.gouv.fr.

Read on 2026-10-11. One CSV per commune and per year at
https://files.data.gouv.fr/geo-dvf/latest/csv/{year}/communes/{dep}/{insee}.csv (302 to an
object store). Years 2021 to 2025 were listed that day. The Cerema DVF API (apidf) was
unreachable the same day, so it is not used.

DVF does not cover Alsace-Moselle (57, 67, 68), which keep the Livre foncier, nor Mayotte.
"""

from __future__ import annotations

import csv
import io
import math
from collections import defaultdict
from typing import Any

from ..http import Fetcher, FetchResult

SOURCE = "dvf_geo"
BASE = "https://files.data.gouv.fr/geo-dvf/latest/csv"
DEFAULT_YEARS = ("2021", "2022", "2023", "2024", "2025")
NOT_COVERED_DEPARTMENTS = {"57", "67", "68", "976"}
MAX_YEARS = 8


def department_of(insee: str) -> str:
    insee = insee.upper()
    if insee.startswith("97") or insee.startswith("98"):
        return insee[:3]
    return insee[:2]


def is_covered(insee: str) -> bool:
    return department_of(insee) not in NOT_COVERED_DEPARTMENTS


def file_url(insee: str, year: str) -> str:
    return f"{BASE}/{year}/communes/{department_of(insee)}/{insee}.csv"


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def commune_rows(
    fetcher: Fetcher, insee: str, years: tuple[str, ...] = DEFAULT_YEARS
) -> tuple[list[dict[str, str]], dict[str, dict[str, Any]]]:
    """All DVF rows for a commune over the given years, plus per-year file metadata.

    A year with no file (HTTP 404) is recorded as absent, not as an error: small communes
    without any sale in a year have no file.
    """
    rows: list[dict[str, str]] = []
    files: dict[str, dict[str, Any]] = {}
    for year in years[:MAX_YEARS]:
        result: FetchResult = await fetcher.get(file_url(insee, year), source=SOURCE)
        if result.status == 404:
            files[year] = {"status": "absent", "fetched_at": result.fetched_at}
            continue
        if result.status >= 400:
            files[year] = {"status": f"http_{result.status}", "fetched_at": result.fetched_at}
            continue
        reader = csv.DictReader(io.StringIO(result.text))
        count = 0
        for row in reader:
            rows.append(row)
            count += 1
        files[year] = {
            "status": "ok",
            "rows": count,
            "fetched_at": result.fetched_at,
            "last_modified": result.headers.get("last-modified"),
        }
    return rows, files


def _num(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _int(value: str | None) -> int | None:
    number = _num(value)
    return int(number) if number is not None else None


def group_mutations(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """One record per mutation id. DVF repeats a mutation once per parcel, lot and local."""
    by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_id[row["id_mutation"]].append(row)
    mutations: list[dict[str, Any]] = []
    for mutation_id, group in by_id.items():
        first = group[0]
        parcels = sorted({r["id_parcelle"] for r in group if r.get("id_parcelle")})
        buildings: dict[tuple[Any, ...], dict[str, Any]] = {}
        land: dict[tuple[Any, ...], dict[str, Any]] = {}
        for r in group:
            if r.get("type_local"):
                key = (
                    r["id_parcelle"],
                    r["type_local"],
                    r.get("surface_reelle_bati"),
                    r.get("nombre_pieces_principales"),
                    r.get("lot1_numero"),
                )
                buildings[key] = {
                    "parcel_id": r["id_parcelle"],
                    "type": r["type_local"],
                    "built_area_m2": _int(r.get("surface_reelle_bati")),
                    "rooms": _int(r.get("nombre_pieces_principales")),
                    "lots": _int(r.get("nombre_lots")),
                }
            if r.get("surface_terrain"):
                lkey = (r["id_parcelle"], r.get("nature_culture"), r.get("surface_terrain"))
                land[lkey] = {
                    "parcel_id": r["id_parcelle"],
                    "nature": r.get("nature_culture") or None,
                    "area_m2": _int(r.get("surface_terrain")),
                }
        address = " ".join(
            part
            for part in (
                first.get("adresse_numero"),
                first.get("adresse_suffixe"),
                first.get("adresse_nom_voie"),
            )
            if part
        ).strip()
        mutations.append(
            {
                "mutation_id": mutation_id,
                "date": first.get("date_mutation"),
                "nature": first.get("nature_mutation"),
                "price_eur": _num(first.get("valeur_fonciere")),
                "address": address or None,
                "postcode": first.get("code_postal") or None,
                "commune": first.get("nom_commune"),
                "parcels": parcels,
                "buildings": list(buildings.values()),
                "land": list(land.values()),
                "lon": _num(first.get("longitude")),
                "lat": _num(first.get("latitude")),
            }
        )
    mutations.sort(key=lambda m: (m["date"] or "", m["mutation_id"]), reverse=True)
    return mutations


def rows_for_parcel(rows: list[dict[str, str]], parcel_id: str) -> list[dict[str, str]]:
    return [r for r in rows if r.get("id_parcelle") == parcel_id]


def rows_within(
    rows: list[dict[str, str]], lon: float, lat: float, radius_m: float
) -> list[tuple[dict[str, str], float]]:
    out: list[tuple[dict[str, str], float]] = []
    for r in rows:
        rlon, rlat = _num(r.get("longitude")), _num(r.get("latitude"))
        if rlon is None or rlat is None:
            continue
        d = haversine_m(lon, lat, rlon, rlat)
        if d <= radius_m:
            out.append((r, d))
    return out
