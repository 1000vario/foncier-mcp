"""Record the expected truth of every eval case from official open data, by a different path
than the server uses.

Sources and access paths (all public, Licence Ouverte 2.0):
  parcel at a point   Etalab cadastre GeoJSON files (cadastre.data.gouv.fr), point-in-polygon in
                      pure Python. The server uses the IGN API Carto instead.
  DVF mutations       geo-dvf commune CSV files (files.data.gouv.fr), filtered on id_parcelle.
                      The server reads the same files, so this part is a parsing check, not an
                      independent measurement. The README says so.
  zoning              Geoplateforme WFS wfs_du:zone_urba, INTERSECTS at the parcel
                      representative point, and wfs_du:municipality for the RNU flag.
                      The server uses API Carto GPU instead.

Usage: python evals/truth/record.py [--dataset evals/dataset.json] [--out evals/truth/truth.json]
Downloads land in evals/truth/_downloads/ (git-ignored) and are reused on later runs.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "src"))

from foncier_mcp.sources import dvf  # noqa: E402
from foncier_mcp.sources.cadastre import representative_point  # noqa: E402

DOWNLOADS = HERE / "_downloads"
CADASTRE_URL = (
    "https://cadastre.data.gouv.fr/data/etalab-cadastre/latest/geojson/communes/"
    "{dep}/{insee}/cadastre-{insee}-parcelles.json.gz"
)
WFS = "https://data.geopf.fr/wfs/ows"
USER_AGENT = "foncier-mcp-evals/0.1 (+https://github.com/1000vario/foncier-mcp)"
YEARS = dvf.DEFAULT_YEARS


def log(*parts: object) -> None:
    print(*parts, file=sys.stderr, flush=True)


def download(url: str, target: Path) -> Path:
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response:
        data = response.read()
    target.write_bytes(data)
    time.sleep(0.5)
    return target


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def point_in_polygon(lon: float, lat: float, rings: list[list[list[float]]]) -> bool:
    if not rings or not point_in_ring(lon, lat, rings[0]):
        return False
    return not any(point_in_ring(lon, lat, hole) for hole in rings[1:])


def point_in_geometry(lon: float, lat: float, geometry: dict) -> bool:
    kind = geometry.get("type")
    if kind == "Polygon":
        return point_in_polygon(lon, lat, geometry["coordinates"])
    if kind == "MultiPolygon":
        return any(point_in_polygon(lon, lat, poly) for poly in geometry["coordinates"])
    return False


class CadastreIndex:
    def __init__(self) -> None:
        self.features: dict[str, list[dict]] = {}

    def load(self, insee: str) -> list[dict]:
        if insee not in self.features:
            dep = dvf.department_of(insee)
            path = download(
                CADASTRE_URL.format(dep=dep, insee=insee),
                DOWNLOADS / "cadastre" / f"{insee}.json.gz",
            )
            with gzip.open(path, "rb") as handle:
                self.features[insee] = json.load(handle)["features"]
            log("cadastre", insee, len(self.features[insee]), "parcels")
        return self.features[insee]

    def at_point(self, insee: str, lon: float, lat: float) -> dict | None:
        for feature in self.load(insee):
            if point_in_geometry(lon, lat, feature["geometry"]):
                return feature
        return None

    def by_id(self, insee: str, parcel_id: str) -> dict | None:
        for feature in self.load(insee):
            if feature["properties"].get("id") == parcel_id:
                return feature
        return None


def dvf_rows(insee: str) -> tuple[list[dict], dict[str, int | str]]:
    rows: list[dict] = []
    files: dict[str, int | str] = {}
    for year in YEARS:
        target = DOWNLOADS / "dvf" / f"{insee}-{year}.csv"
        try:
            download(dvf.file_url(insee, year), target)
        except urllib.error.HTTPError as error:
            files[year] = f"http_{error.code}"
            continue
        text = target.read_text(encoding="utf-8")
        year_rows = list(csv.DictReader(io.StringIO(text)))
        rows.extend(year_rows)
        files[year] = len(year_rows)
    return rows, files


def wfs_zones(lon: float, lat: float) -> list[dict]:
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": "wfs_du:zone_urba",
        "outputFormat": "application/json",
        "cql_filter": f"INTERSECTS(the_geom,POINT({lat} {lon}))",
    }
    body = fetch_json(WFS + "?" + urllib.parse.urlencode(params))
    time.sleep(0.5)
    out = []
    for feature in body.get("features", []):
        props = feature.get("properties", {})
        out.append(
            {
                "libelle": props.get("libelle"),
                "typezone": props.get("typezone"),
                "idurba": props.get("idurba"),
                "partition": props.get("partition"),
            }
        )
    return out


def wfs_municipality(insee: str) -> dict | None:
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": "wfs_du:municipality",
        "outputFormat": "application/json",
        "cql_filter": f"insee='{insee}'",
    }
    body = fetch_json(WFS + "?" + urllib.parse.urlencode(params))
    time.sleep(0.5)
    features = body.get("features", [])
    if not features:
        return None
    props = features[0].get("properties", {})
    return {"is_rnu": props.get("is_rnu"), "name": props.get("name")}


def record_case(case: dict, cadastre: CadastreIndex, dvf_cache: dict) -> dict:
    insee = case["insee"]
    lon, lat = case["input"]["lon"], case["input"]["lat"]
    truth: dict = {"insee": insee}

    feature = cadastre.at_point(insee, lon, lat)
    truth["parcel_at_point"] = feature["properties"]["id"] if feature else None
    expected = case["expected"].get("parcel_id") or truth["parcel_at_point"]
    truth["parcel_id"] = expected
    target = cadastre.by_id(insee, expected) if expected else None
    truth["parcel_exists_in_etalab"] = target is not None
    truth["point_in_expected_parcel"] = bool(
        target and point_in_geometry(lon, lat, target["geometry"])
    )
    rep = representative_point(target["geometry"]) if target else None
    truth["representative_point"] = {"lon": rep[0], "lat": rep[1]} if rep else None
    truth["area_m2"] = target["properties"].get("contenance") if target else None

    if not dvf.is_covered(insee):
        truth["dvf"] = {"covered": False, "mutation_ids": None, "files": None}
    else:
        if insee not in dvf_cache:
            dvf_cache[insee] = dvf_rows(insee)
        rows, files = dvf_cache[insee]
        mutation_ids = sorted({r["id_mutation"] for r in rows if r["id_parcelle"] == expected})
        truth["dvf"] = {"covered": True, "mutation_ids": mutation_ids, "files": files}

    municipality = wfs_municipality(insee)
    zone_point = rep or (lon, lat)
    zones = wfs_zones(zone_point[0], zone_point[1])
    truth["zoning"] = {
        "municipality": municipality,
        "is_rnu": bool(municipality and municipality.get("is_rnu")),
        "zones": zones,
        "zone_point": {"lon": zone_point[0], "lat": zone_point[1]},
    }
    return truth


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(REPO / "evals" / "dataset.json"))
    parser.add_argument("--out", default=str(HERE / "truth.json"))
    parser.add_argument("--only", nargs="*", help="case ids to (re)record; others are kept")
    args = parser.parse_args(argv)

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    out_path = Path(args.out)
    previous = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    recorded = dict(previous.get("cases", {}))

    cadastre = CadastreIndex()
    dvf_cache: dict = {}
    for case in dataset["cases"]:
        if args.only and case["id"] not in args.only:
            continue
        log("case", case["id"])
        truth = record_case(case, cadastre, dvf_cache)
        truth["recorded_at"] = datetime.now(UTC).replace(microsecond=0).isoformat()
        recorded[case["id"]] = truth

    payload = {
        "recorded_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "sources": {
            "parcel": CADASTRE_URL,
            "dvf": dvf.BASE + "/{year}/communes/{dep}/{insee}.csv",
            "zoning": WFS + " (wfs_du:zone_urba, wfs_du:municipality)",
        },
        "method": __doc__.strip(),
        "cases": recorded,
    }
    out_path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    log("wrote", out_path, len(recorded), "cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
