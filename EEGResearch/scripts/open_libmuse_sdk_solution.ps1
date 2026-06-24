$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$sdkRoot = Join-Path $root "libmuse_windows_8.0.5"
$sln = Join-Path $sdkRoot "examples\LibMuseExamples.sln"

if (!(Test-Path $sln)) {
    throw "Solution not found: $sln`nEnsure libmuse_windows_* is present next to this repo (see docs/MUSE_SDK_STEP1_CONNECTIVITY.md)."
}

Write-Host "Opening: $sln"
Start-Process $sln
