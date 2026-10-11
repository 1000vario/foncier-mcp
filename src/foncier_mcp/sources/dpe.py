"""ADEME energy performance diagnostics (DPE) through the data-fair API. Read on 2026-10-11.

Datasets: dpe03existant (existing dwellings, 15.7 million lines) and dpe02neuf (new dwellings,
1.4 million lines), both updated weekly, Licence Ouverte 2.0. Anonymous limit published in the
API description: 600 requests per 60 seconds.
A DPE is attached to a Base Adresse Nationale identifier (identifiant_ban), so lookups need a
house-number level address.
"""

from __future__ import annotations

from typing import Any

from ..http import Fetcher, FetchResult

SOURCE = "ademe_dpe"
BASE = "https://data.ademe.fr/data-fair/api/v1/datasets"
DATASETS = {"dpe03existant": "existing dwellings", "dpe02neuf": "new dwellings"}
SELECT = ",".join(
    [
        "numero_dpe",
        "date_etablissement_dpe",
        "date_fin_validite_dpe",
        "etiquette_dpe",
        "etiquette_ges",
        "conso_5_usages_par_m2_ep",
        "emission_ges_5_usages_par_m2",
        "adresse_ban",
        "identifiant_ban",
        "code_insee_ban",
        "type_batiment",
        "surface_habitable_logement",
        "annee_construction",
        "periode_construction",
    ]
)


async def diagnostics_for_ban_id(
    fetcher: Fetcher, ban_id: str, *, size: int = 20
) -> tuple[list[dict[str, Any]], list[FetchResult], dict[str, int]]:
    records: list[dict[str, Any]] = []
    results: list[FetchResult] = []
    totals: dict[str, int] = {}
    for dataset, label in DATASETS.items():
        data, result = await fetcher.get_json(
            f"{BASE}/{dataset}/lines",
            source=SOURCE,
            params={"size": size, "qs": f'identifiant_ban:"{ban_id}"', "select": SELECT},
        )
        results.append(result)
        totals[dataset] = int(data.get("total") or 0)
        for line in data.get("results") or []:
            records.append(
                {
                    "dpe_number": line.get("numero_dpe"),
                    "dataset": dataset,
                    "dataset_label": label,
                    "issued_on": line.get("date_etablissement_dpe"),
                    "valid_until": line.get("date_fin_validite_dpe"),
                    "energy_class": line.get("etiquette_dpe"),
                    "ghg_class": line.get("etiquette_ges"),
                    "primary_energy_kwh_m2_year": line.get("conso_5_usages_par_m2_ep"),
                    "ghg_kgco2_m2_year": line.get("emission_ges_5_usages_par_m2"),
                    "building_type": line.get("type_batiment"),
                    "living_area_m2": line.get("surface_habitable_logement"),
                    "construction_year": line.get("annee_construction"),
                    "construction_period": line.get("periode_construction"),
                    "address": line.get("adresse_ban"),
                    "ban_id": line.get("identifiant_ban"),
                    "citycode": line.get("code_insee_ban"),
                }
            )
    records.sort(key=lambda r: r.get("issued_on") or "", reverse=True)
    return records, results, totals
