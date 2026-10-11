"""Pure functions of the source modules: no network, no cache."""

import pytest

from foncier_mcp.errors import InvalidInput
from foncier_mcp.sources import cadastre, dvf, gpu

SQUARE = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}


def test_parse_parcel_id():
    parsed = cadastre.parse_parcel_id("34172000DS0638")
    assert parsed == {
        "id": "34172000DS0638",
        "code_insee": "34172",
        "com_abs": "000",
        "section": "DS",
        "numero": "0638",
    }
    assert cadastre.parse_parcel_id("2A004000AB0001")["code_insee"] == "2A004"


@pytest.mark.parametrize("bad", ["", "34172", "34172000DS063", "3417200DS06381", "DS0638"])
def test_parse_parcel_id_refuses(bad):
    with pytest.raises(InvalidInput):
        cadastre.parse_parcel_id(bad)


def test_representative_point():
    assert cadastre.representative_point(SQUARE) == (1.0, 1.0)
    multi = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            [[[10, 10], [14, 10], [14, 14], [10, 14], [10, 10]]],
        ],
    }
    assert cadastre.representative_point(multi) == (12.0, 12.0)
    assert cadastre.representative_point(None) is None


def test_feature_to_parcel():
    feature = {
        "type": "Feature",
        "geometry": SQUARE,
        "properties": {
            "idu": "34172000DS0638",
            "code_insee": "34172",
            "nom_com": "Montpellier",
            "com_abs": "000",
            "section": "DS",
            "numero": "0638",
            "feuille": 1,
            "contenance": 4553,
        },
    }
    parcel = cadastre.feature_to_parcel(feature)
    assert parcel["id"] == "34172000DS0638" and parcel["area_m2"] == 4553
    assert parcel["centroid"] == {"lon": 1.0, "lat": 1.0}
    assert parcel["geometry"] == SQUARE


def test_gpu_dates_and_urls():
    assert gpu.format_gpu_date("20250716") == "2025-07-16"
    assert gpu.format_gpu_date(None) is None
    assert gpu.format_gpu_date("2025") == "2025"
    assert not gpu.regulation_url({"urlfic": "", "nomfic": "reg.pdf#page=12"})
    assert (
        gpu.regulation_url({"urlfic": "https://x.test/reg.pdf", "nomfic": "reg.pdf#page=12"})
        == "https://x.test/reg.pdf#page=12"
    )
    assert gpu.TYPEZONE_MEANING["U"].startswith("urban")


def test_dvf_department_and_coverage():
    assert dvf.department_of("34172") == "34"
    assert dvf.department_of("97601") == "976"
    assert dvf.department_of("2A004") == "2A"
    assert dvf.is_covered("34172")
    assert not dvf.is_covered("67482") and not dvf.is_covered("97601")
    assert dvf.file_url("48092", "2024").endswith("/2024/communes/48/48092.csv")


def test_haversine_known_distance():
    distance = dvf.haversine_m(3.8946, 43.5986, 3.8946, 43.6076)
    assert 990 < distance < 1010


def _row(**over):
    base = {
        "id_mutation": "2024-1",
        "date_mutation": "2024-03-01",
        "nature_mutation": "Vente",
        "valeur_fonciere": "250000",
        "adresse_numero": "12",
        "adresse_suffixe": "",
        "adresse_nom_voie": "RUE TEST",
        "code_postal": "34000",
        "nom_commune": "Montpellier",
        "id_parcelle": "34172000AB0001",
        "lot1_numero": "",
        "nombre_lots": "0",
        "type_local": "",
        "surface_reelle_bati": "",
        "nombre_pieces_principales": "",
        "nature_culture": "",
        "surface_terrain": "",
        "longitude": "3.88",
        "latitude": "43.61",
    }
    base.update(over)
    return base


def test_group_mutations_merges_rows():
    rows = [
        _row(
            type_local="Maison",
            surface_reelle_bati="120",
            nombre_pieces_principales="5",
            nature_culture="S",
            surface_terrain="400",
        ),
        _row(nature_culture="J", surface_terrain="300"),
        _row(id_parcelle="34172000AB0002", nature_culture="J", surface_terrain="50"),
        _row(id_mutation="2023-9", date_mutation="2023-01-15", valeur_fonciere="100000"),
    ]
    mutations = dvf.group_mutations(rows)
    assert [m["mutation_id"] for m in mutations] == ["2024-1", "2023-9"]
    first = mutations[0]
    assert first["price_eur"] == 250000.0 and first["address"] == "12 RUE TEST"
    assert first["parcels"] == ["34172000AB0001", "34172000AB0002"]
    assert first["buildings"] == [
        {
            "parcel_id": "34172000AB0001",
            "type": "Maison",
            "built_area_m2": 120,
            "rooms": 5,
            "lots": 0,
        }
    ]
    assert len(first["land"]) == 3
    assert mutations[1]["buildings"] == [] and mutations[1]["land"] == []


def test_rows_for_parcel_and_within():
    rows = [_row(), _row(id_parcelle="34172000AB0002", longitude="3.90", latitude="43.61")]
    assert len(dvf.rows_for_parcel(rows, "34172000AB0001")) == 1
    near = dvf.rows_within(rows, 3.88, 43.61, 100)
    assert len(near) == 1 and near[0][1] < 1
