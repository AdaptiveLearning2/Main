<#
.SYNOPSIS
Publishes a built kit's Update installer to the update gate's bucket, then a signed feed naming it: canary.json by
default, latest.json with -Promote. The way back (rollback_to) is always what latest.json named before.

.DESCRIPTION
Run in a terminal after build_student_kit.ps1, since it asks for things: the download key and the Cloudflare API
token (R2 edit), each read from the clipboard when you press Enter and never shown, and the signing key's
passphrase. It accepts only the installer whose SHA-256 the build recorded, never overwrites a published installer,
uploads the installer before the feed, and reads both back through the gate before reporting success. See
DEVELOPER_SETUP_WINDOWS.md, "Publishing a kit update".

.EXAMPLE
.\EEGResearch\scripts\publish_kit_update.ps1 -Version 0.2.1 -SigningKey E:\kit-keys\everyday.pem

.EXAMPLE
.\EEGResearch\scripts\publish_kit_update.ps1 -Version 0.2.1 -SigningKey E:\kit-keys\everyday.pem -Promote -Rollout 10
#>
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$SigningKey,
    [switch]$Promote,
    [ValidateRange(0, 100)][int]$Rollout = 100
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # 5.1 draws Invoke-WebRequest's progress so slowly it dominates a download
$eeg = Split-Path $PSScriptRoot -Parent
$out = Join-Path $eeg "dist\kit"
$work = Join-Path $eeg "build\kit\publish"
$bucket = "adaptivelearning-kit"
$wrangler = "wrangler@4.148.0"
$feed = if ($Promote) { "latest" } else { "canary" }

if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "-Version must look like 0.2.1" }
$name = "AdaptiveLearningSensors-Update-$Version.exe"
$installerPath = Join-Path $out $name
if (-not (Test-Path $installerPath) -or -not (Test-Path "$installerPath.sha256")) {
    throw "$installerPath and its .sha256 must both exist: build the kit with build_student_kit.ps1 first"
}
$recorded = ((Get-Content -Raw "$installerPath.sha256").Trim() -split '\s+')[0]
$hash = (Get-FileHash -Algorithm SHA256 $installerPath).Hash.ToLower()
if ($hash -ne $recorded) { throw "$name does not match the SHA-256 the build recorded" }

# The build's venv has the pinned cryptography; the sidecar's dev venv is the fallback.
$py = @((Join-Path $eeg "build\kit\venv\Scripts\python.exe"), (Join-Path $eeg ".venv\Scripts\python.exe")) |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) { throw "no Python with the kit's packages: run build_student_kit.ps1 first" }
$release = Join-Path $eeg "installer\kit_release.py"
Push-Location $eeg
try { $gate = (& $py -c "from src.kit.update_settings import GATE_URL; print(GATE_URL)").Trim() } finally { Pop-Location }
if (Test-Path $work) { Remove-Item -Recurse -Force $work }
New-Item -ItemType Directory -Force $work | Out-Null

