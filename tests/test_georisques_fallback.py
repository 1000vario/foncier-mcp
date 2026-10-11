"""Georisques commune lookups for arrondissement codes, with a stub fetcher: no network."""

from foncier_mcp import tools
from foncier_mcp.sources import georisques

EMPTY = {"results": 0, "data": []}


class StubFetcher:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    async def get_json(self, url, *, source, params):
        self.calls.append((url.rsplit("/", 1)[1], params["code_insee"]))
        return self.answers.get(params["code_insee"], EMPTY), None


async def test_lyon_arrondissement_falls_back_to_the_parent_commune():
    row = {"code_insee": "69123", "code_zone": "2", "zone_sismicite": "2 - FAIBLE"}
    fetcher = StubFetcher({"69123": {"results": 1, "data": [row]}})
    out = await georisques.by_commune(fetcher, "69383")
    assert out["seismic_zone"]["insee"] == "69123"
    assert out["seismic_zone"]["data"]["data"] == [row]
    assert ("zonage_sismique", "69383") in fetcher.calls
    assert ("zonage_sismique", "69123") in fetcher.calls


async def test_paris_arrondissement_known_to_georisques_is_not_retried():
    row = {"code_insee": "75111", "code_zone": "1", "zone_sismicite": "1 - TRES FAIBLE"}
    fetcher = StubFetcher({"75111": {"results": 1, "data": [row]}})
    out = await georisques.by_commune(fetcher, "75111")
    assert out["seismic_zone"]["insee"] == "75111"
    assert ("zonage_sismique", "75056") not in fetcher.calls


async def test_plain_commune_is_never_retried():
    fetcher = StubFetcher({})
    out = await georisques.by_commune(fetcher, "34172")
    assert {c[1] for c in fetcher.calls} == {"34172"}
    assert out["radon_potential"]["insee"] == "34172"


def test_empty_risk_field_carries_a_reason():
    call = {"data": EMPTY, "result": None, "error": None, "insee": "69383"}
    out = tools._risk_field(call, lambda data: None, "Georisques returned no seismic zone")
    assert out["value"] is None
    assert out["reason"] == "Georisques returned no seismic zone"
