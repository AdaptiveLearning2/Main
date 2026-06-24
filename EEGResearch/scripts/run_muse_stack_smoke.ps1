<#
.SYNOPSIS
    Smoke-test the Muse stack: pytest, then REST calls against the FastAPI app (and optional native bridge).

.DESCRIPTION
    1. Optionally runs pytest on tests\test_app.py
    2. Optionally starts muse_native_bridge.exe and/or uvicorn (EEG_SOURCE=muse)
    3. Waits for GET /healthz
    4. GET  /api/v1/muse/status (learner)
    5. POST /api/v1/session/start (admin)
    6. POST /api/v1/muse/refresh (admin)
    7. GET  /api/v1/muse/status (learner)
    8. If -MuseName is set: POST /api/v1/muse/connect (admin)
    9. Polls GET /api/v1/state (learner) until data appears or attempts exhausted
    10. If -MuseName was used: POST /api/v1/muse/disconnect (admin) before stop (when auto-cleanup runs)
    11. POST /api/v1/session/stop (admin) and stop child processes when -StartBridge / -StartServer (unless -NoCleanup)

.EXAMPLE
    # API and bridge already running; only run checks (default tokens, skip pytest):
    .\scripts\run_muse_stack_smoke.ps1 -SkipPytest

.EXAMPLE
    # Full local run: start bridge + API, connect to a known headband name, then tear down:
    .\scripts\run_muse_stack_smoke.ps1 -StartBridge -StartServer -MuseName "Muse-1234"

.EXAMPLE
    # CI-style: pytest + hit a server you started elsewhere with EEG_SOURCE=muse:
    .\scripts\run_muse_stack_smoke.ps1 -BaseUrl "http://127.0.0.1:8000"
#>
param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$LearnerToken = "learner-token-123",
    [string]$AdminToken = "admin-token-123",
    [string]$MuseName = "",
    [ValidateSet("Release", "Debug")]
    [string]$BridgeConfiguration = "Release",
    [string]$BridgeExe = "",
    [int]$ApiPort = 8000,
    [int]$HealthWaitSeconds = 45,
    [int]$StatePollAttempts = 30,
    [int]$StatePollDelayMs = 500,
    [int]$UvicornStartupWaitSeconds = 5,
    [switch]$SkipPytest,
    [switch]$StartBridge,
    [switch]$StartServer,
    [switch]$NoCleanup
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path $PSScriptRoot -Parent
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"

function Resolve-BridgeExe {
    param([string]$Explicit, [string]$Root, [string]$Config)
    if (![string]::IsNullOrWhiteSpace($Explicit)) {
        if (!(Test-Path $Explicit)) {
            throw "Bridge exe not found: $Explicit"
        }
        return (Resolve-Path $Explicit).Path
    }
    $candidates = @(
        (Join-Path $Root "native_bridge\build\$Config\muse_native_bridge.exe"),
        (Join-Path $Root "native_bridge\build\Release\muse_native_bridge.exe"),
        (Join-Path $Root "native_bridge\build\Debug\muse_native_bridge.exe")
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) {
            return (Resolve-Path $p).Path
        }
    }
    throw "muse_native_bridge.exe not found. Build with: .\scripts\run_native_bridge.ps1 -EnableLibMuse (or pass -BridgeExe)"
}

function Get-TextTail {
    param([string]$Path, [int]$MaxChars = 3000)
    if (!(Test-Path $Path)) {
        return ""
    }
    $t = Get-Content -Raw -Path $Path -ErrorAction SilentlyContinue
    if ($null -eq $t) {
        return ""
    }
    if ($t.Length -le $MaxChars) {
        return $t
    }
    return $t.Substring($t.Length - $MaxChars)
}

