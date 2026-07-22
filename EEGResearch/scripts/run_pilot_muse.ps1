param(
    [string]$HostName = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$ApiPort = 8000,
    [ValidateRange(1, 65535)]
    [int]$BridgePort = 8765,
    [string]$LearnerToken = $env:API_TOKEN,
    [string]$AdminToken = $env:ADMIN_TOKEN,
    [ValidateSet("Release", "Debug")]
    [string]$BridgeConfiguration = "Release",
    [ValidateRange(1, 600)]
    [int]$HealthWaitSeconds = 60,
    [ValidateRange(1, 120)]
    [int]$ScanAttempts = 10,
    [ValidateRange(1, 60)]
    [int]$ScanDelaySeconds = 2,
    [ValidateRange(1, 1000)]
    [int]$SessionStartAttempts = 180,
    [ValidateRange(1, 60)]
    [int]$SessionStartTimeoutSeconds = 4,
    [ValidateRange(1, 600)]
    [int]$BridgeReadyWaitSeconds = 180,
    [switch]$ConnectBeforeGui,
    [int]$DeviceIndex = -1,
    [switch]$AutoSelectHeadband,
    [switch]$SkipGui,
    [switch]$NoCleanup,
    [switch]$UseExistingBridge,
    [switch]$UseExistingApi,
    [switch]$StopExistingApiOnPort
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($LearnerToken)) {
    throw "LearnerToken not set. Pass -LearnerToken or set `$env:API_TOKEN before running this script."
}
if ([string]::IsNullOrWhiteSpace($AdminToken)) {
    throw "AdminToken not set. Pass -AdminToken or set `$env:ADMIN_TOKEN before running this script."
}

$repoRoot = Split-Path $PSScriptRoot -Parent
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$runBridgeScript = Join-Path $PSScriptRoot "run_native_bridge.ps1"
$runGuiScript = Join-Path $PSScriptRoot "run_eeg_pilot_gui.ps1"
$baseUrl = "http://$HostName`:$ApiPort"

function Wait-ApiHealthy {
    param([string]$Url, [int]$TimeoutSec)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-RestMethod -Method Get -Uri $Url -TimeoutSec 3
            if ($r.status -eq "ok") {
                return
            }
        } catch {
            Start-Sleep -Milliseconds 400
        }
    }
    throw "Health check failed at $Url after ${TimeoutSec}s"
}

function Get-MuseStatus {
    param([string]$Url, [hashtable]$Headers)
    return Invoke-RestMethod -Method Get -Uri "$Url/api/v1/muse/status" -Headers $Headers -TimeoutSec 10
}

function Test-ApiHealthyQuick {
    param([string]$BaseUrl)
    try {
        $r = Invoke-RestMethod -Method Get -Uri "$BaseUrl/healthz" -TimeoutSec 2
        return ($r.status -eq "ok")
    } catch {
        return $false
    }
}

