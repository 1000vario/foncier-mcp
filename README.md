# foncier-mcp

[![CI](https://github.com/1000vario/foncier-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/1000vario/foncier-mcp/actions/workflows/ci.yml)

An [MCP](https://modelcontextprotocol.io) server that gives an AI agent French public land data
for a parcel, an address or a point: cadastral parcel, recorded sales (DVF), planning zone and
public-utility easements, energy performance certificates, natural and technological risks.

Every value comes with its source and the date it was retrieved. When a source has nothing, the
field is `null` with a written reason. The server never fills a gap with a guess.

- 7 typed, read-only tools, stdio and streamable HTTP transports.
- Public, free sources only: Geoplateforme geocoder, IGN API Carto (cadastre, urbanism),
  DVF files on data.gouv.fr, ADEME DPE, Georisques.
- A reproducible eval suite: 40 public parcels, truth recorded from official open data, rates
  published with their number of cases and date.
- Free tier online (200 calls a day per key). A paid tier is designed but switched off, see below.

## Tools

| Tool | Input (one locator) | Returns |
|---|---|---|
| `geocode_address` | `address`, optional `limit` | best match, coordinates, INSEE code, candidates |
| `find_parcel` | `address`, or `lon` + `lat`, or `parcel_id` | parcel id, commune, section, number, area, centroid, geometry; an address point on the road takes the nearest parcel within 10 m, with a warning |
| `get_parcel_sales` | locator, optional `radius_m` (0 to 2000), `years` | DVF mutations on the parcel, and nearby when a radius is given, with price, date, buildings, land |
| `get_zoning` | locator | planning document, zone(s) at the parcel, prescriptions, public-utility easements; RNU communes are reported as such |
| `get_energy_diagnostics` | `address` or `ban_id` | DPE records for that address (existing and new buildings) |
| `get_risks` | locator or `insee` | commune-level risks (inventory, seismic zone, radon, CatNat orders, flood territories) and point-level risks (clay shrink-swell, flood zoning, cavities, ground movements, classified installations) |
| `summarize_parcel` | locator, optional `radius_m` | the four sections above for one parcel, each independent: a failing source is reported, the others still answer |

A locator is exactly one of `parcel_id` (14 characters, for example `34172000DS0638`), `address`,
or the pair `lon` + `lat` (WGS84). Giving two locators is refused.

### Response shape

```json
{
  "ok": true,
  "tool": "find_parcel",
  "input": {"lon": 3.8946, "lat": 43.5986},
  "data": {
    "parcel": {
      "value": {"id": "34172000DS0638", "code_insee": "34172", "commune": "Montpellier", "area_m2": 4553},
      "source": "apicarto_cadastre",
      "retrieved_at": "2026-10-11T17:12:03+00:00",
      "reason": null
    }
  },
  "warnings": [],
  "errors": [],
  "sources": {"apicarto_cadastre": {"name": "IGN API Carto, cadastre module", "url": "...", "licence": "..."}},
  "generated_at": "2026-10-11T17:12:03+00:00",
  "duration_ms": 343
}
```

A field with no value is `{"value": null, "source": "...", "retrieved_at": null, "reason": "why"}`.
Typed errors carry a `code`: `invalid_input`, `not_found`, `not_covered` (for example DVF in
Alsace-Moselle), `source_unavailable` (retryable), `offline_cache_miss`, `unauthorized`,
`quota_exceeded`, `feature_disabled`.

## Install in under ten minutes

Requirements: Python 3.11 or newer. Nothing else: no database, no API key for the public sources.

Option A, no clone, with [uv](https://docs.astral.sh/uv/):

```bash
uvx --from git+https://github.com/1000vario/foncier-mcp foncier-mcp tools
```

Option B, with pip:

```bash
pip install git+https://github.com/1000vario/foncier-mcp
foncier-mcp tools
```

Option C, from a clone, for development:

```bash
git clone https://github.com/1000vario/foncier-mcp
cd foncier-mcp
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q
```

Try one call without any MCP client:

```bash
foncier-mcp call find_parcel --json '{"lon": 3.8946, "lat": 43.5986}'
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "foncier": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/1000vario/foncier-mcp", "foncier-mcp"]
    }
  }
}
```

### Claude Code

```bash
claude mcp add foncier -- uvx --from git+https://github.com/1000vario/foncier-mcp foncier-mcp
```

For the hosted HTTP service, once you have a key:

```bash
claude mcp add --transport http foncier https://HOST/mcp --header "x-api-key: fm_..."
```

### Codex

In `~/.codex/config.toml`:

```toml
[mcp_servers.foncier]
command = "uvx"
args = ["--from", "git+https://github.com/1000vario/foncier-mcp", "foncier-mcp"]
```

### Any streamable HTTP client

Endpoint `https://HOST/mcp`, JSON responses, stateless. Send the key in the `x-api-key` header.
`GET /health` answers without a key.

The hosted instance is `https://foncier.guillaume-couillard.com/mcp`; its `/health` says whether
it is up. The free key is sent by email by hand, see [Keys](#keys).

## Data sources

All sources were read on 2026-10-11, including their documentation pages. The dates, limits and
licences below are what was observed that day; they are not promises.

| Source | Used for | Access | Licence | Limits observed |
|---|---|---|---|---|
| Geoplateforme geocoder (IGN), `data.geopf.fr/geocodage` | `geocode_address`, reverse geocoding for DPE | HTTP JSON, indexes address and parcel | Etalab Licence Ouverte 2.0 | `x-ratelimit-limit-second: 1`, `ratelimit-limit: 50`. The legacy `api-adresse.data.gouv.fr` is deprecated (sunset 2026-01-31) and is not used |
| IGN API Carto, cadastre module | `find_parcel` | HTTP GeoJSON, by point or by INSEE + section + number | Licence Ouverte 2.0 (Etalab cadastre) | up to 1000 features per call |
| IGN API Carto, GPU module (Geoportail de l'urbanisme) | `get_zoning` | `municipality`, `document`, `zone-urba`, `prescription-surf`, `assiette-sup-s`, `secteur-cc` | Licence Ouverte 2.0 | `urlfic` of the regulation files was empty on every zone read that day, so regulation links are `null` with the file name kept in `regulation_file` |
| DVF, geo-dvf files, `files.data.gouv.fr/geo-dvf/latest/csv/{year}/communes/{dep}/{insee}.csv` | `get_parcel_sales` | one CSV per commune and year, 2021 to 2025, last modified 2026-05-18 | Licence Ouverte 2.0 | not covered: departments 57, 67, 68 (Livre foncier) and 976. The Cerema DVF+ API was unreachable on 2026-10-11, so the files are the primary and only DVF source |
| ADEME, `data.ademe.fr/data-fair` datasets `dpe03existant`, `dpe02neuf` | `get_energy_diagnostics` | HTTP JSON, filtered on the BAN address id | Licence Ouverte 2.0 | 600 requests per 60 s anonymous; weekly refresh |
| Georisques API v1, `georisques.gouv.fr/api/v1` | `get_risks` | `gaspar/risques`, `zonage_sismique`, `radon`, `gaspar/catnat`, `gaspar/tri`, `rga`, `tri_zonage`, `cavites`, `mvt`, `installations_classees` | Licence Ouverte 2.0 | the documentation page was geo-restricted that day; endpoints were read live. The `libelle_tri` field contains a mis-encoded dash which the server replaces |

The server throttles itself per host and keeps a dated local cache (default seven days, under
`~/.cache/foncier-mcp`). Each response says when the upstream data was fetched.

## Evals

```bash
python evals/run.py --live       # every case against the public APIs, fresh cache
python evals/run.py --offline    # replay of the 22 recorded cases, no network, runs in CI
```

The dataset ([evals/dataset.json](evals/dataset.json)) holds 40 public parcels in 12 departments
(13, 23, 31, 33, 34, 35, 48, 55, 59, 67, 69, 75), urban, periurban and rural: 24 with at least
one DVF sale, 14 without any (public buildings, town halls and village parcels, 6 of them in
communes under the RNU with no digitised planning document), 2 in a department DVF does not
cover. None belongs to the author or to his family.

The expected truth is recorded by [evals/truth/record.py](evals/truth/record.py) from official open
data through a different path than the server: Etalab cadastre GeoJSON files with a pure Python
point-in-polygon for the parcel, the geo-dvf CSV files for the mutations, the Geoplateforme WFS
(`wfs_du:zone_urba`, `wfs_du:municipality`) for the zoning. Two honest limits: the DVF check reads
the same files the server reads, so it measures parsing and grouping, not an independent register;
and the zoning truth is taken at the parcel's representative point, so a parcel split between two
zones can count as a miss.

What is measured: geocoding (right commune and within 150 m of the parcel), parcel found from a
point and from an address, DVF recall and precision per mutation, zero sale reported on parcels
without sale, refusal in uncovered departments, zoning label, RNU reported without zoning, and the
absence of invention: a value asserted where the sources hold none, a null without reason or a
value without source counts as a grave failure and fails the run. Latency is reported per tool.

<!-- EVAL_RESULTS_START -->
Measured on 2026-10-11 against the live public APIs (`python evals/run.py --live`, fresh
cache), 40 cases, truth recorded 2026-10-11T17:40:21+00:00, wall time
206 s. Report: [evals/results/2026-10-11-live.md](evals/results/2026-10-11-live.md).

| Metric | Hit | Total | Rate |
|---|---:|---:|---:|
| geocode_address: right commune and within 150 m of the parcel | 34 | 34 | 100.0 % |
| find_parcel from a point: Etalab cadastre parcel found | 40 | 40 | 100.0 % |
| find_parcel from an address: expected parcel found | 28 | 34 | 82.3 % |
| get_parcel_sales: recorded mutations returned (per mutation) | 31 | 31 | 100.0 % |
| get_parcel_sales: returned mutations are recorded (per mutation) | 31 | 31 | 100.0 % |
| get_parcel_sales: parcels without sale reported with zero sale | 14 | 14 | 100.0 % |
| get_parcel_sales: uncovered department refused with not_covered | 2 | 2 | 100.0 % |
| get_zoning: WFS zone label found among returned zones | 33 | 33 | 100.0 % |
| get_zoning: RNU commune reported without zoning, with reason | 6 | 6 | 100.0 % |
| summarize_parcel: envelope ok, every section answered or explained | 40 | 40 | 100.0 % |

Invented values: 0. Crashes outside typed errors: 0.
`summarize_parcel` latency over 40 live calls: median 2206 ms, p95 3129 ms,
max 3610 ms.

Misses on `find_parcel` from an address (6 of 34): `sale-33063-1` (neighbouring parcel); `sale-35051-1` (neighbouring parcel, same recorded sale); `sale-23030-1` (neighbouring parcel); `sale-55463-1` (neighbouring parcel); `public-31555` (open square, no parcel within 10 m, null returned with a reason); `public-35238` (neighbouring parcel).
The BAN point lands inside, or nearest to, a neighbouring parcel, or in the middle of a square.
The server returns what the point says, with the candidates in a warning, rather than
guessing the intended parcel.

The offline replay of the 22 recorded cases (`python evals/run.py --offline`, run in CI on
every push) gave 13/16 on that metric and 100 % on the others, with
0 invented values and 0 cache misses.
<!-- EVAL_RESULTS_END -->

Full reports with per-case detail are in [evals/results](evals/results). A weekly GitHub Action
reruns the live suite and keeps its report as an artifact.

## Service tiers

The hosted server serves two tiers from one code path:

| | Free | Paid (designed, switched off) |
|---|---|---|
| Access | key by email, `x-api-key` header | key by email |
| Quota | 200 calls a day | 5000 calls a day (`FONCIER_MCP_PAID_DAILY_QUOTA`) |
| Tools | all 7, including `summarize_parcel` | the 7 tools plus `export_parcel_pdf` and `watch_parcel` |
| Payment | none | none yet |

The counter and the tier check are implemented and tested. The paid tier is behind
`PAID_TIER_ENABLED=false`: with the flag off, paid keys cannot be issued, paid keys that exist
are served as free with a warning, and the two paid tools are not even listed. It stays off until
a legal question (combining this activity with the author's employment contract) is settled, and
the two paid tools have no implementation behind them yet. Turning the flag on is a decision for
the author, not a deployment detail.

Over stdio (Claude Desktop, Claude Code, Codex running the server locally) there is no key and no
quota: the counter only applies to HTTP requests.

### Keys

```bash
foncier-mcp keys issue --email someone@example.org        # prints the key once
foncier-mcp keys list
foncier-mcp keys revoke fm_abcdefgh
foncier-mcp keys export > keys-export.json                # hashes only, for FONCIER_MCP_KEYS_JSON
```

Keys are stored hashed (SHA-256) in `FONCIER_MCP_KEYS_FILE`; daily usage in
`FONCIER_MCP_USAGE_FILE`, reset at UTC midnight. The free key is sent by email by hand today.

A host without a persistent disk gets its keys through `FONCIER_MCP_KEYS_JSON`, the output of
`keys export` placed in a secret environment variable. The store is then read-only: `issue` and
`revoke` refuse, and a revocation is an export followed by a redeploy. The export holds hashes
and emails, never a key.

## Running the HTTP server

```bash
foncier-mcp serve --transport http --host 0.0.0.0 --port 8080
```

Environment (see [.env.example](.env.example)):

| Variable | Default | Meaning |
|---|---|---|
| `FONCIER_MCP_CACHE_DIR` | `~/.cache/foncier-mcp` | dated response cache |
| `FONCIER_MCP_CACHE_TTL_HOURS` | `168` | cache lifetime |
| `FONCIER_MCP_OFFLINE` | `0` | `1` answers from the cache only |
| `FONCIER_MCP_REQUIRE_API_KEY` | `1` | HTTP requests need a key; stdio never does |
| `FONCIER_MCP_KEYS_FILE`, `FONCIER_MCP_USAGE_FILE` | `~/.config/foncier-mcp/...` | key and usage stores |
| `FONCIER_MCP_KEYS_JSON` | empty | output of `keys export` for a host without a disk; when set the key store is read-only and `FONCIER_MCP_KEYS_FILE` is ignored |
| `FONCIER_MCP_FREE_DAILY_QUOTA` | `200` | free tier quota |
| `PAID_TIER_ENABLED` | `false` | paid tier switch, see above |
| `FONCIER_MCP_ALLOWED_HOSTS` | empty | comma-separated hosts for DNS-rebinding protection; empty accepts any host, which a deployment behind a proxy needs |

### Container

```bash
docker build -t foncier-mcp .
docker run -p 8080:8080 -v foncier-data:/data foncier-mcp
curl http://localhost:8080/health
```

CI builds the image and checks `/health` on every push.

### Hosting, measured on 2026-10-11

Target: a European provider, France for the data locality. Prices read on scaleway.com that day,
excluding VAT, Paris region:

| Option | Monthly price read | Fit |
|---|---|---|
| Scaleway Serverless Containers | 0 EUR within the free allowance (400,000 GB-s and 200,000 vCPU-s a month), then 0.000002 EUR per GB-s and 0.00001 EUR per vCPU-s | the free tier of this service fits inside the allowance at 200 calls a day per key for a handful of keys |
| Scaleway Instance STARDUST1-S | about 0.43 EUR | smallest VM, limited availability |
| Scaleway Instance DEV1-S | about 6.55 EUR | comfortable for the container plus its cache |
| Scaleway Instance PLAY2-PICO | about 10.42 EUR | alternative with newer hardware |

Chosen: Serverless Containers in fr-par, 256 MB, one instance at most, scale to zero, behind
`foncier.guillaume-couillard.com`. Worst case if someone keeps the single instance busy all month
(31 days, 256 MB, 140 mvCPU set explicitly because Scaleway's default when the client sets
nothing is 1000 mvCPU): about 2.3 EUR. The account, the payment method and the
subdomain belong to the author.

### Deployment

The image is built by [publish-image.yml](.github/workflows/publish-image.yml) after a green CI
run on `main` and pushed to `ghcr.io/1000vario/foncier-mcp:latest` (linux/amd64, public).
Scaleway pulls it from there: no Scaleway registry, no Scaleway secret in GitHub.

The author deploys from a Windows terminal with the scripts in [deploy/scaleway](deploy/scaleway):

```powershell
.\deploy\scaleway\install-credentials.ps1 -Provider scaleway     # asks for the key, stores it encrypted (DPAPI)
.\deploy\scaleway\install-credentials.ps1 -Provider cloudflare   # optional, lets the script create the CNAME
foncier-mcp keys export > keys-export.json
.\deploy\scaleway\deploy.ps1 -KeysJsonPath .\keys-export.json
```

`deploy.ps1` creates or updates the namespace and the container (port 8080, 256 MB, 140 mvCPU,
min 0, max 1, public, `FONCIER_MCP_REQUIRE_API_KEY=1`, `PAID_TIER_ENABLED=false`,
`FONCIER_MCP_KEYS_JSON` as a secret variable; `FONCIER_MCP_ALLOWED_HOSTS` stays empty because DNS
rebinding protection guards local servers, not a public endpoint behind the provider's proxy),
waits for `ready`, checks `/health` on the Scaleway endpoint, makes sure the CNAME
`foncier.guillaume-couillard.com` points at that endpoint without proxy so that the HTTP-01
challenge reaches Scaleway, registers the custom domain and waits for its certificate. It prints
identifiers and statuses, never a key. The same steps by hand in the Scaleway console are in
[deploy/scaleway/README.md](deploy/scaleway/README.md).

What this hosting does not give:

- No persistent disk. The cache and the usage counter live in the instance and vanish when it
  scales to zero after about fifteen minutes without a request. The daily quota is therefore
  enforced per warm instance: an upper bound on cost and abuse, not an exact daily count per key.
  A durable counter would need Object Storage (Standard One Zone 0.00803 EUR per GB per month,
  Multi-AZ 0.01606 EUR, read on 2026-10-11, no free storage allowance) and code that does not
  exist yet.
- Keys are read-only in the container. Issuing or revoking one is `keys issue` or `keys revoke`
  on the author's machine, then `keys export` and `deploy.ps1` again.
- Requests reach the public APIs from Scaleway's addresses with the per-host rate limiting of the
  server; every caller shares it.

## Security

- No secret in the repository. Keys are hashed at rest. `.env` files are ignored.
- The deployment scripts keep the provider keys encrypted with Windows DPAPI under the author's
  profile, call the provider APIs over TLS and print statuses and identifiers only.
- Before every push, the author runs a secret scan outside CI (`brain scan-secrets --repo`, a
  local tool); CI runs lint, tests and the offline evals.
- The server is read-only towards its sources and sends them nothing but the lookup parameters.

## Known limits

- Paris, Lyon and Marseille: API Carto keys their parcels by the parent commune (75056, 69123,
  13055) plus an arrondissement code, while the parcel id keeps the arrondissement INSEE code
  (75111000AP0059). The server translates the lookup by id and reports the arrondissement
  code, which is the one the DVF files and the GPU use. Georisques knows the Paris arrondissement
  codes but not those of Lyon and Marseille: their commune-level risks are read for the parent
  commune, and the response says so in a warning. Both found by the live evals on 2026-10-11.
- Address points from the BAN often sit on the road axis, outside every parcel. `find_parcel`
  then takes the nearest parcel within 10 m and says so in a warning that lists the
  candidates. When the point lands inside a neighbouring parcel, the neighbour is returned:
  the eval table counts those cases as misses.
- The Geoplateforme reverse geocoder returns the nearest address, not the address inside the
  parcel; `get_energy_diagnostics` through `summarize_parcel` only keeps a housenumber match within
  30 m and otherwise says so.
- Regulation PDFs of planning zones are named but not linked, because the GPU API returned empty
  `urlfic` fields on the day of reading.
- DVF publishes nothing for Alsace-Moselle (Livre foncier) and Mayotte; the tool answers
  `not_covered`.
- Georisques labels come as published, including occasional empty fields.
- Rates in the eval table hold for the day they were measured; the public APIs change.

## Licence

Code: MIT, see [LICENSE](LICENSE). Data returned by the server stays under its own licence,
Etalab Licence Ouverte 2.0 for every source listed above, which requires naming the producer:
IGN, DGFiP (DVF), ADEME, BRGM and the ministries behind Georisques, and the Geoportail de
l'urbanisme contributors.
