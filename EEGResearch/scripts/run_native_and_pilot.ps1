param(
    [string]$HostName = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$ApiPort = 8001,
    [ValidateRange(1, 65535)]
    [int]$BridgePort = 8765,
    [string]$LearnerToken = "learner-token-123",
    [string]$AdminToken = "admin-token-123",
    [ValidateSet("Release", "Debug")]
    [string]$BridgeConfiguration = "Release",
    [switch]$ConnectBeforeGui,
    [int]$DeviceIndex = -1,
    [switch]$PromptForDevice,
    [switch]$SkipGui,
    [switch]$NoCleanup,
    [switch]$StopExistingApiOnPort
)

$ErrorActionPreference = "Stop"

$runBridgeScript = Join-Path $PSScriptRoot "run_native_bridge.ps1"
$runPilotScript = Join-Path $PSScriptRoot "run_pilot_muse.ps1"
if (!(Test-Path $runBridgeScript)) {
    throw "Missing script: $runBridgeScript"
}
if (!(Test-Path $runPilotScript)) {
    throw "Missing script: $runPilotScript"
}

$bridgeProc = $null
try {
    Write-Host "==> Starting native bridge process"
    $bridgeArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $runBridgeScript,
        "-EnableLibMuse",
        "-Configuration", $BridgeConfiguration,
        "-Port", "$BridgePort"
    )
    $bridgeProc = Start-Process -FilePath "powershell.exe" -ArgumentList $bridgeArgs -PassThru -WindowStyle Minimized
    Start-Sleep -Seconds 3

    Write-Host "==> Running pilot flow with existing bridge"
    $pilotArgs = @{
        HostName            = $HostName
        ApiPort             = $ApiPort
        BridgePort          = $BridgePort
        LearnerToken        = $LearnerToken
        AdminToken          = $AdminToken
        BridgeConfiguration = $BridgeConfiguration
        UseExistingBridge   = $true
    }
    if ($ConnectBeforeGui) {
        $pilotArgs.ConnectBeforeGui = $true
        if ($DeviceIndex -ge 0) {
            $pilotArgs.DeviceIndex = $DeviceIndex
        } elseif (-not $PromptForDevice) {
            $pilotArgs.AutoSelectHeadband = $true
        }
    }
    if ($SkipGui) {
        $pilotArgs.SkipGui = $true
    }
    if ($NoCleanup) {
        $pilotArgs.NoCleanup = $true
    }
    if ($StopExistingApiOnPort) {
        $pilotArgs.StopExistingApiOnPort = $true
    }

    & $runPilotScript @pilotArgs
} finally {
    if ($bridgeProc -and -not $bridgeProc.HasExited) {
        Write-Host "==> Stopping native bridge process ($($bridgeProc.Id))"
        Stop-Process -Id $bridgeProc.Id -Force -ErrorAction SilentlyContinue
    }
}
