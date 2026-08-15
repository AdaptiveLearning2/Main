param(
    [switch]$Muse,
    [switch]$Camera,
    [int]$CameraIndex = 0,
    # Gaze is a second detector on the sampled frames and needs its own model
    # file, so it is opt-in even when the camera is on. Implies -Camera.
    [switch]$Gaze,
    # Emotion is on whenever the camera is, so turning it off needs a switch.
    # Worth having: gaze needs no 35 MB FER+ model, so gaze-only is a real and
    # much cheaper deployment.
    [switch]$NoEmotion,
    # The headband's optical channels -- PPG, and therefore heart rate. Off by
    # default, and a flag rather than a default because turning it on moves a
    # 2025 Athena off PRESET_21: a bandwidth trade with a measured cliff, and an
    # EEG risk in its own right, since the preset change moves bit depth 12 -> 14
    # and a silent EEG regression would be blamed on whatever shipped beside it.
    #
    # Unlike every other flag here this one is passed to the bridge process
    # rather than written to a .env. The bridge is a C++ process that reads its
    # own environment directly and never loads config.py, so a MUSE_ENABLE_OPTICS
    # line in EEGResearch/.env is read by nothing -- the version of this mistake
    # that looks like it worked.
    [switch]$Optics,
    # Which rung: 1031/1032 are 16 CH, 1033/1034 8 CH, 1035/1036 4 CH, odd being
    # low power. Empty leaves the bridge on its own default (1035, the bottom).
    [string]$OpticsPreset = ""
)

$ErrorActionPreference = "Stop"

# Made real rather than merely documented. Gaze is a channel of the camera
# device, so every line that provisions or enables it sits inside the -Camera
# block -- and without this, `./start.ps1 -Gaze` would run to completion having
# silently done nothing at all, which is the failure this script exists to turn
# into a message. Promoted rather than refused: the intent is unambiguous.
if ($Gaze -and -not $Camera) {
    Write-Host "-Gaze implies -Camera; enabling the camera too." -ForegroundColor Yellow
    $Camera = $true
}

# The sidecar refuses to construct a camera adapter with every channel off,
# because a camera that computes nothing is indistinguishable from a student out
# of shot. Caught here so it reads as a bad combination of flags rather than as
# a stack trace from a sidecar that started and then would not connect.
#
# It has to consult FACE_HEART_ENABLED, which this script never writes: heart is
# the third channel in the adapter's guard, it is off by default, and it is
# hand-set in the .env when someone is testing that path. Without this read the
# two copies of one rule disagree -- PowerShell refusing a heart-only camera the
# Python underneath would accept.
$heartOn = $false
$preEnv = Join-Path $PSScriptRoot "EEGResearch\.env"
if (Test-Path $preEnv) {
    # Same shape as start.sh's grep: leading space, space around `=`, and any
    # casing. The two drifted apart the moment they were written by hand --
    # PowerShell's -match is case-insensitive by default and grep's is not, so
    # a hand-edited `FACE_HEART_ENABLED=True` was read on Windows and missed on
    # a Mac. Parity was this guard's whole point.
    $heartOn = @(Get-Content $preEnv) -match '^\s*FACE_HEART_ENABLED\s*=\s*true\s*$' | ForEach-Object { $true } | Select-Object -First 1
    if (-not $heartOn) { $heartOn = $false }
}
if ($Camera -and $NoEmotion -and -not $Gaze -and -not $heartOn) {
    Write-Host "-NoEmotion without -Gaze leaves the camera with nothing to measure." -ForegroundColor Red
    Write-Host "  Add -Gaze, or drop -Camera." -ForegroundColor Yellow
    exit 1
}

