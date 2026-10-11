"""Response envelope and the sourced-field convention.

Every value a tool returns is wrapped as {"value", "source", "retrieved_at", "reason"}.
A missing value is never guessed: value is null and reason says why.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .errors import FoncierError

# Identifiers used in the "source" field, with the metadata published in the README.
SOURCES: dict[str, dict[str, str]] = {
    "geoplateforme_geocodage": {
        "name": "IGN Geoplateforme geocoding service (Base Adresse Nationale index)",
        "url": "https://data.geopf.fr/geocodage",
        "licence": "Licence Ouverte / Open Licence 2.0 (Etalab)",
    },
    "apicarto_cadastre": {
        "name": "IGN API Carto, cadastre module (Parcellaire Express PCI)",
        "url": "https://apicarto.ign.fr/api/cadastre",
        "licence": "Licence Ouverte / Open Licence 2.0 (Etalab)",
    },
    "apicarto_gpu": {
        "name": "IGN API Carto, urbanisme module (Geoportail de l urbanisme)",
        "url": "https://apicarto.ign.fr/api/gpu",
        "licence": "Licence Ouverte / Open Licence 2.0 (Etalab)",
    },
    "dvf_geo": {
        "name": "Demandes de valeurs foncieres geolocalisees (DGFiP data, Etalab files)",
        "url": "https://files.data.gouv.fr/geo-dvf/latest/csv/",
        "licence": "Licence Ouverte / Open Licence 2.0 (Etalab)",
    },
    "ademe_dpe": {
        "name": "ADEME, diagnostics de performance energetique (existing and new dwellings)",
        "url": "https://data.ademe.fr/datasets/dpe03existant",
        "licence": "Licence Ouverte / Open Licence 2.0 (Etalab)",
    },
    "georisques": {
        "name": "Georisques API v1 (BRGM and Ministere de la Transition ecologique)",
        "url": "https://georisques.gouv.fr/api/v1",
        "licence": "Licence Ouverte / Open Licence 2.0 (Etalab), per the Georisques site footer",
    },
    "foncier_mcp": {
        "name": "Computed by foncier-mcp from the sources above (geometry, distances, grouping)",
        "url": "https://github.com/1000vario/foncier-mcp",
        "licence": "MIT",
    },
}


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def sourced(value: Any, source: str, retrieved_at: str | None) -> dict[str, Any]:
    """Wrap a value that really came from a source."""
    if source not in SOURCES:
        raise ValueError(f"unknown source id: {source}")
    return {"value": value, "source": source, "retrieved_at": retrieved_at, "reason": None}


def missing(source: str, reason: str) -> dict[str, Any]:
    """Wrap an absent value with the reason it is absent."""
    if source not in SOURCES:
        raise ValueError(f"unknown source id: {source}")
    return {"value": None, "source": source, "retrieved_at": None, "reason": reason}


def is_sourced(field: Any) -> bool:
    return (
        isinstance(field, dict)
        and set(field.keys()) >= {"value", "source", "retrieved_at", "reason"}
        and field["source"] in SOURCES
    )


def collect_sources(data: Any, acc: set[str] | None = None) -> set[str]:
    """Walk a payload and collect every source id it references."""
    if acc is None:
        acc = set()
    if isinstance(data, dict):
        if is_sourced(data):
            acc.add(data["source"])
            collect_sources(data["value"], acc)
        else:
            for v in data.values():
                collect_sources(v, acc)
    elif isinstance(data, list):
        for v in data:
            collect_sources(v, acc)
    return acc


def envelope(
    tool: str,
    inputs: dict[str, Any],
    data: dict[str, Any],
    *,
    warnings: list[str] | None = None,
    errors: list[dict[str, Any]] | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    used = sorted(collect_sources(data))
    return {
        "ok": True,
        "tool": tool,
        "input": inputs,
        "data": data,
        "warnings": warnings or [],
        "errors": errors or [],
        "sources": {sid: SOURCES[sid] for sid in used},
        "generated_at": now_iso(),
        "duration_ms": duration_ms,
    }


def error_envelope(
    tool: str, inputs: dict[str, Any], err: FoncierError, *, duration_ms: int | None = None
) -> dict[str, Any]:
    return {
        "ok": False,
        "tool": tool,
        "input": inputs,
        "data": None,
        "warnings": [],
        "errors": [err.to_dict()],
        "sources": {},
        "generated_at": now_iso(),
        "duration_ms": duration_ms,
    }
