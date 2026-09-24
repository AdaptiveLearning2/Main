param(
    [switch]$Muse,
    [switch]$Camera,
    [int]$CameraIndex = 0,
    # Landmark gaze channel; opt-in, needs its own model. Implies -Camera.
    [switch]$Gaze,
    # FER+ off; gaze-only skips the 35 MB model.
    [switch]$NoEmotion,
    # Headband optical channels (PPG, heart rate). Moves a 2025 Athena off PRESET_21 (EEG
    # bit depth 12 -> 14). Passed to the bridge's environment: it never reads a .env.
    [switch]$Optics,
    # 1031/1032 16 CH, 1033/1034 8 CH, 1035/1036 4 CH, odd = low power. Empty = bridge default 1035.
    [string]$OpticsPreset = "",
    # EEG_SPECTRUM_SOURCE=local instead of the SDK band ratio; written every run, so only this flag selects it.
    [switch]$LocalCalm
)

$ErrorActionPreference = "Stop"

# Gaze is provisioned inside the -Camera block, so promote rather than silently do nothing.
if ($Gaze -and -not $Camera) {
    Write-Host "-Gaze implies -Camera; enabling the camera too." -ForegroundColor Yellow
    $Camera = $true
}

# The sidecar refuses a camera with every channel off; refuse the flags here instead.
# Reads the hand-set FACE_HEART_ENABLED (never written here), the adapter's third channel.
$heartOn = $false
$preEnv = Join-Path $PSScriptRoot "EEGResearch\.env"
if (Test-Path $preEnv) {
    # Same match as start.sh's grep (case-insensitive, spaces allowed); keep them in parity.
    $heartOn = @(Get-Content $preEnv) -match '^\s*FACE_HEART_ENABLED\s*=\s*true\s*$' | ForEach-Object { $true } | Select-Object -First 1
    if (-not $heartOn) { $heartOn = $false }
}
if ($Camera -and $NoEmotion -and -not $Gaze -and -not $heartOn) {
    Write-Host "-NoEmotion without -Gaze leaves the camera with nothing to measure." -ForegroundColor Red
    Write-Host "  Add -Gaze, or drop -Camera." -ForegroundColor Yellow
    exit 1
}

# Refused, not promoted: the simulator has no optical channel, so the run would look like -Optics failing.
if ($Optics -and -not $Muse) {
    Write-Host "-Optics needs -Muse: the simulator has no optical channel to enable." -ForegroundColor Red
    Write-Host "  Add -Muse, or drop -Optics." -ForegroundColor Yellow
    exit 1
}
# Same: under the simulator the local calm is a placeholder all session.
if ($LocalCalm -and -not $Muse) {
    Write-Host "-LocalCalm needs -Muse: the simulator delivers no raw stream to score calm from." -ForegroundColor Red
    Write-Host "  Add -Muse, or drop -LocalCalm." -ForegroundColor Yellow
    exit 1
}
$spectrumSource = if ($LocalCalm) { "local" } else { "sdk" }
if ($OpticsPreset -and -not $Optics) {
    Write-Host "-OpticsPreset does nothing without -Optics." -ForegroundColor Red
    Write-Host "  Add -Optics, or drop -OpticsPreset." -ForegroundColor Yellow
    exit 1
}
# The bridge would fall back to 1035 and say so only in its own window.
if ($OpticsPreset -and $OpticsPreset -notmatch '^103[1-6]$') {
    Write-Host "-OpticsPreset '$OpticsPreset' is not a preset (expected 1031-1036)." -ForegroundColor Red
    Write-Host "  16 CH: 1031/1032   8 CH: 1033/1034   4 CH: 1035/1036   (odd = low power)" -ForegroundColor Yellow
    exit 1
}
# Warned, not refused: reproducing the 16 CH bandwidth cliff needs this rung.
if ($OpticsPreset -in @("1031", "1032")) {
    Write-Host "  WARNING: PRESET_$OpticsPreset is 16 CH optics." -ForegroundColor Red
    Write-Host "  Measured on hardware: BLE link drops within ~20s and electrode contact" -ForegroundColor Yellow
    Write-Host "  collapses to [4,4,4,4] -- it takes EEG down with it. 1033-1036 held for" -ForegroundColor Yellow
    Write-Host "  minutes. Proceeding, since reproducing that measurement needs this rung." -ForegroundColor Yellow
}

