"""Run the eval suite: 40 public parcels, measured against the recorded truth.

    python evals/run.py --live      fresh cache, every case, the public APIs as they answer today
    python evals/run.py --record    live run of the offline subset, responses kept in evals/recorded
    python evals/run.py --offline   replay of the offline subset from evals/recorded, no network

Writes evals/results/<date>-<mode>.json and .md (or under --out). Every rate is published with its
number of cases. The exit code is 1 when a tool invents a value, when the offline replay misses a
recorded response, or when a tool crashes outside its typed errors.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import shutil
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src"))

from foncier_mcp import __version__, tools  # noqa: E402
from foncier_mcp.config import Settings  # noqa: E402
from foncier_mcp.http import Fetcher  # noqa: E402
from foncier_mcp.models import is_sourced  # noqa: E402
from foncier_mcp.sources.dvf import haversine_m  # noqa: E402

GEOCODE_TOLERANCE_M = 150.0
FIELD_KEYS = {"value", "source", "retrieved_at", "reason"}


class Tally:
    """A rate with its numerator and denominator."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.hit = 0
        self.total = 0
        self.misses: list[str] = []

    def add(self, ok: bool, case_id: str) -> None:
        self.total += 1
        if ok:
            self.hit += 1
        else:
            self.misses.append(case_id)

    def as_dict(self) -> dict[str, Any]:
        rate = round(self.hit / self.total, 4) if self.total else None
        return {"label": self.label, "hit": self.hit, "total": self.total, "rate": rate}


def audit_fields(data: Any, path: str = "data") -> tuple[int, list[str], list[str]]:
    """Count sourced fields; list nulls without reason and leaves outside any sourced field."""
    if isinstance(data, dict) and FIELD_KEYS <= set(data):
        bad = [] if data["value"] is not None or data.get("reason") else [path]
        return 1, bad, []
    fields, nulls, loose = 0, [], []
    if isinstance(data, dict):
        for key, value in data.items():
            f, n, x = audit_fields(value, f"{path}.{key}")
            fields, nulls, loose = fields + f, nulls + n, loose + x
    elif isinstance(data, list):
        for i, value in enumerate(data):
            f, n, x = audit_fields(value, f"{path}[{i}]")
            fields, nulls, loose = fields + f, nulls + n, loose + x
    elif data is not None:
        loose.append(path)
    return fields, nulls, loose


def value_of(envelope: dict[str, Any], *keys: str) -> Any:
    node: Any = envelope.get("data")
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
        if is_sourced(node):
            node = node["value"]
    return node


def error_codes(envelope: dict[str, Any]) -> set[str]:
    return {e.get("code") for e in envelope.get("errors", [])}


