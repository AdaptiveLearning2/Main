param(
    [switch]$Muse,
    [switch]$Camera,
    [int]$CameraIndex = 0
)

$ErrorActionPreference = "Stop"
$root        = $PSScriptRoot
$eegDir      = Join-Path $root "EEGResearch"
$backendDir  = Join-Path $root "Website\AdaptiveLearning\backend"
$frontendDir = Join-Path $root "Website\AdaptiveLearning\frontend"
$bridgeExe   = Join-Path $eegDir "native_bridge\build\Release\muse_native_bridge.exe"
$sdkDir      = Join-Path $eegDir "libmuse_windows_8.0.5"
$model       = "llama3.1:8b"
$emotionModel = Join-Path $eegDir "models\emotion-ferplus-8.onnx"

function Set-EnvKey {
    # Rewrite a key in a .env, or append it if absent. Appending matters: a
    # first-time checkout has no FACE_* lines at all, and a -replace against a
    # missing key silently does nothing -- the flag would appear to work and
    # change no behaviour.
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

# 1. Ollama
Write-Host "[1/5] Ollama (LLM)" -ForegroundColor Cyan
if (!(Get-Command ollama -ErrorAction SilentlyContinue)) {
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

    $bridgeCmd = "& '$bridgeExe'"
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

    # The `face` extra is optional, so a machine that has never installed it is
    # the normal case rather than a broken one. Checked here, at setup, because
    # the alternative is the sidecar starting cleanly and the camera failing
    # only when a lesson begins.
    Push-Location $eegDir
    & ".\.venv\Scripts\python.exe" -c "import cv2, onnxruntime" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Pop-Location
        Write-Host "  ERROR: the 'face' extra is not installed in EEGResearch/.venv" -ForegroundColor Red
        Write-Host "  Install it with:" -ForegroundColor Yellow
        Write-Host "    cd EEGResearch; .\.venv\Scripts\Activate.ps1; pip install -e `".[face]`"" -ForegroundColor Yellow
        exit 1
    }

    # Fetch and verify the FER+ model now, not on the first frame. A 35 MB
    # download in front of a student's first session would look like the
    # feature being broken, and a checksum failure is an install problem that
    # should be seen here.
    Write-Host "  Checking emotion model..." -ForegroundColor Gray
    & ".\.venv\Scripts\python.exe" -c "from pathlib import Path; from src.app.services.face_emotion import ensure_model; ensure_model(Path(r'$emotionModel'))"
    if ($LASTEXITCODE -ne 0) {
        Pop-Location
        Write-Host "  ERROR: emotion model could not be fetched or failed verification" -ForegroundColor Red
        exit 1
    }
    Pop-Location

    $headband = if ($Muse) { "default:muse@8765" } else { "default:sim" }
    Set-EnvKey $eegEnv "EEG_DEVICES" "$headband,camera:face@$CameraIndex"
    Set-EnvKey $eegEnv "FACE_ENABLED" "true"
    Set-EnvKey $eegEnv "FACE_CAMERA_INDEX" "$CameraIndex"
    Write-Host "  EEG_DEVICES = $headband,camera:face@$CameraIndex" -ForegroundColor Gray
} else {
    Set-EnvKey $eegEnv "FACE_ENABLED" "false"

    # Remove only the camera entry this script writes, leaving any other devices
    # alone. Blanking EEG_DEVICES outright would silently destroy a hand-written
    # multi-headband registry -- "station1:muse@8765,station2:muse@8766" -- in a
    # file the docs tell people to edit. A stale camera entry still has to go, or
    # a later plain run keeps opening the webcam.
    if (Test-Path $eegEnv) {
        $line = @(Get-Content $eegEnv) | Where-Object { $_ -match '^EEG_DEVICES=' }
        if ($line) {
            $current = ($line -split '=', 2)[1]
            $kept = @($current -split ',' | Where-Object { $_ -and ($_ -notmatch ':face(@|$)') })
            Set-EnvKey $eegEnv "EEG_DEVICES" ($kept -join ',')
        }
    }
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