# Refused rather than promoted, which is the opposite call to -Gaze above. That
# promotion was safe because the intent was unambiguous and the cost was a camera
# nobody asked for. This flag configures a *headband*, and the alternative to one
# is the simulator -- which models no optical channel at all, so every window
# would be refused `no_samples` and the run would look exactly like -Optics not
# working. Guessing here would manufacture that.
if ($Optics -and -not $Muse) {
    Write-Host "-Optics needs -Muse: the simulator has no optical channel to enable." -ForegroundColor Red
    Write-Host "  Add -Muse, or drop -Optics." -ForegroundColor Yellow
    exit 1
}
if ($OpticsPreset -and -not $Optics) {
    Write-Host "-OpticsPreset does nothing without -Optics." -ForegroundColor Red
    Write-Host "  Add -Optics, or drop -OpticsPreset." -ForegroundColor Yellow
    exit 1
}
# A value outside the range is a typo, not a preference. The bridge already says
# so and falls back to 1035 -- but it says so on stderr in its own window, so the
# session records on a rung nobody chose while the operator believes otherwise.
# Refusing here is the difference between a wrong number and no number.
if ($OpticsPreset -and $OpticsPreset -notmatch '^103[1-6]$') {
    Write-Host "-OpticsPreset '$OpticsPreset' is not a preset (expected 1031-1036)." -ForegroundColor Red
    Write-Host "  16 CH: 1031/1032   8 CH: 1033/1034   4 CH: 1035/1036   (odd = low power)" -ForegroundColor Yellow
    exit 1
}
# Warned, not refused. 16 CH at 64Hz was measured dropping the BLE link within
# ~20s and collapsing electrode contact from [1,1,1,1] to [4,4,4,4] -- it takes
# EEG down with it. But selecting it is how that was measured, so the rung stays
# reachable and the script says what is known about it instead.
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
$backendEnv = Join-Path $backendDir ".env"
$frontendEnv = Join-Path $frontendDir ".env"
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

    # Set in the window that launches the exe, not in a .env: the bridge reads
    # getenv directly. Backticked so PowerShell expands them in the *child*.
    $bridgeCmd = "& '$bridgeExe'"
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

    # The `face` extra is optional, so a machine that has never installed it is
    # the normal case rather than a broken one. Checked here, at setup, because
    # the alternative is the sidecar starting cleanly and the camera failing
    # only when a lesson begins.
    Push-Location $eegDir
    # Probed one module at a time, with stderr silenced *inside* Python rather
    # than by `2>$null`. On PowerShell 5.1 redirecting a native command's stderr
    # wraps each line in an ErrorRecord, and under the `$ErrorActionPreference =
    # "Stop"` set at the top of this file that is terminating -- so a missing
    # module killed the script here, before the message below could explain it,
    # and surfaced as a bare NativeCommandError naming neither the module nor
    # the fix. One probe per module so the report can say which one.
    $missing = @()
    foreach ($mod in @("cv2", "onnxruntime")) {
        & ".\.venv\Scripts\python.exe" -c `
            "import sys, os; sys.stderr = open(os.devnull, 'w'); import $mod"
        if ($LASTEXITCODE -ne 0) { $missing += $mod }
    }
    if ($missing.Count -gt 0) {
        Pop-Location
        # `.[face,gaze]` when -Gaze was asked for: sending someone to `.[face]`
        # here only makes them fail again at the mediapipe check below.
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

    # MediaPipe is its own extra and is not in `.[face]`. Checked here for the
    # same reason cv2 is: without it the model download below still succeeds --
    # `ensure_model` imports nothing heavy -- so setup reports success, writes
    # FACE_GAZE_ENABLED=true, and the channel then fails on the first frame of a
    # lesson as `landmarker_unavailable`, indistinguishable from a missing
    # model file. That is precisely the failure this whole block exists to move
    # from lesson time to setup time.
    if ($Gaze) {
        # Same stderr handling as the cv2 probe above, and for the same reason:
        # `2>$null` here would terminate the script before this message ran.
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

    # Fetch and verify the FER+ model now, not on the first frame. A 35 MB
    # download in front of a student's first session would look like the
    # feature being broken, and a checksum failure is an install problem that
    # should be seen here.
    if (-not $NoEmotion) {
        Write-Host "  Checking emotion model..." -ForegroundColor Gray
        & ".\.venv\Scripts\python.exe" -c "from pathlib import Path; from src.app.services.face_emotion import ensure_model; ensure_model(Path(r'$emotionModel'))"
        if ($LASTEXITCODE -ne 0) {
            Pop-Location
            Write-Host "  ERROR: emotion model could not be fetched or failed verification" -ForegroundColor Red
            exit 1
        }
    }
    # Same argument as the emotion model above, and the same failure if it is
    # skipped: without this the landmarker is built on the first frame of a
    # lesson, finds nothing, and the gaze channel is dead for the session. The
    # sidecar deliberately will not fetch it itself -- a student's laptop must
    # not reach the internet when a camera opens.
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

    $headband = if ($Muse) { "default:muse@8765" } else { "default:sim" }
    Set-EnvKey $eegEnv "EEG_DEVICES" "$headband,camera:face@$CameraIndex"
    Set-EnvKey $eegEnv "FACE_ENABLED" "true"
    Set-EnvKey $eegEnv "FACE_CAMERA_INDEX" "$CameraIndex"
    # Written on both branches, never left to whatever a previous run set. A
    # stale "true" here with no -Gaze would enable a channel the operator did
    # not ask for on this run, which is the same trap the EEG_DEVICES cleanup
    # below exists to avoid.
    Set-EnvKey $eegEnv "FACE_GAZE_ENABLED" $(if ($Gaze) { "true" } else { "false" })
    # Written explicitly rather than left to the config default, which is
    # `true`. The default made emotion silently on whenever the camera was, so
    # this file described two thirds of the camera's configuration and the rest
    # lived in a Python default -- and a reader checking what a session recorded
    # would have had to know that to get the right answer.
    Set-EnvKey $eegEnv "FACE_EMOTION_ENABLED" $(if ($NoEmotion) { "false" } else { "true" })
    Set-EnvKey $eegEnv "FACE_LANDMARK_MODEL_PATH" "$landmarkModel"
    Write-Host "  EEG_DEVICES = $headband,camera:face@$CameraIndex" -ForegroundColor Gray

    # The camera only records under push, so -Camera selects it. `face_signals`
    # has exactly one writer -- /api/signals/face, which the sidecar POSTs to --
    # and the poller never writes that table, so a camera configured under pull
    # captures frames and stores nothing.
    #
    # Written on both branches for the same reason FACE_* is: a stale
    # INGEST_MODE=push left over from a camera run would disable the poller on a
    # later headband-only run, and a headband that records nothing while the
    # page says "streaming" is exactly what explicit modes exist to prevent.
    Set-EnvKey $eegEnv "PUSH_ENABLED" "true"
    Set-EnvKey $eegEnv "BACKEND_URL" "http://127.0.0.1:8000"
    Set-EnvKey $backendEnv "INGEST_MODE" "push"
    # The page talks to the sidecar directly under push, and it authenticates
    # with the sidecar's own API_TOKEN. Copied here rather than left to a
    # hand-edit: unset, `call()` omits the Authorization header entirely and
    # every sidecar request 401s -- while the sidecar looks perfectly healthy
    # from a terminal, because curl sends the token and the browser does not.
    $apiToken = (Select-String -Path $eegEnv -Pattern '^API_TOKEN=(.*)$').Matches.Groups[1].Value
    if ($apiToken) { Set-EnvKey $frontendEnv "VITE_EEG_LOCAL_TOKEN" $apiToken }
    Write-Host "  INGEST_MODE = push (the camera's only writer is the push endpoint)" -ForegroundColor Gray
} else {
    Set-EnvKey $eegEnv "FACE_ENABLED" "false"
    Set-EnvKey $eegEnv "FACE_GAZE_ENABLED" "false"
    Set-EnvKey $eegEnv "FACE_EMOTION_ENABLED" "false"
    # Back to pull: the backend polls the sidecar, which is what a
    # single-machine headband deployment wants and what the poller's consent
    # and rollup paths are exercised against.
    Set-EnvKey $eegEnv "PUSH_ENABLED" "false"
    Set-EnvKey $backendEnv "INGEST_MODE" "pull"

    # Remove only the camera entry this script writes, leaving any other devices
    # alone. Blanking EEG_DEVICES outright would silently destroy a hand-written
    # multi-headband registry -- "station1:muse@8765,station2:muse@8766" -- in a
    # file the docs tell people to edit. A stale camera entry still has to go, or
    # a later plain run keeps opening the webcam.
    if (Test-Path $eegEnv) {
        # Select-Object -Last 1: Where-Object yields an array if EEG_DEVICES
        # somehow appears twice, and -split on an array would misparse. The last
        # occurrence is what a dotenv reader would take.
        $line = @(Get-Content $eegEnv) | Where-Object { $_ -match '^EEG_DEVICES=' } | Select-Object -Last 1
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