$root        = $PSScriptRoot
$eegDir      = Join-Path $root "EEGResearch"
$backendDir  = Join-Path $root "Website\AdaptiveLearning\backend"
$frontendDir = Join-Path $root "Website\AdaptiveLearning\frontend"
$bridgeExe   = Join-Path $eegDir "native_bridge\build\Release\muse_native_bridge.exe"
$sdkDir      = Join-Path $eegDir "libmuse_windows_8.0.5"
$model       = "llama3.1:8b"
$emotionModel = Join-Path $eegDir "models\emotion-ferplus-8.onnx"
$landmarkModel = Join-Path $eegDir "models\face_landmarker.task"

function Update-DeviceRegistry {
    # Compose EEG_DEVICES: drop the camera entry, re-point an existing `default:` entry to
    # this run's headband, keep other stations; with -camera, ensure `default:` and append it.
    # -DryRun validates and writes nothing. See CLAUDE.md, *The device registry is composed*.
    param([string]$path, [string]$headband, [string]$camera = "", [switch]$DryRun)
    if (!(Test-Path $path)) { return $true }
    # -Last 1: a duplicated key would make an array; dotenv takes the last.
    $line = @(Get-Content $path) | Where-Object { $_ -match '^EEG_DEVICES=' } | Select-Object -Last 1
    if (!$line) {
        if ($camera -and -not $DryRun) { Set-EnvKey $path "EEG_DEVICES" "$headband,$camera" }
        return $true
    }
    $current = ($line -split '=', 2)[1]
    $entries = @($current -split ',' | Where-Object { $_ -and ($_ -notmatch ':face(@|$)') })
    # A named station on the headband's address cannot share its bridge port, and the backend
    # drives `default`; only the user can resolve it, so return $false. sim entries are exempt.
    $addr = ($headband -split ':', 2)[1]
    $clash = @($entries | Where-Object {
        ($_ -notmatch '^default:') -and ($addr -ne 'sim') -and (($_ -split ':', 2)[1] -eq $addr) })
    if ($clash.Count -gt 0) {
        Write-Host "EEG_DEVICES names $($clash[0]) on the bridge address this run's headband needs ($addr)." -ForegroundColor Red
        Write-Host "  The backend drives the 'default' device, and two muse devices cannot share a bridge port." -ForegroundColor Yellow
        Write-Host "  Give that station its own port, remove it, or run without -Muse. Neither .env was changed." -ForegroundColor Yellow
        return $false
    }
    if ($DryRun) { return $true }
    $kept = @($entries | ForEach-Object { if ($_ -match '^default:') { $headband } else { $_ } })
    if ($camera) {
        if (-not ($kept -match '^default:')) { $kept = @($headband) + $kept }
        $kept += $camera
    }
    Set-EnvKey $path "EEG_DEVICES" ($kept -join ',')
    return $true
}

function Set-EnvKey {
    # Rewrite a key in a .env, or append it if absent (a -replace alone misses a missing key).
    param([string]$path, [string]$key, [string]$value)
    if (!(Test-Path $path)) { return }
    $lines = @(Get-Content $path)
    if ($lines -match "^$key=") {
        ($lines -replace "^$key=.*", "$key=$value") | Set-Content $path
    } else {
        Add-Content $path "$key=$value"
    }
}