function Wait-ApiHealthy {
    param(
        [string]$Url,
        [int]$TimeoutSec,
        [System.Diagnostics.Process]$ChildProcess = $null,
        [string]$StderrLogPath = $null,
        [int]$BindPort = 8000
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $nextProgress = (Get-Date)
    $lastEx = ""
    while ((Get-Date) -lt $deadline) {
        if ($null -ne $ChildProcess -and $ChildProcess.HasExited) {
            $errTail = if ($StderrLogPath) { Get-TextTail -Path $StderrLogPath } else { "" }
            throw (
                "The API process (uvicorn) exited before the site became ready (exit code: $($ChildProcess.ExitCode)).`n" +
                "Typical causes: port $BindPort already in use, missing Python deps, or import error in the app.`n" +
                "Stderr log tail:`n$errTail"
            )
        }
        try {
            $r = Invoke-RestMethod -Method Get -Uri $Url -TimeoutSec 3
            if ($r.status -eq "ok") {
                return
            }
            $lastEx = "Response did not have status=ok. Got: $($r | ConvertTo-Json -Compress)"
        } catch {
            $lastEx = $_.Exception.Message
        }
        if ((Get-Date) -ge $nextProgress) {
            $left = [int][Math]::Max(0, ($deadline - (Get-Date)).TotalSeconds)
            Write-Host "  ... still waiting for $Url (${left}s left); last: $lastEx"
            $nextProgress = (Get-Date).AddSeconds(5)
        }
        Start-Sleep -Milliseconds 400
    }
    $errTail2 = if ($StderrLogPath) { Get-TextTail -Path $StderrLogPath } else { "" }
    $noServerHint = if ($null -eq $ChildProcess) {
        "No API was auto-started. Run the server first (e.g. .\scripts\run_simulator.ps1) " +
        "or pass -StartServer so this script can launch uvicorn. " +
        "If the server uses a custom port, use -BaseUrl (e.g. -BaseUrl 'http://127.0.0.1:9000') and open that URL in a browser. " +
        "If a previous uvicorn is still on this port, stop it or set -ApiPort to a free port (with -StartServer)."
    } else {
        ""
    }
    throw @"
Health check failed within ${TimeoutSec}s: $Url
$noServerHint
Last error: $lastEx
Uvicorn stderr (tail):
$errTail2
"@
}

$startedBridge = $null
$startedServer = $null
$smokeUvicornErrLog = $null
$smokeUvicornOutLog = $null
$cleanup = (-not $NoCleanup) -and ($StartBridge -or $StartServer)

try {
    if (!(Test-Path $python)) {
        throw "Missing venv: $python"
    }
    $base = $BaseUrl.TrimEnd("/")

    if (-not $SkipPytest) {
        Write-Host "==> pytest tests\test_app.py"
        & $python -m pytest (Join-Path $repoRoot "tests\test_app.py") -q
        if ($LASTEXITCODE -ne 0) {
            throw "pytest failed ($LASTEXITCODE)"
        }
    }

    if ($StartBridge) {
        $exe = Resolve-BridgeExe -Explicit $BridgeExe -Root $repoRoot -Config $BridgeConfiguration
        Write-Host "==> Starting native bridge: $exe"
        $startedBridge = Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe -Parent) -PassThru -WindowStyle Minimized
        Start-Sleep -Seconds 2
    }

    if ($StartServer) {
        Write-Host "==> Starting uvicorn (EEG_SOURCE=muse) on port $ApiPort"
        $env:EEG_SOURCE = "muse"
        $env:API_TOKEN = $LearnerToken
        $env:ADMIN_TOKEN = $AdminToken
        $env:HOST = "127.0.0.1"
        $env:PORT = "$ApiPort"
        $smokeUvicornOutLog = Join-Path $env:TEMP "eeg_muse_smoke_uvicorn_$(Get-Random).out.log"
        $smokeUvicornErrLog = Join-Path $env:TEMP "eeg_muse_smoke_uvicorn_$(Get-Random).err.log"
        $null = New-Item -Path $smokeUvicornOutLog, $smokeUvicornErrLog -ItemType File -Force
        $startedServer = Start-Process -FilePath $python -ArgumentList @(
            "-m", "uvicorn", "src.app.main:app",
            "--host", "127.0.0.1", "--port", "$ApiPort"
        ) -WorkingDirectory $repoRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput $smokeUvicornOutLog -RedirectStandardError $smokeUvicornErrLog
        $BaseUrl = "http://127.0.0.1:$ApiPort"
        Write-Host "Requests will use BaseUrl=$BaseUrl (child PID $($startedServer.Id))"
        Write-Host "Uvicorn logs: $smokeUvicornOutLog | $smokeUvicornErrLog"
        Write-Host "==> Pausing $UvicornStartupWaitSeconds s for first import + bind..."
        Start-Sleep -Seconds $UvicornStartupWaitSeconds
    } else {
        Write-Host "==> -StartServer not used: expecting an API at $BaseUrl (if nothing is running, the health check will time out; use -StartServer or start .\scripts\run_simulator.ps1)"
    }

    $base = $BaseUrl.TrimEnd("/")
    $healthUrl = "$base/healthz"
    Write-Host "==> Waiting for $healthUrl"
    Wait-ApiHealthy -Url $healthUrl -TimeoutSec $HealthWaitSeconds -ChildProcess $startedServer -StderrLogPath $smokeUvicornErrLog -BindPort $ApiPort

    $hLearner = @{ Authorization = "Bearer $LearnerToken" }
    $hAdmin = @{ Authorization = "Bearer $AdminToken" }

    Write-Host "==> GET /api/v1/muse/status"
    $status1 = Invoke-RestMethod -Method Get -Uri "$base/api/v1/muse/status" -Headers $hLearner
    if ($status1.status -ne "ok") {
        throw "Unexpected muse/status: $($status1 | ConvertTo-Json -Compress)"
    }
    Write-Host ($status1 | ConvertTo-Json -Depth 6)

    Write-Host "==> POST /api/v1/session/start"
    Invoke-RestMethod -Method Post -Uri "$base/api/v1/session/start" -Headers $hAdmin -TimeoutSec 30 | Out-Null

    Write-Host "==> POST /api/v1/muse/refresh"
    $ref = Invoke-RestMethod -Method Post -Uri "$base/api/v1/muse/refresh" -Headers $hAdmin -TimeoutSec 30
    if (-not $ref.data.ok) {
        Write-Host "WARN: muse/refresh data.ok=false (expected if EEG_SOURCE is not muse or bridge not connected): $($ref | ConvertTo-Json -Compress)"
    } else {
        Write-Host ($ref | ConvertTo-Json -Depth 4)
    }

    Write-Host "==> GET /api/v1/muse/status (after refresh)"
    $status2 = Invoke-RestMethod -Method Get -Uri "$base/api/v1/muse/status" -Headers $hLearner
    Write-Host ($status2 | ConvertTo-Json -Depth 6)

    if (![string]::IsNullOrWhiteSpace($MuseName)) {
        Write-Host "==> POST /api/v1/muse/connect (name=$MuseName)"
        $body = (@{ name = $MuseName } | ConvertTo-Json -Compress)
        $con = Invoke-RestMethod -Method Post -Uri "$base/api/v1/muse/connect" -Headers $hAdmin `
            -ContentType "application/json; charset=utf-8" -Body $body -TimeoutSec 60
        if (-not $con.data.ok) {
            throw "muse/connect failed: $($con | ConvertTo-Json -Depth 5)"
        }
        Write-Host ($con | ConvertTo-Json -Depth 4)
    } else {
        Write-Host "==> Skipping muse/connect (pass -MuseName 'Muse-XXXX' to connect)"
    }

    Write-Host "==> Polling GET /api/v1/state (up to $StatePollAttempts times)"
    $gotData = $false
    for ($i = 0; $i -lt $StatePollAttempts; $i++) {
        $st = Invoke-RestMethod -Method Get -Uri "$base/api/v1/state" -Headers $hLearner -TimeoutSec 5
        if ($st.status -eq "ok" -and $st.data) {
            Write-Host "state ok (attempt $($i + 1)):"
            Write-Host ($st.data | ConvertTo-Json -Depth 5)
            $gotData = $true
            break
        }
        Write-Host "  idle/waiting... ($($st.status)) $($st.message)"
        Start-Sleep -Milliseconds $StatePollDelayMs
    }
    if (-not $gotData) {
        Write-Host "WARN: No /api/v1/state payload yet (need EEG_SOURCE=muse, bridge running, and a successful connect)."
    }

    Write-Host "==> Smoke finished successfully."
} finally {
    if ($cleanup) {
        if ($startedServer -and -not $startedServer.HasExited) {
            if (![string]::IsNullOrWhiteSpace($MuseName)) {
                Write-Host "==> POST /api/v1/muse/disconnect"
                try {
                    Invoke-RestMethod -Method Post -Uri "$($BaseUrl.TrimEnd('/'))/api/v1/muse/disconnect" `
                        -Headers @{ Authorization = "Bearer $AdminToken" } -TimeoutSec 15 | Out-Null
                } catch {
                    Write-Host "WARN: muse/disconnect failed: $_"
                }
            }
            Write-Host "==> POST /api/v1/session/stop"
            try {
                Invoke-RestMethod -Method Post -Uri "$($BaseUrl.TrimEnd('/'))/api/v1/session/stop" `
                    -Headers @{ Authorization = "Bearer $AdminToken" } -TimeoutSec 15 | Out-Null
            } catch {
                Write-Host "WARN: session/stop failed: $_"
            }
            Start-Sleep -Milliseconds 500
            Write-Host "==> Stopping uvicorn PID $($startedServer.Id)"
            Stop-Process -Id $startedServer.Id -Force -ErrorAction SilentlyContinue
        }
        if ($startedBridge -and -not $startedBridge.HasExited) {
            Write-Host "==> Stopping native bridge PID $($startedBridge.Id)"
            Stop-Process -Id $startedBridge.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
