<#
.SYNOPSIS
Stores the Scaleway or Cloudflare API key that deploy.ps1 uses, encrypted with Windows DPAPI.

.DESCRIPTION
Asks for the values in the terminal, encrypts the secret with the current Windows account
(Export-Clixml of a PSCredential), restricts the store directory to the current user and SYSTEM,
checks that the secret was not written in clear, then performs one read-only API call to confirm
that the key works. Prints statuses and identifiers only, never a key.

Run it once per provider on the machine that will run deploy.ps1. The Cloudflare key is optional:
without it, deploy.ps1 prints the CNAME to create by hand and stops until it resolves.

Scaleway: create an IAM application for this deployment, give it a policy with
ContainersFullAccess on the project that will host the container, generate an API key for it and
paste the access key, the secret key and the project ID here.

Cloudflare: create an API token with Zone / DNS / Edit on the zone of the hostname only.

.PARAMETER Provider
scaleway (default) or cloudflare.

.PARAMETER Replace
Replace a store that already exists.

.PARAMETER Status
Only report whether the stores exist and whether the keys still work. No prompt, no write.

.EXAMPLE
.\install-credentials.ps1 -Provider scaleway
.\install-credentials.ps1 -Provider cloudflare
.\install-credentials.ps1 -Status
#>
[CmdletBinding()]
param(
    [ValidateSet('scaleway', 'cloudflare')][string]$Provider = 'scaleway',
    [switch]$Replace,
    [switch]$Status
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$StoreDirectory = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'foncier-mcp\credentials'

function Get-StorePath([string]$Name) {
    return Join-Path $StoreDirectory ($Name + '.credentials.xml')
}

function Test-EncryptedRoundTrip {
    $probe = [guid]::NewGuid().ToString()
    $secure = ConvertTo-SecureString $probe -AsPlainText -Force
    $encrypted = ConvertFrom-SecureString $secure
    $restored = ConvertTo-SecureString $encrypted
    $credential = New-Object System.Management.Automation.PSCredential('probe', $restored)
    if ($encrypted.Contains($probe) -or $credential.GetNetworkCredential().Password -cne $probe) {
        throw 'Windows DPAPI encryption does not work on this account.'
    }
    $secure.Dispose()
    $restored.Dispose()
}

function Protect-StoreDirectory {
    [void][IO.Directory]::CreateDirectory($StoreDirectory)
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $systemSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
    foreach ($sid in @($currentSid, $systemSid)) {
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $StoreDirectory -AclObject $acl
}

function Get-PlainText([securestring]$Secure) {
    return (New-Object System.Management.Automation.PSCredential('x', $Secure)).GetNetworkCredential().Password
}

function Save-Credential([string]$Name, [string]$UserName, [securestring]$Secret, [hashtable]$Extra) {
    $path = Get-StorePath $Name
    if ((Test-Path -LiteralPath $path) -and -not $Replace) {
        throw "A $Name key is already installed at $path. Use -Replace to overwrite it, or -Status to test it."
    }
    $plain = Get-PlainText $Secret
    if ([string]::IsNullOrWhiteSpace($plain)) { throw 'The secret is empty.' }
    Test-EncryptedRoundTrip
    Protect-StoreDirectory
    $record = [ordered]@{
        version = 1
        provider = $Name
        saved_at = (Get-Date).ToString('o')
        credential = New-Object System.Management.Automation.PSCredential($UserName, $Secret)
    }
    foreach ($key in $Extra.Keys) { $record[$key] = $Extra[$key] }
    $tempFile = Join-Path $StoreDirectory ([guid]::NewGuid().ToString() + '.tmp')
    try {
        [pscustomobject]$record | Export-Clixml -LiteralPath $tempFile -Encoding UTF8
        $loaded = Import-Clixml -LiteralPath $tempFile
        if ($loaded.credential.GetNetworkCredential().Password -cne $plain) {
            throw 'The encrypted store does not read back correctly.'
        }
        if ([IO.File]::ReadAllText($tempFile).Contains($plain)) {
            throw 'The secret must not be stored in clear.'
        }
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
        [IO.File]::Move($tempFile, $path)
    } finally {
        if (Test-Path -LiteralPath $tempFile) { Remove-Item -LiteralPath $tempFile -Force }
        $plain = $null
    }
    return $path
}

function Invoke-Probe([string]$Uri, [hashtable]$Headers) {
    try {
        $response = Invoke-WebRequest -Uri $Uri -Headers $Headers -Method Get -TimeoutSec 25 -UseBasicParsing
        return [ordered]@{ http = [int]$response.StatusCode; body = $response.Content }
    } catch {
        $code = $null
        if ($null -ne $_.Exception.PSObject.Properties['Response'] -and $null -ne $_.Exception.Response) {
            $code = [int]$_.Exception.Response.StatusCode
        }
        return [ordered]@{ http = $code; body = $null; error = $_.Exception.GetType().Name }
    }
}

function Describe-Http([int]$Code) {
    switch ($Code) {
        200 { 'ok' }
        401 { 'authentication refused: wrong key' }
        403 { 'permission refused: check the IAM policy or the token scope' }
        404 { 'not found: check the project id or the zone' }
        default { "http $Code" }
    }
}

function Test-Scaleway($Record) {
    $headers = @{ 'X-Auth-Token' = $Record.credential.GetNetworkCredential().Password }
    try {
        $uri = 'https://api.scaleway.com/containers/v1/regions/fr-par/namespaces?page_size=1&project_id=' + $Record.project_id
        $probe = Invoke-Probe $uri $headers
    } finally { $headers.Clear() }
    return Describe-Http $probe.http
}

function Test-Cloudflare($Record) {
    $headers = @{ Authorization = 'Bearer ' + $Record.credential.GetNetworkCredential().Password }
    try {
        $probe = Invoke-Probe 'https://api.cloudflare.com/client/v4/user/tokens/verify' $headers
    } finally { $headers.Clear() }
    if ($probe.http -eq 200) {
        $status = (ConvertFrom-Json $probe.body).result.status
        return "ok, token $status"
    }
    return Describe-Http $probe.http
}

function Get-StoreStatus([string]$Name) {
    $path = Get-StorePath $Name
    $row = [ordered]@{ provider = $Name; installed = (Test-Path -LiteralPath $path); store = $path }
    if (-not $row.installed) { return [pscustomobject]$row }
    $record = Import-Clixml -LiteralPath $path
    $row.saved_at = $record.saved_at
    if ($Name -eq 'scaleway') {
        $row.project_id = $record.project_id
        $row.api_check = Test-Scaleway $record
    } else {
        $row.api_check = Test-Cloudflare $record
    }
    return [pscustomobject]$row
}

if ($Status) {
    @('scaleway', 'cloudflare') | ForEach-Object { Get-StoreStatus $_ } | Format-List
    exit 0
}

Write-Host "The secret is encrypted with your Windows account and never printed." -ForegroundColor Cyan
if ($Provider -eq 'scaleway') {
    $project = (Read-Host -Prompt 'Scaleway project ID (UUID of the project hosting the container)').Trim()
    $parsed = [guid]::Empty
    if (-not [guid]::TryParse($project, [ref]$parsed)) { throw 'The project ID must be a UUID.' }
    $access = (Read-Host -Prompt 'Scaleway access key (starts with SCW)').Trim()
    if (-not $access.StartsWith('SCW')) { throw 'The access key starts with SCW.' }
    $secret = Read-Host -Prompt 'Scaleway secret key' -AsSecureString
    $path = Save-Credential 'scaleway' $access $secret @{ project_id = $parsed.ToString() }
} else {
    $secret = Read-Host -Prompt 'Cloudflare API token (Zone DNS Edit on the zone only)' -AsSecureString
    $path = Save-Credential 'cloudflare' 'cloudflare' $secret @{}
}
Write-Host "Stored encrypted at $path" -ForegroundColor Green
Get-StoreStatus $Provider | Format-List
