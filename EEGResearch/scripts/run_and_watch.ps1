param(
    [string]$HostName = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [string]$LearnerToken = "learner-token-123",
    [string]$AdminToken = "admin-token-123",
    [ValidateRange(100, 600000)]
    [int]$IntervalMs = 5000
)

$ErrorActionPreference = "Stop"

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