function Get-FromGate {
    # The status code, saving the body to $to on 200; 404 is an answer, anything else stops the script.
    param([string]$path, [string]$to)
    try {
        $null = Invoke-WebRequest -UseBasicParsing -Uri "$gate$path" -OutFile $to `
            -Headers @{ Authorization = "Bearer $script:downloadKey" }
        return 200
    } catch {
        if ($null -eq $_.Exception.Response) { throw "the gate did not answer for ${path}: $($_.Exception.Message)" }
        $status = [int]$_.Exception.Response.StatusCode  # 5.1's WebException and 7's HttpResponseException alike
        if ($status -eq 404) { return 404 }
        throw "the gate answered $status for $path"
    }
}

function Read-Feed {
    # kit_release.py verify: the feed as a kit would read it, against the keys the kits trust.
    param([string]$path)
    $text = & $py $release verify $path
    if ($LASTEXITCODE -ne 0) { throw "$path does not verify against the kits' keys" }
    return $text | ConvertFrom-Json
}

function Invoke-Wrangler {
    param([string[]]$arguments)
    $ErrorActionPreference = "Continue"  # npx writes notices to stderr, fatal under Stop once captured on 5.1
    $output = & npx --yes $wrangler @arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "wrangler $($arguments[0..2] -join ' ') failed: $($output -join ' ')" }
}

$null = Read-Host "Copy the download key (the gate's DOWNLOAD_KEY), then press Enter"
$script:downloadKey = (Get-Clipboard -Raw).Trim()
try {
    # What latest.json names now is the way back, for a canary as much as for a promotion.
    $current = Join-Path $work "latest-before.json"
    $rollbackArgs = @()
    if ((Get-FromGate "/v1/feed/latest.json" $current) -eq 200) {
        $before = Read-Feed $current
        # Promoting the version latest.json already names changes its rollout and keeps its way back.
        $same = [version]$before.version -eq [version]$Version
        if ([version]$before.version -gt [version]$Version -or ($same -and -not $Promote)) {
            throw "latest.json already names $($before.version); publish a newer version"
        }
        $rollbackArgs = @("--rollback-to-feed", $current)
        $wayBack = if ($same) { $before.rollback_to.version } else { $before.version }
        Write-Host "The way back is $(if ($wayBack) { $wayBack } else { 'none' })."
    } else {
        Write-Host "No latest.json yet, so this feed names no way back and no kit will install it." -ForegroundColor Yellow
    }

    $already = (Get-FromGate "/v1/files/$name" (Join-Path $work "published-$name")) -eq 200
    if ($already) {
        $published = (Get-FileHash -Algorithm SHA256 (Join-Path $work "published-$name")).Hash.ToLower()
        if ($published -ne $hash) { throw "$name is already published with other bytes; a published version is never replaced" }
        Write-Host "$name is already published with these bytes; only the feed changes."
    }

    $signed = Join-Path $work "$feed.json"
    Write-Host "== Signing $feed.json for $Version at $Rollout%" -ForegroundColor Cyan
    & $py $release sign --key $SigningKey --installer $installerPath --rollout $Rollout @rollbackArgs --out $signed
    if ($LASTEXITCODE -ne 0) { throw "signing failed" }

    $null = Read-Host "Copy the Cloudflare API token (R2 edit), then press Enter"
    $env:CLOUDFLARE_API_TOKEN = (Get-Clipboard -Raw).Trim()
    try {
        if (-not $already) {
            Write-Host "== Uploading $name" -ForegroundColor Cyan
            Invoke-Wrangler @("r2", "object", "put", "$bucket/files/$name", "--file", $installerPath, "--remote")
        }
        Write-Host "== Uploading $feed.json" -ForegroundColor Cyan  # after the installer, so no feed names a missing file
        Invoke-Wrangler @("r2", "object", "put", "$bucket/feed/$feed.json", "--file", $signed, "--remote")
    } finally { Remove-Item Env:CLOUDFLARE_API_TOKEN -ErrorAction SilentlyContinue }

    Write-Host "== Reading both back through the gate" -ForegroundColor Cyan
    $back = Join-Path $work "back-$name"
    if ((Get-FromGate "/v1/files/$name" $back) -ne 200) { throw "the gate does not serve $name" }
    if ((Get-FileHash -Algorithm SHA256 $back).Hash.ToLower() -ne $hash) { throw "the gate serves other bytes for $name" }
    $feedBack = Join-Path $work "back-$feed.json"
    if ((Get-FromGate "/v1/feed/$feed.json" $feedBack) -ne 200) { throw "the gate does not serve $feed.json" }
    if ((Get-FileHash $feedBack).Hash -ne (Get-FileHash $signed).Hash) { throw "the gate serves another $feed.json" }
    $now = Read-Feed $feedBack
    if ($now.version -ne $Version) { throw "the published $feed.json names $($now.version), not $Version" }
} finally { $script:downloadKey = $null }

Write-Host "Published $Version to $feed.json at $Rollout%, signed by $($now.signed_by); way back: $(if ($now.rollback_to) { $now.rollback_to.version } else { 'none' })" -ForegroundColor Green