class Runner:
    def __init__(self, fetcher: Fetcher, truth: dict[str, Any], mode: str) -> None:
        self.fetcher = fetcher
        self.truth = truth
        self.mode = mode
        self.latency: dict[str, list[float]] = {}
        self.tallies = {
            key: Tally(label)
            for key, label in [
                ("geocoding", "geocode_address: right commune and within 150 m of the parcel"),
                ("parcel_by_point", "find_parcel from a point: Etalab cadastre parcel found"),
                ("parcel_by_address", "find_parcel from an address: expected parcel found"),
                ("dvf_recall", "get_parcel_sales: recorded mutations returned (per mutation)"),
                (
                    "dvf_precision",
                    "get_parcel_sales: returned mutations are recorded (per mutation)",
                ),
                ("dvf_no_sale", "get_parcel_sales: parcels without sale reported with zero sale"),
                (
                    "dvf_not_covered",
                    "get_parcel_sales: uncovered department refused with not_covered",
                ),
                ("zoning_label", "get_zoning: WFS zone label found among returned zones"),
                ("zoning_rnu", "get_zoning: RNU commune reported without zoning, with reason"),
                (
                    "summary_ok",
                    "summarize_parcel: envelope ok, every section answered or explained",
                ),
            ]
        }
        self.inventions: list[str] = []
        self.crashes: list[str] = []
        self.cache_misses: list[str] = []
        self.per_case: list[dict[str, Any]] = []

    async def call(self, name: str, inputs: dict[str, Any], case_id: str) -> dict[str, Any]:
        try:
            out = await tools.run_tool(self.fetcher, name, inputs)
        except Exception as exc:  # noqa: BLE001 - a crash is a finding, not a stop
            self.crashes.append(f"{case_id}:{name}: {type(exc).__name__}: {exc}")
            return {"ok": False, "data": None, "errors": [{"code": "crash"}], "warnings": []}
        self.latency.setdefault(name, []).append(float(out.get("duration_ms") or 0))
        if "offline_cache_miss" in error_codes(out):
            self.cache_misses.append(f"{case_id}:{name}")
        return out

    def invented(self, case_id: str, what: str) -> None:
        self.inventions.append(f"{case_id}: {what}")

    async def run_case(self, case: dict[str, Any]) -> None:
        cid = case["id"]
        truth = self.truth["cases"][cid]
        expected_parcel = truth["parcel_id"]
        parcel_at_point = truth["parcel_at_point"]
        record: dict[str, Any] = {"id": cid, "kind": case["kind"], "checks": {}}
        checks = record["checks"]
        address = case["input"].get("address")
        lon, lat = case["input"]["lon"], case["input"]["lat"]
        rep = truth.get("representative_point") or {"lon": lon, "lat": lat}

        if address:
            out = await self.call("geocode_address", {"address": address}, cid)
            coords = value_of(out, "coordinates")
            city = value_of(out, "citycode")
            ok = bool(
                out.get("ok")
                and city == case["insee"]
                and coords
                and haversine_m(coords["lon"], coords["lat"], rep["lon"], rep["lat"])
                <= GEOCODE_TOLERANCE_M
            )
            self.tallies["geocoding"].add(ok, cid)
            checks["geocoding"] = ok

        if parcel_at_point:
            out = await self.call("find_parcel", {"lon": lon, "lat": lat}, cid)
            found = (value_of(out, "parcel") or {}).get("id")
            ok = found == parcel_at_point
            self.tallies["parcel_by_point"].add(ok, cid)
            checks["parcel_by_point"] = {"ok": ok, "found": found, "expected": parcel_at_point}

        if address and expected_parcel:
            out = await self.call("find_parcel", {"address": address}, cid)
            found = (value_of(out, "parcel") or {}).get("id")
            ok = found == expected_parcel
            self.tallies["parcel_by_address"].add(ok, cid)
            checks["parcel_by_address"] = {"ok": ok, "found": found, "expected": expected_parcel}

        if expected_parcel:
            out = await self.call("get_parcel_sales", {"parcel_id": expected_parcel}, cid)
            dvf_truth = truth["dvf"]
            if not dvf_truth["covered"]:
                refused = (
                    "not_covered" in error_codes(out) and value_of(out, "sales_on_parcel") is None
                )
                self.tallies["dvf_not_covered"].add(refused, cid)
                checks["dvf_not_covered"] = refused
                if value_of(out, "sales_on_parcel"):
                    self.invented(cid, "sales listed in a department DVF does not cover")
            else:
                found_ids = {m["mutation_id"] for m in (value_of(out, "sales_on_parcel") or [])}
                truth_ids = set(dvf_truth["mutation_ids"])
                for mid in truth_ids:
                    self.tallies["dvf_recall"].add(mid in found_ids, f"{cid}:{mid}")
                for mid in found_ids:
                    self.tallies["dvf_precision"].add(mid in truth_ids, f"{cid}:{mid}")
                    if mid not in truth_ids:
                        self.invented(cid, f"mutation {mid} is not in the DVF file")
                if not truth_ids:
                    self.tallies["dvf_no_sale"].add(not found_ids and out.get("ok", False), cid)
                checks["dvf"] = {"found": sorted(found_ids), "expected": sorted(truth_ids)}

            out = await self.call("get_zoning", {"parcel_id": expected_parcel}, cid)
            zoning_truth = truth["zoning"]
            zones = value_of(out, "zones")
            labels = {z.get("label") for z in zones} if isinstance(zones, list) else set()
            if zoning_truth["is_rnu"]:
                ok = (
                    out.get("ok", False)
                    and value_of(out, "municipality", "is_rnu") is True
                    and zones is None
                )
                self.tallies["zoning_rnu"].add(ok, cid)
                checks["zoning_rnu"] = ok
                if labels:
                    self.invented(cid, f"zoning {sorted(labels)} in an RNU commune")
            elif zoning_truth["zones"]:
                truth_labels = {z["libelle"] for z in zoning_truth["zones"]}
                ok = bool(labels & truth_labels)
                self.tallies["zoning_label"].add(ok, cid)
                checks["zoning_label"] = {
                    "ok": ok,
                    "found": sorted(x for x in labels if x),
                    "expected": sorted(truth_labels),
                }
            else:
                checks["zoning_label"] = {"skipped": "WFS returned no zone at the truth point"}

            out = await self.call("summarize_parcel", {"parcel_id": expected_parcel}, cid)
            fields, nulls, loose = audit_fields(out.get("data"))
            section_ok = out.get("ok", False) and not nulls and not loose
            self.tallies["summary_ok"].add(section_ok, cid)
            checks["summary"] = {
                "ok": section_ok,
                "sourced_fields": fields,
                "nulls_without_reason": nulls,
                "unsourced_leaves": loose,
                "errors": sorted(error_codes(out)),
                "warnings": len(out.get("warnings", [])),
            }
            for path in nulls:
                self.invented(cid, f"null without reason at {path}")
            for path in loose:
                self.invented(cid, f"value without source at {path}")
        self.per_case.append(record)

    def latency_table(self) -> dict[str, dict[str, float | int]]:
        table = {}
        for name, values in sorted(self.latency.items()):
            ordered = sorted(values)
            p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
            table[name] = {
                "calls": len(values),
                "median_ms": round(statistics.median(values)),
                "p95_ms": round(p95),
                "max_ms": round(max(values)),
            }
        return table


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Eval results, {report['date']} ({report['mode']})",
        "",
        f"foncier-mcp {report['version']}, {report['cases']} cases, truth recorded "
        f"{report['truth_recorded_at']}.",
        "",
        "| Metric | Hit | Total | Rate |",
        "|---|---:|---:|---:|",
    ]
    for t in report["metrics"].values():
        rate = "n/a" if t["rate"] is None else f"{100 * t['rate']:.1f} %"
        lines.append(f"| {t['label']} | {t['hit']} | {t['total']} | {rate} |")
    lines += [
        "",
        f"Invented values: {len(report['inventions'])} "
        f"(a value asserted where the sources hold none, a null without reason, "
        f"a value without source).",
        f"Crashes outside typed errors: {len(report['crashes'])}. "
        f"Offline cache misses: {len(report['cache_misses'])}.",
        "",
        "| Tool | Calls | Median ms | p95 ms | Max ms |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, row in report["latency_ms"].items():
        lines.append(
            f"| {name} | {row['calls']} | {row['median_ms']} | {row['p95_ms']} | {row['max_ms']} |"
        )
    misses = {k: v["misses"] for k, v in report["metrics"].items() if v.get("misses")}
    if misses:
        lines += ["", "## Misses", ""]
        for key, items in misses.items():
            lines.append(f"- {key}: {', '.join(items)}")
    if report["inventions"]:
        lines += ["", "## Inventions", ""] + [f"- {x}" for x in report["inventions"]]
    if report["crashes"]:
        lines += ["", "## Crashes", ""] + [f"- {x}" for x in report["crashes"]]
    return "\n".join(lines) + "\n"


async def run(args: argparse.Namespace) -> int:
    dataset = json.loads((REPO / "evals" / "dataset.json").read_text(encoding="utf-8"))
    truth = json.loads((REPO / "evals" / "truth" / "truth.json").read_text(encoding="utf-8"))
    cases = dataset["cases"]
    if args.mode in ("offline", "record"):
        cases = [c for c in cases if c.get("offline")]
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]

    temp_dir = None
    if args.mode == "live":
        temp_dir = Path(tempfile.mkdtemp(prefix="foncier-evals-"))
        cache_dir = temp_dir
    else:
        cache_dir = REPO / "evals" / "recorded"
    settings = dataclasses.replace(
        Settings.from_env(),
        cache_dir=cache_dir,
        offline=args.mode == "offline",
        require_api_key=False,
    )
    fetcher = Fetcher(settings)
    runner = Runner(fetcher, truth, args.mode)
    started = time.monotonic()
    try:
        for case in cases:
            print(f"  {case['id']}", file=sys.stderr, flush=True)
            await runner.run_case(case)
    finally:
        await fetcher.aclose()
    if args.mode == "record":
        compressed = fetcher.cache.compress()
        print(f"compressed {compressed} recorded responses", file=sys.stderr)
    if temp_dir:
        shutil.rmtree(temp_dir, ignore_errors=True)

    now = datetime.now(UTC)
    metrics = {}
    for key, tally in runner.tallies.items():
        row = tally.as_dict()
        row["misses"] = tally.misses
        metrics[key] = row
    report = {
        "date": now.date().isoformat(),
        "generated_at": now.replace(microsecond=0).isoformat(),
        "mode": args.mode,
        "version": __version__,
        "cases": len(cases),
        "truth_recorded_at": truth["recorded_at"],
        "wall_seconds": round(time.monotonic() - started, 1),
        "metrics": metrics,
        "latency_ms": runner.latency_table(),
        "inventions": runner.inventions,
        "crashes": runner.crashes,
        "cache_misses": runner.cache_misses,
        "per_case": runner.per_case,
    }
    out_dir = Path(args.out) if args.out else REPO / "evals" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{report['date']}-{args.mode}"
    (out_dir / f"{stem}.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    markdown = render_markdown(report)
    (out_dir / f"{stem}.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    failed = bool(runner.inventions or runner.crashes or runner.cache_misses)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--live", dest="mode", action="store_const", const="live")
    group.add_argument("--record", dest="mode", action="store_const", const="record")
    group.add_argument("--offline", dest="mode", action="store_const", const="offline")
    parser.add_argument("--out", help="results directory (default evals/results)")
    parser.add_argument("--only", nargs="*", help="case ids to run")
    args = parser.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
