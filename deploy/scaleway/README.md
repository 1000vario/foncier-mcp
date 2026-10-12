# Deploying foncier-mcp on Scaleway Serverless Containers

Everything here is run by the author from a Windows terminal. The repository holds no provider
key and no GitHub secret: the image is public on GitHub Packages and Scaleway pulls it from there.

## What runs where

| Piece | Where | Who creates it |
|---|---|---|
| Image `ghcr.io/1000vario/foncier-mcp:latest` | GitHub Packages, linux/amd64 | [publish-image.yml](../../.github/workflows/publish-image.yml) after a green CI run on `main` |
| Namespace `foncier-mcp`, container `foncier-mcp` | Scaleway Serverless Containers, fr-par | `deploy.ps1` |
| CNAME `foncier.guillaume-couillard.com` | Cloudflare DNS, DNS only (grey cloud) | `deploy.ps1` with a Cloudflare token, or the author by hand |
| Custom domain and certificate | Scaleway, HTTP-01 challenge | `deploy.ps1` |

## Before the first run

1. Check that the package is public. On 2026-10-11 the image was publicly pullable right after
   the first workflow run, without any manual change: the package is linked to this public
   repository. If a later check fails, open the package `foncier-mcp` under your GitHub profile,
   Package settings, Danger Zone, Change visibility, Public. `deploy.ps1` checks this with an
   anonymous pull token and stops if the image is not public.
2. Create a Scaleway IAM application dedicated to this deployment (for instance
   `foncier-mcp-deploy`), attach a policy with `ContainersFullAccess` scoped to the project that
   will host the container, generate an API key for it. Keep the project ID at hand.
3. Optional: create a Cloudflare API token with Zone / DNS / Edit limited to the zone
   `guillaume-couillard.com`. Without it the script prints the CNAME to create and waits.
4. Store the keys encrypted:

```powershell
.\deploy\scaleway\install-credentials.ps1 -Provider scaleway
.\deploy\scaleway\install-credentials.ps1 -Provider cloudflare   # optional
.\deploy\scaleway\install-credentials.ps1 -Status                # read-only check
```

The store is `%LOCALAPPDATA%\foncier-mcp\credentials\`, encrypted with Windows DPAPI for the
current account, directory restricted to that account and SYSTEM.

## Deploy or update

```powershell
foncier-mcp keys export > keys-export.json        # hashes and emails only, no key
.\deploy\scaleway\deploy.ps1 -KeysJsonPath .\keys-export.json
.\deploy\scaleway\deploy.ps1 -DryRun              # read-only: shows what exists and what would change
```

`deploy.ps1`:

1. Checks that the image is publicly pullable.
2. Finds or creates the namespace, then the container: image above, port 8080, 256 MB and
   140 mvCPU (the console pairing for that memory), `min_scale` 0, `max_scale` 1, public,
   environment `FONCIER_MCP_REQUIRE_API_KEY=1`, `PAID_TIER_ENABLED=false`,
   `FONCIER_MCP_FREE_DAILY_QUOTA=200`, secret environment `FONCIER_MCP_KEYS_JSON` from the
   export file.
   On an existing container it updates image, limits, variables and redeploys.
   HTTPS connections only is set at creation; on an existing container it is read back and
   switched on when off, so plain HTTP is redirected to HTTPS.
3. Waits for status `ready` and reads the default endpoint. `FONCIER_MCP_ALLOWED_HOSTS` stays
   empty: DNS rebinding protection guards local servers, not a public endpoint behind a proxy.
4. Checks `/health` on the default endpoint, and that `http://` on it answers a redirect to
   HTTPS rather than the service.
5. Resolves the CNAME on 1.1.1.1 and 8.8.8.8. Missing: creates it through Cloudflare when a token
   is stored (proxy off, so the HTTP-01 challenge reaches Scaleway), otherwise prints it and stops.
6. Registers the custom domain on the container and waits for `ready`. Scaleway must complete the
   challenge within three minutes; otherwise the domain is in `error` and the script prints
   `error_message`.
7. Checks `https://foncier.guillaume-couillard.com/health`, the same redirect on
   `http://foncier.guillaume-couillard.com`, and prints a summary.

Nothing printed is secret: identifiers, statuses, hostnames.

## The same by hand, in the Scaleway console

1. Serverless, Containers, Create namespace, region Paris, name `foncier-mcp`.
2. Deploy container: external registry, image `ghcr.io/1000vario/foncier-mcp:latest`, port 8080,
   256 MB with 140 mvCPU, min scale 0, max scale 1, public, timeout default.
3. Environment variables: `FONCIER_MCP_REQUIRE_API_KEY=1`, `PAID_TIER_ENABLED=false`,
   `FONCIER_MCP_FREE_DAILY_QUOTA=200`.
   Secret: `FONCIER_MCP_KEYS_JSON` = the content of `keys-export.json`.
4. Container, Security tab, HTTPS connections only: on.
   Wait for Ready, open `<default endpoint>/health`.
5. Cloudflare, DNS, add CNAME `foncier` pointing at the default endpoint host, proxy off.
6. Container, Endpoints, Add custom domain `foncier.guillaume-couillard.com`, wait for Ready.

## Known behaviours of this hosting

- No persistent disk. The cache and the daily counter live in the instance and reset when it
  scales to zero after about fifteen minutes without a request. The quota is enforced per warm
  instance: an upper bound, not an exact count per key and day.
- Keys are read-only in the container. Issue or revoke on your machine, export, deploy again.
- A pending custom domain answers 404 until its certificate is issued.
- The script sets 140 mvCPU explicitly: the limitations page gives 1000 mvCPU as the default when
  the client sets nothing, which would multiply the worst-case cost in the main README by about
  ten. Check the value in the console after the first deployment.
