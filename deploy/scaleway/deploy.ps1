<#
.SYNOPSIS
Creates or updates the foncier-mcp Serverless Container on Scaleway and its custom domain.

.DESCRIPTION
Idempotent. Needs the Scaleway key stored by install-credentials.ps1 (an IAM application with
ContainersFullAccess on the project). With a stored Cloudflare token it also creates or fixes the
CNAME; without it, it prints the record to create and stops until the name resolves.
Prints identifiers, statuses and hostnames only, never a key.

Steps: image publicly pullable, namespace, container (create or update and redeploy), wait for
ready, /health on the default endpoint, CNAME check on 1.1.1.1 and 8.8.8.8, custom domain with
its certificate, /health on the custom hostname, summary.

.PARAMETER KeysJsonPath
File produced by `foncier-mcp keys export`. Its content becomes the secret variable
FONCIER_MCP_KEYS_JSON. Without it an existing container keeps its current keys and a new one
starts with none: every call then answers unauthorized.

.PARAMETER DryRun
Read-only: shows what exists and what would be created or changed. No write on any provider.

.EXAMPLE
foncier-mcp keys export > keys-export.json
.\deploy.ps1 -KeysJsonPath .\keys-export.json
.\deploy.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string]$Hostname = 'foncier.guillaume-couillard.com',
    [string]$Region = 'fr-par',
    [string]$Image = 'ghcr.io/1000vario/foncier-mcp:latest',
    [string]$NamespaceName = 'foncier-mcp',
    [string]$ContainerName = 'foncier-mcp',
    [string]$KeysJsonPath,
    [int]$MemoryMB = 256,
    [int]$MvCpu = 140,
    [int]$MaxScale = 1,
    [int]$FreeDailyQuota = 200,
    [switch]$SkipDomain,
    [switch]$DryRun
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$StoreDirectory = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'foncier-mcp\credentials'
$ScwApi = "https://api.scaleway.com/containers/v1/regions/$Region"
$CfApi = 'https://api.cloudflare.com/client/v4'
$script:ScwHeaders = @{}
$script:StopEarly = $false
$Summary = [ordered]@{ hostname = $Hostname; region = $Region; image = $Image; dry_run = [bool]$DryRun }

function Step([string]$Text) { Write-Host ('== ' + $Text) -ForegroundColor Cyan }
function Note([string]$Text) { Write-Host ('   ' + $Text) }

function Load-Store([string]$Name) {
    $path = Join-Path $StoreDirectory ($Name + '.credentials.xml')
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    return Import-Clixml -LiteralPath $path
}

function Get-ErrorDetail($ErrorRecord) {
    $code = $null
    $message = $ErrorRecord.Exception.Message
    $exception = $ErrorRecord.Exception
    if ($null -ne $exception.PSObject.Properties['Response'] -and $null -ne $exception.Response) {
        try { $code = [int]$exception.Response.StatusCode } catch { $code = $null }
    }
    if ($null -ne $ErrorRecord.ErrorDetails -and $ErrorRecord.ErrorDetails.Message) {
        $raw = $ErrorRecord.ErrorDetails.Message
        try {
            $parsed = ConvertFrom-Json $raw
            if ($null -ne $parsed.PSObject.Properties['message']) { $message = $parsed.message }
            elseif ($null -ne $parsed.PSObject.Properties['errors']) {
                $message = (@($parsed.errors) | ForEach-Object { $_.message }) -join '; '
            } else { $message = $raw }
        } catch { $message = $raw }
    }
    return [ordered]@{ http = $code; message = $message }
}

function Invoke-Api([string]$Method, [string]$Uri, [hashtable]$Headers, $Body) {
    $params = @{ Method = $Method; Uri = $Uri; Headers = $Headers; TimeoutSec = 60 }
    if ($null -ne $Body) {
        $params.ContentType = 'application/json; charset=utf-8'
        $params.Body = [Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Depth 10 -Compress))
    }
    try {
        return Invoke-RestMethod @params
    } catch {
        $detail = Get-ErrorDetail $_
        $shown = $Uri -replace '\?.*$', ''
        throw ('{0} {1} failed: http {2}, {3}' -f $Method, $shown, $detail.http, $detail.message)
    }
}

function Scw([string]$Method, [string]$Path, $Body) {
    return Invoke-Api $Method ($ScwApi + $Path) $script:ScwHeaders $Body
}