function Check-Venv {
    param([string]$dir)
    $activate = Join-Path $dir ".venv\Scripts\Activate.ps1"
    $pyExe    = Join-Path $dir ".venv\Scripts\python.exe"
    $needRebuild = $false

    if (!(Test-Path $activate)) {
        $needRebuild = $true
        Write-Host "  No venv found in $dir -- creating one..." -ForegroundColor Yellow
    } else {
        # Check the venv was built with the same Python version currently on PATH
        $systemVer = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        $venvVer   = & $pyExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($venvVer -ne $systemVer) {
            Write-Host "  Venv Python ($venvVer) does not match system Python ($systemVer) -- rebuilding..." -ForegroundColor Yellow
            Remove-Item -Recurse -Force (Join-Path $dir ".venv")
            $needRebuild = $true
        }
    }

    if ($needRebuild) {
        Push-Location $dir
        python -m venv .venv
        $pip = Join-Path $dir ".venv\Scripts\pip.exe"
        $setup = Join-Path $dir "pyproject.toml"
        if (Test-Path $setup) {
            & $pip install -e . -q
        } else {
            & $pip install -r requirements.txt -q
        }
        Pop-Location
    }
}

function Start-Window {
    param([string]$title, [string]$workdir, [string]$command)
    $full = "cd '$workdir'; $command"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $full -WindowStyle Normal
    Write-Host "  Started: $title" -ForegroundColor Green
    Start-Sleep -Seconds 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  AdaptiveLearning -- Starting Stack"    -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1. Ollama, skipped when the backend is configured for Claude.
# `Test-Path` first: Select-String on a missing file is terminating under Stop.
$llmProvider = "ollama"
$backendEnvPath = Join-Path $backendDir ".env"
if (Test-Path $backendEnvPath) {
    $providerLine = Select-String -Path $backendEnvPath -Pattern '^\s*LLM_PROVIDER\s*=\s*(\S+)' |
        Select-Object -First 1
    # Guard the match too: .Matches[0] on an absent key is a null-array index.
    if ($providerLine -and $providerLine.Matches.Count -gt 0) {
        $llmProvider = $providerLine.Matches[0].Groups[1].Value.Trim().ToLower()
    }
}

Write-Host "[1/5] Ollama (LLM)" -ForegroundColor Cyan
if ($llmProvider -eq "claude") {
    Write-Host "  LLM_PROVIDER=claude in backend/.env -- skipping Ollama." -ForegroundColor Gray
    Write-Host "  Nothing local is needed for question generation." -ForegroundColor Gray
} elseif (!(Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host "  Ollama not found. Install from https://ollama.com then re-run." -ForegroundColor Yellow
} else {
    $running = ollama list 2>$null
    if (!$running) {
        Start-Window "Ollama" $root "ollama serve"
        Start-Sleep -Seconds 3
    } else {
        Write-Host "  Ollama already running -- skipping." -ForegroundColor Gray
    }
    $models = ollama list 2>$null
    if ($models -notmatch [regex]::Escape($model)) {
        Write-Host "  Pulling $model (this may take a while)..." -ForegroundColor Yellow
        ollama pull $model
    }
}

# 2. Native bridge + EEG source config
$eegEnv = Join-Path $eegDir ".env"
$backendEnv = Join-Path $backendDir ".env"
$frontendEnv = Join-Path $frontendDir ".env"

# Validate the registry before any .env write, so a refusal changes nothing;
# the composed value is applied on each branch after provisioning.
$headband = if ($Muse) { "default:muse@8765" } else { "default:sim" }
$cameraEntry = if ($Camera) { "camera:face@$CameraIndex" } else { "" }
if (-not (Update-DeviceRegistry $eegEnv $headband $cameraEntry -DryRun)) { exit 1 }

if ($Muse) {
    Write-Host "[2/5] Native Muse Bridge" -ForegroundColor Cyan

    # Point EEGResearch at the real headband
    if (Test-Path $eegEnv) {
        (Get-Content $eegEnv) -replace '^EEG_SOURCE=.*', 'EEG_SOURCE=muse' | Set-Content $eegEnv
    }

    if (!(Test-Path $bridgeExe)) {
        Write-Host "  Bridge exe not found -- building now..." -ForegroundColor Yellow
        if (!(Test-Path $sdkDir)) {
            Write-Host "  ERROR: libMuse SDK not found at $sdkDir" -ForegroundColor Red
            exit 1
        }
        Push-Location $eegDir
        & ".\scripts\run_native_bridge.ps1" -EnableLibMuse -LibMuseSdkDir $sdkDir -BuildOnly
        Pop-Location
    }

    # libmuse.dll must sit next to the exe at runtime
    $dll    = Join-Path $sdkDir "examples\lib\release\x64\libmuse.dll"
    $dllDst = Join-Path (Split-Path $bridgeExe) "libmuse.dll"
    if (!(Test-Path $dllDst)) {
        Write-Host "  Copying libmuse.dll next to bridge exe..." -ForegroundColor Yellow
        Copy-Item $dll $dllDst
    }

    # Set in the launching window, not a .env: the bridge reads getenv directly. Backticked
    # to expand in the child. The supervisor's bounded restarts inherit them.
    $bridgeSupervisor = Join-Path $eegDir "scripts\run_bridge_supervised.ps1"
    $bridgeCmd = "& '$bridgeSupervisor' -Exe '$bridgeExe'"
    if ($Optics) {
        if ($OpticsPreset) {
            $bridgeCmd = "`$env:MUSE_OPTICS_PRESET='$OpticsPreset'; $bridgeCmd"
        }
        $bridgeCmd = "`$env:MUSE_ENABLE_OPTICS='1'; $bridgeCmd"
        $rung = if ($OpticsPreset) { "PRESET_$OpticsPreset" } else { "PRESET_1035 (bridge default)" }
        Write-Host "  Optics ON -- $rung. Heart rate needs a 2025 Athena; older" -ForegroundColor Yellow
        Write-Host "  models have no PRESET_10xx range and stay on PRESET_21." -ForegroundColor Gray
        Write-Host "  First reading is withheld until a second window agrees, so" -ForegroundColor Gray
        Write-Host "  expect ~35s before a bpm appears." -ForegroundColor Gray
    }
    Start-Window "Muse Bridge :8765" $eegDir $bridgeCmd
    Write-Host "  Waiting 3s for bridge to start..." -ForegroundColor Gray
    Start-Sleep -Seconds 3
} else {
    Write-Host "[2/5] Simulator mode -- switching EEG_SOURCE to sim" -ForegroundColor Gray
    if (Test-Path $eegEnv) {
        (Get-Content $eegEnv) -replace '^EEG_SOURCE=.*', 'EEG_SOURCE=sim' | Set-Content $eegEnv
        Write-Host "  Set EEG_SOURCE=sim in EEGResearch/.env" -ForegroundColor Gray
    }
}

# 2b. Camera
if ($Camera) {
    Write-Host "[2/5] Camera (index $CameraIndex)" -ForegroundColor Cyan
    Check-Venv $eegDir

    # The optional `face` extra is checked at setup, not when a lesson begins.
    Push-Location $eegDir
    # One module per probe, stderr silenced inside Python: `2>$null` on a native command is terminating under Stop.
    $missing = @()
    foreach ($mod in @("cv2", "onnxruntime")) {
        & ".\.venv\Scripts\python.exe" -c `
            "import sys, os; sys.stderr = open(os.devnull, 'w'); import $mod"
        if ($LASTEXITCODE -ne 0) { $missing += $mod }
    }
    if ($missing.Count -gt 0) {
        Pop-Location
        # `.[face,gaze]` under -Gaze, or they fail again at the mediapipe check.
        $extra = if ($Gaze) { ".[face,gaze]" } else { ".[face]" }
        Write-Host "  ERROR: the 'face' extra is not installed in EEGResearch/.venv" -ForegroundColor Red
        Write-Host "    could not import: $($missing -join ', ')" -ForegroundColor Red
        Write-Host "  Install it with:" -ForegroundColor Yellow
        Write-Host "    cd EEGResearch; .\.venv\Scripts\Activate.ps1; pip install -e `"$extra`"" -ForegroundColor Yellow
        Write-Host "  If cv2 is the one failing and pip says it is already installed," -ForegroundColor Yellow
        Write-Host "  uninstall opencv-python first -- it and opencv-contrib-python" -ForegroundColor Yellow
        Write-Host "  both provide cv2, and whichever landed last owns the import." -ForegroundColor Yellow
        exit 1
    }

    # MediaPipe is its own extra; `ensure_model` imports nothing heavy, so without this
    # check setup succeeds and gaze dies on the first frame as `landmarker_unavailable`.
    if ($Gaze) {
        # Same stderr handling as the cv2 probe.
        & ".\.venv\Scripts\python.exe" -c `
            "import sys, os; sys.stderr = open(os.devnull, 'w'); import mediapipe"
        if ($LASTEXITCODE -ne 0) {
            Pop-Location
            Write-Host "  ERROR: -Gaze needs the 'gaze' extra, which is not installed" -ForegroundColor Red
            Write-Host "  Install it with:" -ForegroundColor Yellow
            Write-Host "    cd EEGResearch; .\.venv\Scripts\Activate.ps1; pip install -e `".[face,gaze]`"" -ForegroundColor Yellow
            exit 1
        }
    }

    # Fetch and verify the 35 MB FER+ model at setup, not on the first frame.
    if (-not $NoEmotion) {
        Write-Host "  Checking emotion model..." -ForegroundColor Gray
        & ".\.venv\Scripts\python.exe" -c "from pathlib import Path; from src.app.services.face_emotion import ensure_model; ensure_model(Path(r'$emotionModel'))"
        if ($LASTEXITCODE -ne 0) {
            Pop-Location
            Write-Host "  ERROR: emotion model could not be fetched or failed verification" -ForegroundColor Red
            exit 1
        }
    }
    # Likewise; the sidecar never fetches it, so a student's laptop stays offline when a camera opens.
    if ($Gaze) {
        Write-Host "  Checking face landmark model..." -ForegroundColor Gray
        & ".\.venv\Scripts\python.exe" -c "from src.app.services.face_landmarks import ensure_model; ensure_model(r'$landmarkModel')"
        if ($LASTEXITCODE -ne 0) {
            Pop-Location
            Write-Host "  ERROR: face landmark model could not be fetched or failed verification" -ForegroundColor Red
            exit 1
        }
    }
    Pop-Location

    # Applied only after provisioning succeeded.
    $null = Update-DeviceRegistry $eegEnv $headband $cameraEntry
    Set-EnvKey $eegEnv "FACE_ENABLED" "true"
    Set-EnvKey $eegEnv "FACE_CAMERA_INDEX" "$CameraIndex"
    # Every FACE_* key is written on both branches, so no stale value survives.
    Set-EnvKey $eegEnv "FACE_GAZE_ENABLED" $(if ($Gaze) { "true" } else { "false" })
    # Explicit, not the config default (`true`).
    Set-EnvKey $eegEnv "FACE_EMOTION_ENABLED" $(if ($NoEmotion) { "false" } else { "true" })
    Set-EnvKey $eegEnv "FACE_LANDMARK_MODEL_PATH" "$landmarkModel"
    # Read back, since the registry is composed onto existing stations.
    Write-Host "  $(@(Get-Content $eegEnv) | Where-Object { $_ -match '^EEG_DEVICES=' } | Select-Object -Last 1)" -ForegroundColor Gray

    # The camera records only under push: face_signals' one writer is /api/signals/face.
    # Mode keys are written on both branches, so a stale push cannot disable a later poller.
    Set-EnvKey $eegEnv "PUSH_ENABLED" "true"
    Set-EnvKey $eegEnv "BACKEND_URL" "http://127.0.0.1:8000"
    # From the flag on both branches, so a hand-edited `local` cannot survive.
    Set-EnvKey $eegEnv "EEG_SPECTRUM_SOURCE" $spectrumSource
    Set-EnvKey $backendEnv "INGEST_MODE" "push"
    # The page calls the sidecar with its API_TOKEN; unset, every browser call 401s.
    # Guard the file and the match: a fresh checkout has neither. -Last 1 as dotenv reads.
    $apiToken = $null
    if (Test-Path $eegEnv) {
        $tokenLine = Select-String -Path $eegEnv -Pattern '^API_TOKEN=(.*)$' | Select-Object -Last 1
        if ($tokenLine) { $apiToken = $tokenLine.Matches[0].Groups[1].Value }
    }
    if ($apiToken) {
        Set-EnvKey $frontendEnv "VITE_EEG_LOCAL_TOKEN" $apiToken
    } else {
        # The sidecar writes API_TOKEN on first start, so the next run picks it up.
        Write-Host "  No API_TOKEN in $eegEnv yet -- VITE_EEG_LOCAL_TOKEN not set." -ForegroundColor Yellow
        Write-Host "  The browser will 401 against the sidecar. Re-run this script once it has started." -ForegroundColor Yellow
    }
    Write-Host "  INGEST_MODE = push (the camera's only writer is the push endpoint)" -ForegroundColor Gray
} else {
    Set-EnvKey $eegEnv "FACE_ENABLED" "false"
    Set-EnvKey $eegEnv "FACE_GAZE_ENABLED" "false"
    Set-EnvKey $eegEnv "FACE_EMOTION_ENABLED" "false"
    # Back to pull: the backend polls the sidecar.
    Set-EnvKey $eegEnv "PUSH_ENABLED" "false"
    Set-EnvKey $backendEnv "INGEST_MODE" "pull"
    Set-EnvKey $eegEnv "EEG_SPECTRUM_SOURCE" $spectrumSource

    # Drops the camera entry and re-points the headband entry.
    $null = Update-DeviceRegistry $eegEnv $headband
}

# 3. EEGResearch backend
Write-Host "[3/5] EEGResearch backend (port 8001)" -ForegroundColor Cyan
Check-Venv $eegDir
$eegCmd = ".\.venv\Scripts\Activate.ps1; uvicorn src.app.main:app --host 127.0.0.1 --port 8001 --reload"
Start-Window "EEG Backend :8001" $eegDir $eegCmd
Start-Sleep -Seconds 2

# 4. Website backend
Write-Host "[4/5] Website backend (port 8000)" -ForegroundColor Cyan
Check-Venv $backendDir
$apiCmd = ".\.venv\Scripts\Activate.ps1; uvicorn main:app --reload --port 8000"
Start-Window "Website Backend :8000" $backendDir $apiCmd
Start-Sleep -Seconds 2

# 5. Frontend
Write-Host "[5/5] Frontend (Vite)" -ForegroundColor Cyan
$nmPath = Join-Path $frontendDir "node_modules"
if (!(Test-Path $nmPath)) {
    Write-Host "  node_modules not found -- running npm install..." -ForegroundColor Yellow
    Push-Location $frontendDir
    npm install
    Pop-Location
}
Start-Window "Frontend :5173" $frontendDir "npm run dev"

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  All services started!"                 -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Frontend:    http://localhost:5173"    -ForegroundColor White
Write-Host "  Website API: http://localhost:8000"    -ForegroundColor White
Write-Host "  EEG API:     http://localhost:8001"    -ForegroundColor White
if ($Muse) {
    Write-Host "  Muse Bridge: port 8765"            -ForegroundColor White
}
if ($Camera) {
    Write-Host "  Camera:      index $CameraIndex"   -ForegroundColor White
}
Write-Host ""
if ($Muse) {
    Write-Host "  Turn on your Muse S and click Connect Headband in the app." -ForegroundColor Yellow
} else {
    Write-Host "  Running in simulator mode. Use .\start.ps1 -Muse to enable the headband." -ForegroundColor Gray
}
if (!$Camera) {
    Write-Host "  No camera. Use .\start.ps1 -Camera to enable facial capture." -ForegroundColor Gray
}
Write-Host ""
