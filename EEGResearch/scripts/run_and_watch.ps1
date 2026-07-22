param(
    [string]$HostName = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [string]$LearnerToken = $env:API_TOKEN,
    [string]$AdminToken = $env:ADMIN_TOKEN,
    [ValidateRange(100, 600000)]
    [int]$IntervalMs = 5000
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($LearnerToken)) {
    throw "LearnerToken not set. Pass -LearnerToken or set `$env:API_TOKEN before running this script."
}
if ([string]::IsNullOrWhiteSpace($AdminToken)) {
    throw "AdminToken not set. Pass -AdminToken or set `$env:ADMIN_TOKEN before running this script."
}

$runScript = Join-Path $PSScriptRoot "run_simulator.ps1"
$watchScript = Join-Path $PSScriptRoot "watch_live_state.ps1"

if (!(Test-Path $runScript)) {
    throw "Missing script: $runScript"
}
if (!(Test-Path $watchScript)) {
    throw "Missing script: $watchScript"
}

Write-Host "Starting simulator..."
& $runScript -HostName $HostName -Port $Port -LearnerToken $LearnerToken -AdminToken $AdminToken

Write-Host ""
Write-Host "Starting live watcher..."
& $watchScript -HostName $HostName -Port $Port -LearnerToken $LearnerToken -IntervalMs $IntervalMs