function Wait-Status([string]$Kind, [string]$Path, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ($true) {
        $obj = Scw 'Get' $Path $null
        if ($obj.status -eq 'ready') { return $obj }
        if ($obj.status -eq 'error') {
            $msg = ''
            if ($null -ne $obj.PSObject.Properties['error_message']) { $msg = $obj.error_message }
            throw "$Kind is in error: $msg"
        }
        if ((Get-Date) -gt $deadline) { throw "$Kind still '$($obj.status)' after $TimeoutSeconds s" }
        Note "$Kind status: $($obj.status), waiting"
        Start-Sleep -Seconds 10
    }
}

function Test-ImagePublic([string]$Ref) {
    if ($Ref -notmatch '^ghcr\.io/([^:@]+)') { Note 'image is not on ghcr.io, public check skipped'; return $true }
    $repo = $Matches[1]
    try {
        $token = Invoke-RestMethod -Uri ('https://ghcr.io/token?scope=repository:' + $repo + ':pull') -TimeoutSec 30
        return ($null -ne $token.PSObject.Properties['token'] -and [bool]$token.token)
    } catch { return $false }
}

function Resolve-Cname([string]$Name, [string]$Server) {
    try {
        $answers = Resolve-DnsName -Name $Name -Type CNAME -Server $Server -DnsOnly -ErrorAction Stop
        $cname = @($answers | Where-Object { $_.Type -eq 'CNAME' }) | Select-Object -First 1
        if ($null -ne $cname) { return $cname.NameHost.TrimEnd('.') }
    } catch { }
    return $null
}

function Set-CloudflareCname([string]$Name, [string]$Target, $CfRecord) {
    $headers = @{ Authorization = 'Bearer ' + $CfRecord.credential.GetNetworkCredential().Password }
    try {
        $labels = $Name.Split('.')
        $zoneName = ($labels[($labels.Length - 2)..($labels.Length - 1)]) -join '.'
        $zones = Invoke-Api 'Get' ($CfApi + '/zones?name=' + $zoneName) $headers $null
        $zone = @($zones.result) | Select-Object -First 1
        if ($null -eq $zone) { throw "Cloudflare zone $zoneName is not visible with the stored token" }
        $existing = Invoke-Api 'Get' ($CfApi + "/zones/$($zone.id)/dns_records?name=$Name") $headers $null
        $records = @($existing.result)
        $conflicts = @($records | Where-Object { $_.type -ne 'CNAME' })
        if ($conflicts.Count -gt 0) {
            $types = ($conflicts | ForEach-Object { $_.type }) -join ','
            throw "records of type $types already exist on $Name; remove them first"
        }
        $cname = @($records | Where-Object { $_.type -eq 'CNAME' }) | Select-Object -First 1
        $body = [ordered]@{
            type = 'CNAME'; name = $Name; content = $Target; proxied = $false; ttl = 1
            comment = 'foncier-mcp on Scaleway Serverless Containers; DNS only so the HTTP-01 challenge reaches Scaleway'
        }
        if ($null -eq $cname) {
            $created = Invoke-Api 'Post' ($CfApi + "/zones/$($zone.id)/dns_records") $headers $body
            Note "CNAME created in Cloudflare: $Name points at $Target (record $($created.result.id))"
        } elseif ($cname.content.TrimEnd('.') -ne $Target -or $cname.proxied) {
            $updated = Invoke-Api 'Patch' ($CfApi + "/zones/$($zone.id)/dns_records/$($cname.id)") $headers $body
            Note "CNAME updated in Cloudflare: $Name points at $Target, proxy off (record $($updated.result.id))"
        } else {
            Note 'CNAME already right in Cloudflare'
        }
    } finally { $headers.Clear() }
}

function Test-Health([string]$Base, [string]$Label) {
    try {
        $health = Invoke-RestMethod -Uri ($Base.TrimEnd('/') + '/health') -TimeoutSec 120
        Note "$Label /health: $($health.status), version $($health.version), paid tier $($health.paid_tier_enabled)"
        return $health.status
    } catch {
        Note "$Label /health not answering: $($_.Exception.Message)"
        return 'unreachable'
    }
}