function Test-TcpPortOpen {
    param(
        [string]$TargetHost = "127.0.0.1",
        [int]$Port,
        [int]$TimeoutMs = 700
    )
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $ar = $client.BeginConnect($TargetHost, $Port, $null, $null)
        if (-not $ar.AsyncWaitHandle.WaitOne($TimeoutMs)) {
            return $false
        }
        $client.EndConnect($ar) | Out-Null
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-ProcessIdsByListeningPort {
    param([int]$Port)
    try {
        $rows = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        if (-not $rows) { return @() }
        return @($rows | Select-Object -ExpandProperty OwningProcess -Unique)
    } catch {
        return @()
    }
}

function Start-ApiProcess {
    param(
        [string]$PythonExe,
        [string]$WorkingDir,
        [string]$BindHost,
        [int]$Port
    )
    $outLog = Join-Path $env:TEMP "run_pilot_muse_api_$(Get-Random).out.log"
    $errLog = Join-Path $env:TEMP "run_pilot_muse_api_$(Get-Random).err.log"
    $proc = Start-Process -FilePath $PythonExe -ArgumentList @(
        "-m", "uvicorn", "src.app.main:app",
        "--host", $BindHost,
        "--port", "$Port"
    ) -WorkingDirectory $WorkingDir -PassThru -WindowStyle Hidden `
      -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    return @{
        Process = $proc
        OutLog = $outLog
        ErrLog = $errLog
    }
}

if (!(Test-Path $python)) {
    throw "Missing venv python: $python"
}
if (!(Test-Path $runBridgeScript)) {
    throw "Missing script: $runBridgeScript"
}
if (!(Test-Path $runGuiScript)) {
    throw "Missing script: $runGuiScript"
}

$bridgeProc = $null
$apiProc = $null
$apiOutLog = $null
$apiErrLog = $null
$cleanup = -not $NoCleanup
$learnerHeaders = @{ Authorization = "Bearer $LearnerToken" }
$adminHeaders = @{ Authorization = "Bearer $AdminToken" }

try {
    Write-Host "==> Starting Muse pilot stack (GettingData32-style flow)"

    if (-not $UseExistingBridge) {
        # Avoid collisions with previously running bridge/API pairs.
        if (Test-TcpPortOpen -TargetHost "127.0.0.1" -Port $BridgePort -TimeoutMs 500) {
            $originalBridgePort = $BridgePort
            $maxBridgePortBumps = 10
            $foundFreeBridgePort = $false
            for ($b = 1; $b -le $maxBridgePortBumps; $b++) {
                $candidate = $originalBridgePort + $b
                if (-not (Test-TcpPortOpen -TargetHost "127.0.0.1" -Port $candidate -TimeoutMs 500)) {
                    $BridgePort = $candidate
                    $foundFreeBridgePort = $true
                    break
                }
            }
            if (-not $foundFreeBridgePort) {
                throw "Bridge port $originalBridgePort is already in use and no free alternative was found."
            }
            Write-Host "==> Bridge port $originalBridgePort in use; switching to $BridgePort"
        }

        Write-Host "==> Starting native bridge (libMuse)"
        $bridgeCmd = @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $runBridgeScript,
            "-EnableLibMuse",
            "-Configuration", $BridgeConfiguration,
            "-Port", "$BridgePort"
        )
        $bridgeProc = Start-Process -FilePath "powershell.exe" -ArgumentList $bridgeCmd -PassThru -WindowStyle Minimized
        Start-Sleep -Seconds 3
    } else {
        Write-Host "==> Using existing native bridge on port $BridgePort"
    }

    if (-not $UseExistingApi) {
        if ($StopExistingApiOnPort) {
            $pids = Get-ProcessIdsByListeningPort -Port $ApiPort
            if ($pids.Count -gt 0) {
                Write-Host "==> Stopping existing listener(s) on port ${ApiPort}: $($pids -join ', ')"
                foreach ($procId in $pids) {
                    try { Stop-Process -Id $procId -Force -ErrorAction Stop } catch {}
                }
                Start-Sleep -Milliseconds 600
            }
        }

        Write-Host "==> Starting API (EEG_SOURCE=muse)"
        $env:EEG_SOURCE = "muse"
        $env:MUSE_BRIDGE_HOST = "127.0.0.1"
        $env:MUSE_BRIDGE_PORT = "$BridgePort"
        $env:API_TOKEN = $LearnerToken
        $env:ADMIN_TOKEN = $AdminToken
        $apiStart = Start-ApiProcess -PythonExe $python -WorkingDir $repoRoot -BindHost $HostName -Port $ApiPort
        $apiProc = $apiStart.Process
        $apiOutLog = $apiStart.OutLog
        $apiErrLog = $apiStart.ErrLog
        Start-Sleep -Seconds 2
        Write-Host "API logs: $apiOutLog | $apiErrLog"

        # If bind fails because the requested port is occupied, optionally evict that
        # process and retry the same port once before bumping.
        if ($apiProc.HasExited) {
            $errTail = if (Test-Path $apiErrLog) { Get-Content -Raw $apiErrLog } else { "" }
            if ($errTail -match "10048" -or $errTail -match "address already in use") {
                if ($StopExistingApiOnPort) {
                    $pids = Get-ProcessIdsByListeningPort -Port $ApiPort
                    if ($pids.Count -gt 0) {
                        Write-Host "==> Port $ApiPort occupied; stopping listener(s): $($pids -join ', ')"
                        foreach ($procId in $pids) {
                            try { Stop-Process -Id $procId -Force -ErrorAction Stop } catch {}
                        }
                        Start-Sleep -Milliseconds 600
                        Write-Host "==> Retrying API on requested port $ApiPort"
                        $apiStart = Start-ApiProcess -PythonExe $python -WorkingDir $repoRoot -BindHost $HostName -Port $ApiPort
                        $apiProc = $apiStart.Process
                        $apiOutLog = $apiStart.OutLog
                        $apiErrLog = $apiStart.ErrLog
                        Start-Sleep -Seconds 2
                        Write-Host "API logs: $apiOutLog | $apiErrLog"
                        if (-not $apiProc.HasExited) {
                            $errTail = ""
                        }
                    }
                }

                if ($apiProc.HasExited) {
                $maxPortBumps = 5
                $bound = $false
                for ($b = 1; $b -le $maxPortBumps; $b++) {
                    $ApiPort = $ApiPort + 1
                    $baseUrl = "http://$HostName`:$ApiPort"
                    Write-Host "==> Port in use; retrying API on $baseUrl"
                    $apiStart = Start-ApiProcess -PythonExe $python -WorkingDir $repoRoot -BindHost $HostName -Port $ApiPort
                    $apiProc = $apiStart.Process
                    $apiOutLog = $apiStart.OutLog
                    $apiErrLog = $apiStart.ErrLog
                    Start-Sleep -Seconds 2
                    Write-Host "API logs: $apiOutLog | $apiErrLog"
                    if (-not $apiProc.HasExited) {
                        $bound = $true
                        break
                    }
                }
                if (-not $bound) {
                    throw "API failed to bind to ports. Last error log: $apiErrLog"
                }
                }
            }
        }
    } else {
        Write-Host "==> Using existing API at $baseUrl"
    }

    Write-Host "==> Skipping blocking health check (temporary)"
    try {
        $hz = Invoke-RestMethod -Method Get -Uri "$baseUrl/healthz" -TimeoutSec 3
        Write-Host "==> API health: $($hz.status)"
    } catch {
        Write-Host "WARN: quick health probe failed at $baseUrl/healthz : $($_.Exception.Message)"
    }

    # Do NOT actively connect-probe the bridge socket here.
    # BridgeTcpServer accepts a single client at a time; a probe can occupy that slot
    # and delay the API's own adapter connection.
    Write-Host "==> Bridge configured on 127.0.0.1:$BridgePort (skipping active TCP probe)"

    Write-Host "==> Starting session (with retry)"
    $sessionStarted = $false
    $lastSessionErr = ""
    for ($i = 0; $i -lt $SessionStartAttempts; $i++) {
        if ($apiProc -and $apiProc.HasExited) {
            $errTail = ""
            if ($apiErrLog -and (Test-Path $apiErrLog)) {
                $errTail = Get-Content -Raw $apiErrLog
                if ($errTail.Length -gt 2500) {
                    $errTail = $errTail.Substring($errTail.Length - 2500)
                }
            }
            throw "API process exited before session start (exit $($apiProc.ExitCode)). Error log tail:`n$errTail"
        }
        try {
            Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/session/start" -Headers $adminHeaders -TimeoutSec $SessionStartTimeoutSeconds | Out-Null
            $sessionStarted = $true
            break
        } catch {
            $detail = $_.Exception.Message
            if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
                $detail = "$detail | body=$($_.ErrorDetails.Message)"
            }
            $lastSessionErr = $detail
            if (($i % 10) -eq 0) {
                Write-Host "  waiting for API session/start... attempt $($i + 1)/$SessionStartAttempts"
                Write-Host "    last error: $detail"
            }
            Start-Sleep -Milliseconds 1000
        }
    }
    if (-not $sessionStarted) {
        throw "Failed to start session at $baseUrl after $SessionStartAttempts attempts. Last error: $lastSessionErr"
    }

    if ($ConnectBeforeGui) {
        Write-Host "==> Refreshing device scan"
        Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/muse/refresh" -Headers $adminHeaders -TimeoutSec 20 | Out-Null

        $devices = @()
        for ($i = 0; $i -lt $ScanAttempts; $i++) {
            $status = Get-MuseStatus -Url $baseUrl -Headers $learnerHeaders
            $ing = $status.data.ingestion
            $devices = @($ing.muse_devices)
            if ($devices.Count -gt 0) {
                break
            }
            Write-Host "  no devices yet (attempt $($i + 1)/$ScanAttempts), retrying in ${ScanDelaySeconds}s..."
            Start-Sleep -Seconds $ScanDelaySeconds
        }

        if ($devices.Count -eq 0) {
            throw "No Muse devices found. Ensure headband is on, nearby, and not connected elsewhere."
        }

        Write-Host "==> Found headbands:"
        for ($i = 0; $i -lt $devices.Count; $i++) {
            Write-Host "  [$i] $($devices[$i])"
        }
        $selectedIndex = 0
        if ($DeviceIndex -ge 0) {
            $selectedIndex = $DeviceIndex
            Write-Host "==> Using device index from parameter: $selectedIndex"
        } elseif ($AutoSelectHeadband) {
            Write-Host "==> Auto-selecting first discovered headband [0]"
        } else {
            $selection = Read-Host "Select device index to connect (default 0)"
            if (-not [string]::IsNullOrWhiteSpace($selection)) {
                if (-not [int]::TryParse($selection, [ref]$selectedIndex)) {
                    throw "Invalid selection: $selection"
                }
            }
        }
        if ($selectedIndex -lt 0 -or $selectedIndex -ge $devices.Count) {
            throw "Selection out of range: $selectedIndex"
        }
        $selectedName = [string]$devices[$selectedIndex]

        Write-Host "==> Connecting to $selectedName"
        $body = (@{ name = $selectedName } | ConvertTo-Json -Compress)
        $connectResp = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/muse/connect" -Headers $adminHeaders `
            -ContentType "application/json; charset=utf-8" -Body $body -TimeoutSec 30
        if (-not $connectResp.data.ok) {
            throw "Connect failed: $($connectResp | ConvertTo-Json -Depth 6)"
        }

        Write-Host "==> Waiting for connected state"
        $connected = $false
        for ($i = 0; $i -lt 20; $i++) {
            $status = Get-MuseStatus -Url $baseUrl -Headers $learnerHeaders
            $conn = [string]$status.data.ingestion.connection_state_name
            if ($conn -eq "connected") {
                $connected = $true
                break
            }
            Start-Sleep -Milliseconds 500
        }
        if (-not $connected) {
            Write-Host "WARN: connect command sent but status not yet 'connected'."
        }

        Write-Host ""
        Write-Host "Connected flow complete."
    } else {
        Write-Host "==> Skipping pre-connect. Open GUI and connect/refresh headband there."
    }
    Write-Host "API: $baseUrl"
    Write-Host "State endpoint: $baseUrl/api/v1/state"

    if (-not $SkipGui) {
        Write-Host "==> Launching pilot GUI"
        $guiProc = Start-Process -FilePath "powershell.exe" -ArgumentList @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $runGuiScript
        ) -PassThru
        Write-Host "GUI PID: $($guiProc.Id)"
    } else {
        Write-Host "GUI launch skipped (-SkipGui)."
    }

    Write-Host ""
    Write-Host "Press Enter to stop session and cleanup (or Ctrl+C)."
    [void](Read-Host)
} finally {
    if ($cleanup) {
        try {
            Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/muse/disconnect" -Headers $adminHeaders -TimeoutSec 10 | Out-Null
        } catch {}
        try {
            Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/session/stop" -Headers $adminHeaders -TimeoutSec 10 | Out-Null
        } catch {}

        if ($apiProc -and -not $apiProc.HasExited) {
            Stop-Process -Id $apiProc.Id -Force -ErrorAction SilentlyContinue
        }
        if ($bridgeProc -and -not $bridgeProc.HasExited) {
            Stop-Process -Id $bridgeProc.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
