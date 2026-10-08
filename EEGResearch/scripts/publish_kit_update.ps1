<#
.SYNOPSIS
Publishes a built kit's Update installer to the update gate's bucket, then a signed feed naming it: canary.json by
default, latest.json with -Promote. The feed lists every installer the published feeds name, as ways back.

.DESCRIPTION
Run in a terminal after build_student_kit.ps1, since it asks for two things: the signing key's passphrase, and the
Cloudflare API token (R2 edit), read from the clipboard when you press Enter and then cleared from it and from
clipboard history. The download key is read from -DownloadKeyFile. It accepts only the installer whose SHA-256 the
build recorded, never replaces a published installer, uploads the installer before the feed, and reads both back
through the gate before reporting success. See DEVELOPER_SETUP_WINDOWS.md, "Publishing a kit update".

.EXAMPLE
.\EEGResearch\scripts\publish_kit_update.ps1 -Version 0.2.1 -SigningKey E:\kit-keys\everyday.pem -DownloadKeyFile E:\kit-keys\download.key

.EXAMPLE
.\EEGResearch\scripts\publish_kit_update.ps1 -Version 0.2.1 -SigningKey E:\kit-keys\everyday.pem -DownloadKeyFile E:\kit-keys\download.key -Promote -Rollout 10
#>
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$SigningKey,
    [Parameter(Mandatory = $true)][string]$DownloadKeyFile,
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
if (-not (Test-Path $DownloadKeyFile)) { throw "-DownloadKeyFile $DownloadKeyFile does not exist" }

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
    # kit_release.py verify: the feed as a kit would read it, against the keys the kits trust, and named as served.
    param([string]$path, [string]$expected)
    $text = & $py $release verify $path
    if ($LASTEXITCODE -ne 0) { throw "$path does not verify against the kits' keys" }
    $manifest = $text | ConvertFrom-Json
    if ($manifest.feed -ne $expected) { throw "$path is served as $expected.json but signed as the $($manifest.feed) feed" }
    return $manifest
}

function Invoke-Wrangler {
    param([string[]]$arguments)
    $ErrorActionPreference = "Continue"  # npx writes notices to stderr, fatal under Stop once captured on 5.1
    $output = & npx --yes $wrangler @arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "wrangler $($arguments[0..2] -join ' ') failed: $($output -join ' ')" }
}

function Clear-ClipboardSecret {
    # Clipboard history (Win+V) keeps every copy, so the secret's entries go from there as well as the clipboard.
    param([string]$secret)
    try {
        Add-Type -AssemblyName System.Runtime.WindowsRuntime
        $clip = [Windows.ApplicationModel.DataTransfer.Clipboard, Windows.ApplicationModel.DataTransfer, ContentType = WindowsRuntime]
        $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
            $_.Name -eq "AsTask" -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
        } | Select-Object -First 1
        $wait = { param($operation, [type]$result) $task = $asTask.MakeGenericMethod($result).Invoke($null, @($operation)); $null = $task.Wait(-1); $task.Result }
        $clip::Clear()
        $items = & $wait ($clip::GetHistoryItemsAsync()) ([Windows.ApplicationModel.DataTransfer.ClipboardHistoryItemsResult])
        $removed = 0
        foreach ($item in @($items.Items)) {
            try { $text = & $wait ($item.Content.GetTextAsync()) ([string]) } catch { continue }  # not text
            if ($text.Trim() -eq $secret -and $clip::DeleteItemFromHistory($item)) { $removed++ }
        }
        Write-Host "Cleared the clipboard and $removed copy(ies) of the token from clipboard history."
    } catch {
        Write-Warning "Could not clear the token from the clipboard ($($_.Exception.Message)); clear it with Win+V."
    }
}

$script:downloadKey = (Get-Content -Raw $DownloadKeyFile).Trim()
try {
    # Every installer either feed names becomes a way back, so a computer on any published version can return to it.
    $historyArgs = @()
    foreach ($published in "latest", "canary") {
        $path = Join-Path $work "$published-before.json"
        if ((Get-FromGate "/v1/feed/$published.json" $path) -ne 200) { continue }
        $before = Read-Feed $path $published
        $historyArgs += @("--history-from", $path)
        if ($published -eq "latest") {
            # Promoting the version latest.json already names changes its rollout; anything else must be newer.
            $same = [version]$before.version -eq [version]$Version
            if ([version]$before.version -gt [version]$Version -or ($same -and -not $Promote)) {
                throw "latest.json already names $($before.version); publish a newer version"
            }
        }
    }
    if (-not $historyArgs) {
        Write-Host "Nothing is published yet, so this feed lists no way back and no kit installs it." -ForegroundColor Yellow
    }

    $already = (Get-FromGate "/v1/files/$name" (Join-Path $work "published-$name")) -eq 200
    if ($already) {
        $publishedHash = (Get-FileHash -Algorithm SHA256 (Join-Path $work "published-$name")).Hash.ToLower()
        if ($publishedHash -ne $hash) { throw "$name is already published with other bytes; a published version is never replaced" }
        Write-Host "$name is already published with these bytes; only the feed changes."
    }

    $signed = Join-Path $work "$feed.json"
    Write-Host "== Signing $feed.json for $Version at $Rollout%" -ForegroundColor Cyan
    & $py $release sign --key $SigningKey --installer $installerPath --feed $feed --rollout $Rollout @historyArgs --out $signed
    if ($LASTEXITCODE -ne 0) { throw "signing failed" }

    $null = Read-Host "Copy the Cloudflare API token (R2 edit), then press Enter"
    $token = (Get-Clipboard -Raw).Trim()
    Clear-ClipboardSecret $token
    $env:CLOUDFLARE_API_TOKEN = $token
    $token = $null
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
    $now = Read-Feed $feedBack $feed
    if ($now.version -ne $Version) { throw "the published $feed.json names $($now.version), not $Version" }
} finally { $script:downloadKey = $null }

Write-Host "Published $Version to $feed.json at $Rollout%, signed by $($now.signed_by); ways back: $(if ($now.history) { $now.history -join ', ' } else { 'none' })" -ForegroundColor Green
