import json

import pytest

from foncier_mcp.cache import ResponseCache
from foncier_mcp.errors import NotFound
from foncier_mcp.models import (
    SOURCES,
    collect_sources,
    envelope,
    error_envelope,
    is_sourced,
    missing,
    sourced,
)


def test_sourced_and_missing_shapes():
    field = sourced(1, "foncier_mcp", "2026-10-11T00:00:00+00:00")
    assert is_sourced(field) and field["reason"] is None
    gap = missing("dvf_geo", "why")
    assert is_sourced(gap) and gap["value"] is None and gap["reason"] == "why"


def test_unknown_source_refused():
    with pytest.raises(ValueError):
        sourced(1, "wikipedia", None)
    with pytest.raises(ValueError):
        missing("wikipedia", "x")


def test_envelope_lists_only_used_sources():
    data = {
        "a": sourced({"nested": [missing("ademe_dpe", "r")]}, "apicarto_cadastre", None),
        "plain": 3,
    }
    env = envelope("t", {"x": 1}, data, warnings=["w"], duration_ms=5)
    assert env["ok"] is True and env["warnings"] == ["w"] and env["duration_ms"] == 5
    assert set(env["sources"]) == {"apicarto_cadastre", "ademe_dpe"}
    assert env["sources"]["ademe_dpe"] == SOURCES["ademe_dpe"]
    assert collect_sources(data) == {"apicarto_cadastre", "ademe_dpe"}
    json.dumps(env)


def test_error_envelope():
    err = NotFound("nope", source="dvf_geo", details={"k": 1})
    env = error_envelope("t", {}, err, duration_ms=1)
    assert env["ok"] is False and env["data"] is None and env["sources"] == {}
    assert env["errors"] == [
        {
            "code": "not_found",
            "message": "nope",
            "retryable": False,
            "source": "dvf_geo",
            "details": {"k": 1},
        }
    ]


def test_cache_roundtrip_keeps_only_listed_headers(tmp_path):
    cache = ResponseCache(tmp_path, 1)
    url = "https://example.test/a?b=1"
    assert cache.get(url) is None
    cache.put(url, 200, {"Content-Type": "text/csv", "X-Secret": "no"}, "body")
    hit = cache.get(url)
    assert hit is not None and hit.text == "body" and hit.status == 200
    assert hit.headers == {"content-type": "text/csv"}
    assert hit.fetched_at.endswith("+00:00")


def test_cache_corrupt_entry_is_a_miss(tmp_path):
    cache = ResponseCache(tmp_path, 1)
    url = "https://example.test/x"
    cache.put(url, 200, {}, "ok")
    cache.path_for(url).write_text("{not json", encoding="utf-8")
    assert cache.get(url) is None


def test_cache_compress_in_place_is_transparent(tmp_path):
    cache = ResponseCache(tmp_path, 1)
    cache.put("https://example.test/1", 200, {}, "one")
    cache.put("https://example.test/2", 404, {}, "two")
    assert cache.compress() == 2
    assert cache.compress() == 0
    assert cache.path_for("https://example.test/1").read_bytes()[:2] == b"\x1f\x8b"
    assert cache.get("https://example.test/1").text == "one"
    assert cache.get("https://example.test/2").status == 404


def test_cache_expiry_and_ignore_ttl(tmp_path):
    cache = ResponseCache(tmp_path, 1)
    url = "https://example.test/old"
    cache.put(url, 200, {}, "old")
    path = cache.path_for(url)
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["fetched_at"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(entry), encoding="utf-8")
    assert cache.get(url) is None
    stale = cache.get(url, ignore_ttl=True)
    assert stale is not None and stale.text == "old"