try {
    Step 'Credentials'
    $scw = Load-Store 'scaleway'
    if ($null -eq $scw) { throw 'No Scaleway key stored. Run .\install-credentials.ps1 -Provider scaleway first.' }
    $cf = Load-Store 'cloudflare'
    $ProjectId = $scw.project_id
    $script:ScwHeaders = @{ 'X-Auth-Token' = $scw.credential.GetNetworkCredential().Password }
    Note "Scaleway project $ProjectId, region $Region"
    Note ('Cloudflare token: ' + $(if ($null -ne $cf) { 'stored' } else { 'not stored, the CNAME is created by hand' }))
    $Summary.project_id = $ProjectId

    $keysJson = $null
    if ($KeysJsonPath) {
        $keysJson = (Get-Content -LiteralPath $KeysJsonPath -Raw -Encoding UTF8).Trim()
        $parsedKeys = ConvertFrom-Json $keysJson
        if ($null -eq $parsedKeys.PSObject.Properties['keys']) { throw "$KeysJsonPath is not the output of 'foncier-mcp keys export'" }
        $keyCount = @($parsedKeys.keys.PSObject.Properties).Count
        Note "keys export: $keyCount records (hashes only)"
        $Summary.key_records = $keyCount
    } else {
        Note 'no -KeysJsonPath: keys unchanged on an existing container, none on a new one'
    }

    Step 'Image'
    if (Test-ImagePublic $Image) {
        Note "$Image is publicly pullable"
    } else {
        throw "$Image is not publicly pullable. Set the GitHub package to public (package settings, change visibility), check that publish-image.yml ran, then run again."
    }

    Step 'Namespace'
    $namespaces = Scw 'Get' "/namespaces?project_id=$ProjectId&name=$NamespaceName" $null
    $namespace = @($namespaces.namespaces | Where-Object { $_.name -eq $NamespaceName }) | Select-Object -First 1
    if ($null -eq $namespace) {
        if ($DryRun) {
            Note "would create namespace $NamespaceName"
        } else {
            $namespace = Scw 'Post' '/namespaces' ([ordered]@{
                project_id = $ProjectId; name = $NamespaceName
                description = 'foncier-mcp, MCP server for French public land data'
            })
            Note "created namespace $($namespace.id)"
            $namespace = Wait-Status 'namespace' "/namespaces/$($namespace.id)" 300
        }
    } else {
        Note "namespace exists: $($namespace.id), status $($namespace.status)"
    }
    if ($null -ne $namespace) { $Summary.namespace_id = $namespace.id }

    Step 'Container'
    $envVars = [ordered]@{
        FONCIER_MCP_REQUIRE_API_KEY = '1'
        PAID_TIER_ENABLED = 'false'
        FONCIER_MCP_FREE_DAILY_QUOTA = "$FreeDailyQuota"
    }
    $spec = [ordered]@{
        image = $Image; port = 8080
        memory_limit_bytes = [int64]$MemoryMB * 1024 * 1024
        mvcpu_limit = $MvCpu
        min_scale = 0; max_scale = $MaxScale
        privacy = 'public'; protocol = 'http1'
        environment_variables = $envVars
    }
    if ($null -ne $keysJson) { $spec.secret_environment_variables = @{ FONCIER_MCP_KEYS_JSON = $keysJson } }
    $container = $null
    if ($null -ne $namespace) {
        $containers = Scw 'Get' "/containers?namespace_id=$($namespace.id)&name=$ContainerName" $null
        $container = @($containers.containers | Where-Object { $_.name -eq $ContainerName }) | Select-Object -First 1
    }
    if ($null -eq $container) {
        if ($DryRun) {
            Note "would create container ${ContainerName}: $Image, port 8080, $MemoryMB MB, $MvCpu mvCPU, scale 0 to $MaxScale, public"
        } else {
            $body = [ordered]@{ namespace_id = $namespace.id; name = $ContainerName; description = 'foncier-mcp streamable HTTP server' }
            foreach ($key in $spec.Keys) { $body[$key] = $spec[$key] }
            $container = Scw 'Post' '/containers' $body
            Note "created container $($container.id)"
            $container = Wait-Status 'container' "/containers/$($container.id)" 600
        }
    } else {
        Note "container exists: $($container.id), status $($container.status), image $($container.image)"
        if ($DryRun) {
            Note 'would update image, limits and variables, then redeploy'
        } else {
            $container = Scw 'Patch' "/containers/$($container.id)" $spec
            $container = Wait-Status 'container' "/containers/$($container.id)" 600
            [void](Scw 'Post' "/containers/$($container.id)/redeploy" @{})
            Note 'redeploy requested'
            Start-Sleep -Seconds 5
            $container = Wait-Status 'container' "/containers/$($container.id)" 600
        }
    }
    $endpointHost = $null
    if ($null -ne $container) {
        $Summary.container_id = $container.id
        $Summary.container_status = $container.status
        if ($null -ne $container.PSObject.Properties['public_endpoint'] -and $container.public_endpoint) {
            $endpoint = $container.public_endpoint
            if ($endpoint -notmatch '^https?://') { $endpoint = 'https://' + $endpoint }
            $endpointHost = ([uri]$endpoint).Host
            $Summary.public_endpoint = $endpoint
            Note "endpoint $endpoint"
            if ($container.status -eq 'ready') { $Summary.endpoint_health = Test-Health $endpoint 'endpoint' }
        }
    }

    Step "Custom domain $Hostname"
    if ($SkipDomain) {
        Note 'skipped (-SkipDomain)'
    } elseif ($null -eq $endpointHost) {
        Note 'no endpoint yet, domain step skipped'
    } else {
        $resolved = Resolve-Cname $Hostname '1.1.1.1'
        if ($resolved -ne $endpointHost) {
            Note ('CNAME on 1.1.1.1: ' + $(if ($resolved) { $resolved } else { 'none' }) + ", expected $endpointHost")
            if ($DryRun) {
                Note 'would create or fix the CNAME'
            } elseif ($null -ne $cf) {
                Set-CloudflareCname $Hostname $endpointHost $cf
                $deadline = (Get-Date).AddMinutes(5)
                do {
                    Start-Sleep -Seconds 15
                    $r1 = Resolve-Cname $Hostname '1.1.1.1'
                    $r8 = Resolve-Cname $Hostname '8.8.8.8'
                    Note "resolution: 1.1.1.1 gives $r1, 8.8.8.8 gives $r8"
                } while ((($r1 -ne $endpointHost) -or ($r8 -ne $endpointHost)) -and ((Get-Date) -lt $deadline))
                if (($r1 -ne $endpointHost) -or ($r8 -ne $endpointHost)) {
                    throw 'the CNAME does not resolve on both public resolvers yet; run the script again in a few minutes'
                }
            } else {
                Write-Host ''
                Write-Host 'Create this DNS record with the proxy OFF (grey cloud), then run the script again:' -ForegroundColor Yellow
                Write-Host "  CNAME  $Hostname  points at  $endpointHost"
                $Summary.domain_status = 'waiting for the CNAME'
                $script:StopEarly = $true
            }
        } else {
            Note "CNAME resolves to $endpointHost"
        }
        if (-not $DryRun -and -not $script:StopEarly) {
            $domains = Scw 'Get' "/domains?container_id=$($container.id)" $null
            $domain = @($domains.domains | Where-Object { $_.hostname -eq $Hostname }) | Select-Object -First 1
            if ($null -eq $domain) {
                $domain = Scw 'Post' '/domains' ([ordered]@{ container_id = $container.id; hostname = $Hostname })
                Note "domain registered: $($domain.id)"
            } else {
                Note "domain exists: $($domain.id), status $($domain.status)"
            }
            if ($domain.status -eq 'error') {
                $msg = ''
                if ($null -ne $domain.PSObject.Properties['error_message']) { $msg = $domain.error_message }
                Note "domain in error ($msg): removing the failed registration and registering again"
                [void](Scw 'Delete' "/domains/$($domain.id)" $null)
                Start-Sleep -Seconds 10
                $domain = Scw 'Post' '/domains' ([ordered]@{ container_id = $container.id; hostname = $Hostname })
            }
            $domain = Wait-Status 'domain' "/domains/$($domain.id)" 600
            $Summary.domain_id = $domain.id
            $Summary.domain_status = $domain.status
            $Summary.custom_domain_health = Test-Health "https://$Hostname" $Hostname
        } elseif ($DryRun) {
            $domains = Scw 'Get' "/domains?container_id=$($container.id)" $null
            $domain = @($domains.domains | Where-Object { $_.hostname -eq $Hostname }) | Select-Object -First 1
            if ($null -ne $domain) { Note "domain exists: $($domain.id), status $($domain.status)"; $Summary.domain_status = $domain.status }
            else { Note 'would register the custom domain' }
        }
    }
} finally {
    $script:ScwHeaders.Clear()
}

Step 'Summary'
[pscustomobject]$Summary | ConvertTo-Json -Depth 3
if ($script:StopEarly) { exit 2 }
