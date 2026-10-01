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
    [switch]$LocalCalm,
    # A student machine for the hosted site: starts only the bridge and sidecar, pushing to -BackendUrl.
    [switch]$Hosted,
    [string]$BackendUrl = "",
    # The site's https origin (e.g. https://name.pages.dev), the sidecar's only allowed caller.
    [string]$FrontendOrigin = "",
    # The site's VITE_EEG_LOCAL_TOKEN: every student machine's sidecar must share it.
    [string]$LearnerToken = ""
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
    $heartOn = @(Get-Content -Encoding UTF8 $preEnv) -match '^\s*FACE_HEART_ENABLED\s*=\s*true\s*$' | ForEach-Object { $true } | Select-Object -First 1
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
    $line = @(Get-Content -Encoding UTF8 $path) | Where-Object { $_ -match '^EEG_DEVICES=' } | Select-Object -Last 1
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
    # UTF-8 with no BOM both ways: the sidecar decodes .env as UTF-8, and dotenv reads a BOM into the first key.
    param([string]$path, [string]$key, [string]$value)
    if (!(Test-Path $path)) { return }
    $full = Convert-Path $path
    try {
        $lines = [IO.File]::ReadAllLines($full, (New-Object Text.UTF8Encoding $false, $true))
    } catch [Text.DecoderFallbackException] {
        # Saved in the ANSI code page: convert it, or a character a person typed is rewritten as U+FFFD.
        $lines = [IO.File]::ReadAllLines($full, [Text.Encoding]::Default)
    }
    if ($lines -match "^$key=") {
        $lines = $lines -replace "^$key=.*", "$key=$value"
    } else {
        $lines += "$key=$value"
    }
    [IO.File]::WriteAllLines($full, [string[]]$lines, (New-Object Text.UTF8Encoding $false))
}

function Get-EnvValue {
    # The last assignment, as dotenv reads it; $null for a missing file or key.
    param([string]$path, [string]$key)
    if (!(Test-Path $path)) { return $null }
    $line = Select-String -Encoding UTF8 -Path $path -Pattern "^$key=(.*)$" | Select-Object -Last 1
    if ($line) { return $line.Matches[0].Groups[1].Value }
    return $null
}

function New-SidecarToken {
    # 32 random bytes, URL-safe base64: what secrets.token_urlsafe(32) gives.
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return ([Convert]::ToBase64String($bytes)).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Update-SidecarTokens {
    # The sidecar refuses .env.example's `replace-me` tokens, so a real pair is made once here, and
    # the backend gets the same one: under pull it calls the sidecar with it.
    param([string]$eegEnv, [string]$backendEnv)
    if (!(Test-Path $eegEnv)) { return }
    foreach ($pair in @(@("API_TOKEN", "EEG_API_TOKEN"), @("ADMIN_TOKEN", "EEG_ADMIN_TOKEN"))) {
        $current = Get-EnvValue $eegEnv $pair[0]
        # Trimmed and in any case (-like ignores case), as the sidecar reads it.
        $norm = if ($current) { $current.Trim() } else { "" }
        # It refuses equal tokens too (the learner one ships in the page), so a copied admin token is remade.
        # -ceq: tokens are case-sensitive, and the sidecar compares them exactly.
        $sameAsApi = ($pair[0] -eq "ADMIN_TOKEN") -and $current -and ($current -ceq (Get-EnvValue $eegEnv "API_TOKEN"))
        if (-not $norm -or $norm -like "replace-me*" -or $sameAsApi) {
            $token = New-SidecarToken
            Set-EnvKey $eegEnv $pair[0] $token
            Set-EnvKey $backendEnv $pair[1] $token
            Write-Host "  Generated $($pair[0]) (and the backend's $($pair[1]))" -ForegroundColor Gray
        }
    }
}

function Test-HostedArgs {
    # Every reason a -Hosted run would misconfigure the sidecar; empty when it is safe. Writes nothing.
    param([bool]$hosted, [bool]$muse, [string]$backendUrl, [string]$frontendOrigin,
          [string]$learnerToken, [string]$eegEnv)
    $errors = @()
    if (-not $hosted) {
        if ($backendUrl -or $frontendOrigin -or $learnerToken) {
            $errors += "-BackendUrl, -FrontendOrigin and -LearnerToken do nothing without -Hosted."
        }
        return ,$errors
    }
    # The simulator streams whether or not a headband is paired: made-up EEG on a student's record.
    if (-not $muse) {
        $errors += "-Hosted needs -Muse: the simulator would push made-up EEG to the hosted backend."
    }
    # No path: the push client appends /api/signals/..., so /api would post to /api/api/...
    # No user name: GetLeftPart keeps one, and no browser sends it in an Origin.
    $u = $null
    if (-not $backendUrl -or -not [Uri]::TryCreate($backendUrl, 'Absolute', [ref]$u) -or $u.UserInfo -or
            $u.Scheme -ne 'https' -or $u.AbsolutePath -ne '/' -or $u.Query -or $u.Fragment) {
        $errors += "-BackendUrl must be the hosted backend's https address with no path or user name, e.g. https://name.onrender.com."
    }
    $o = $null
    if (-not $frontendOrigin -or -not [Uri]::TryCreate($frontendOrigin, 'Absolute', [ref]$o) -or $o.UserInfo -or
            $o.Scheme -ne 'https' -or $o.AbsolutePath -ne '/' -or $o.Query -or $o.Fragment) {
        $errors += "-FrontendOrigin must be the site's https origin with no path or user name, e.g. https://name.pages.dev."
    }
    # token_urlsafe's alphabet, which survives a .env round trip; the placeholder is refused by the sidecar.
    if ($learnerToken -cnotmatch '^[A-Za-z0-9_-]+$' -or $learnerToken -like 'replace-me*') {
        $errors += "-LearnerToken must be the site's VITE_EEG_LOCAL_TOKEN (letters, digits, - and _)."
    }
    # Set-EnvKey skips a missing file silently, which would start a sidecar on the old settings.
    if (-not (Test-Path $eegEnv)) {
        $errors += "No $eegEnv -- copy EEGResearch\.env.example to .env first."
    }
    return ,$errors
}

function Set-HostedToken {
    # The site's learner token into every copy this machine keeps, before Update-SidecarTokens runs,
    # so it generates only ADMIN_TOKEN (never one equal to this) and a later local run still matches.
    param([string]$eegEnv, [string]$backendEnv, [string]$frontendEnv, [string]$learnerToken)
    Set-EnvKey $eegEnv "API_TOKEN" $learnerToken
    Set-EnvKey $backendEnv "EEG_API_TOKEN" $learnerToken
    Set-EnvKey $frontendEnv "VITE_EEG_LOCAL_TOKEN" $learnerToken
}

function Set-HostedSidecarEnv {
    # Push to the hosted backend and accept calls only from the site, both written as a browser
    # sends an origin (lower case, no default port): CORS compares the strings exactly.
    param([string]$eegEnv, [string]$backendUrl, [string]$frontendOrigin)
    Set-EnvKey $eegEnv "PUSH_ENABLED" "true"
    Set-EnvKey $eegEnv "BACKEND_URL" ([Uri]$backendUrl).GetLeftPart([UriPartial]::Authority)
    Set-EnvKey $eegEnv "ALLOWED_ORIGINS" ([Uri]$frontendOrigin).GetLeftPart([UriPartial]::Authority)
}

function Add-LocalOrigins {
    # Adds whichever local frontend origins the sidecar's allowlist lacks, keeping every other entry.
    param([string]$eegEnv, [string]$localOrigins)
    if (!(Test-Path $eegEnv)) { return }
    $current = Get-EnvValue $eegEnv "ALLOWED_ORIGINS"
    $list = @()
    if ($current) { $list = @($current -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }) }
    foreach ($origin in ($localOrigins -split ',')) {
        if ($list -notcontains $origin) { $list += $origin }
    }
    Set-EnvKey $eegEnv "ALLOWED_ORIGINS" ($list -join ',')
}

# A native command's stdout, stderr dropped. `2>$null` under Stop aborts on PS 5.1: each stderr
# line becomes an ErrorRecord that Stop makes terminating -- in exactly the state being probed.
# Continue is local to this function, so the caller keeps Stop.
function Invoke-Quiet {
    param([scriptblock]$Command)
    $ErrorActionPreference = "Continue"
    & $Command 2>$null
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
        $venvVer   = Invoke-Quiet { & $pyExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" }
        if ($venvVer -ne $systemVer) {
            Write-Host "  Venv Python ($venvVer) does not match system Python ($systemVer) -- rebuilding..." -ForegroundColor Yellow
            Remove-Item -Recurse -Force (Join-Path $dir ".venv")
            $needRebuild = $true
        }
    }

    if ($needRebuild) {
        Push-Location $dir
        python -m venv .venv
        Pop-Location
        Install-VenvDeps $dir
    } elseif (Test-Path (Join-Path $dir "pyproject.toml")) {
        # An editable install keeps the dependency list it was installed with, so what a pulled
        # pyproject adds or raises is installed here, before a window starts and dies on the import.
        $missing = @(Get-MissingDeps $pyExe $dir)
        if ($missing.Count -gt 0) {
            Write-Host "  Installing what this venv lacks: $($missing -join ', ')" -ForegroundColor Yellow
            Install-VenvDeps $dir
            $missing = @(Get-MissingDeps $pyExe $dir)
            if ($missing.Count -gt 0) {
                Write-Host "  ERROR: $dir\.venv still lacks $($missing -join ', '). From that folder run:" -ForegroundColor Red
                Write-Host "    .\.venv\Scripts\pip install -e ." -ForegroundColor Yellow
                exit 1
            }
        }
    }
}

function Get-MissingDeps {
    # Unmet requirements, from the sidecar's own probe; a probe that cannot run reports nothing.
    param([string]$python, [string]$dir)
    $probe = Join-Path $dir "scripts\missing_runtime_deps.py"
    $out = Invoke-Quiet { & $python $probe }
    return @(($out -join ' ') -split '\s+' | Where-Object { $_ })
}

function Install-VenvDeps {
    param([string]$dir)
    Push-Location $dir
    try {
        $pip = Join-Path $dir ".venv\Scripts\pip.exe"
        if (Test-Path (Join-Path $dir "pyproject.toml")) {
            & $pip install -e . -q
        } else {
            & $pip install -r requirements.txt -q
        }
    } finally { Pop-Location }
}

function Get-ClearCommand {
    # Every name, set here or not: the new window runs the user's profile first, which may set one.
    param([string[]]$names)
    return "Remove-Item " + (($names | ForEach-Object { "Env:$_" }) -join ', ') + " -ErrorAction SilentlyContinue; "
}

function Get-SidecarCommand {
    # Hosted, the window first drops every variable the sidecar reads: one left in a session
    # (EEG_SOURCE=sim from a test run, say) beats the .env just written.
    param([bool]$hosted, [string]$python, [string]$eegDir)
    $run = ".\.venv\Scripts\Activate.ps1; uvicorn src.app.main:app --host 127.0.0.1 --port 8001"
    # No --reload for students: nothing on their machine edits the sidecar's code.
    if (-not $hosted) { return "$run --reload" }
    # The names come from the sidecar's own Settings, so a key added there is covered too.
    Push-Location $eegDir
    try {
        $names = @((Invoke-Quiet { & $python -c "from src.app.config import Settings; print(' '.join(f.alias for f in Settings.model_fields.values() if f.alias))" }) -split '\s+' |
            Where-Object { $_ -match '^[A-Za-z_][A-Za-z0-9_]*$' })
    } catch {
        $names = @()  # a missing interpreter throws past Invoke-Quiet; refused below, by name
    } finally { Pop-Location }
    if ($names.Count -eq 0) {
        Write-Host "  ERROR: could not read the sidecar's settings with $python, so its window could" -ForegroundColor Red
        Write-Host "  not be cleared. Close the bridge window, then fix the sidecar's venv." -ForegroundColor Red
        exit 1
    }
    $set = @($names | Where-Object { Test-Path "Env:$_" } | Sort-Object -Unique)
    if ($set.Count -gt 0) {
        Write-Host "  Hosted: clearing $($set -join ', ') in the sidecar's window, so it reads its .env" -ForegroundColor Yellow
    }
    return (Get-ClearCommand $names) + $run
}

function Get-BridgeCommand {
    # The bridge reads getenv, not a .env, so the flags' values are set in its window. Hosted, every
    # variable it reads is dropped first, so the bridge listens where the cleared sidecar looks.
    param([bool]$hosted, [bool]$optics, [string]$preset, [string]$supervisor, [string]$exe)
    $cmd = "& '$supervisor' -Exe '$exe'"
    if ($optics) {
        if ($preset) { $cmd = "`$env:MUSE_OPTICS_PRESET='$preset'; $cmd" }
        $cmd = "`$env:MUSE_ENABLE_OPTICS='1'; $cmd"
    }
    if ($hosted) {
        $cmd = (Get-ClearCommand @('MUSE_BRIDGE_PORT', 'MUSE_ENABLE_OPTICS', 'MUSE_OPTICS_PRESET',
            'MUSE_LIVENESS_TIMEOUT_MS', 'MUSE_AUTO_RECONNECT')) + $cmd
    }
    return $cmd
}

function Start-Window {
    param([string]$title, [string]$workdir, [string]$command)
    $full = "cd '$workdir'; $command"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $full -WindowStyle Normal
    Write-Host "  Started: $title" -ForegroundColor Green
    Start-Sleep -Seconds 1
}

# Before any .env is written or any process started, so a refusal changes nothing.
$hostedErrors = Test-HostedArgs -hosted $Hosted.IsPresent -muse $Muse.IsPresent -backendUrl $BackendUrl `
    -frontendOrigin $FrontendOrigin -learnerToken $LearnerToken -eegEnv (Join-Path $eegDir ".env")
if ($hostedErrors.Count -gt 0) {
    foreach ($e in $hostedErrors) { Write-Host $e -ForegroundColor Red }
    exit 1
}
# Added back on every local run, so a hosted run's origin cannot lock the local frontend out.
$localOrigins = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
if ($Hosted) {
    Write-Host "  AdaptiveLearning -- Student machine"  -ForegroundColor Cyan
} else {
    Write-Host "  AdaptiveLearning -- Starting Stack"    -ForegroundColor Cyan
}
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1. Ollama, skipped when the backend is configured for Claude, and on a hosted student machine.
# `Test-Path` first: Select-String on a missing file is terminating under Stop.
$llmProvider = "ollama"
$backendEnvPath = Join-Path $backendDir ".env"
if ($Hosted) {
    $llmProvider = "hosted"
} elseif (Test-Path $backendEnvPath) {
    $providerLine = Select-String -Encoding UTF8 -Path $backendEnvPath -Pattern '^\s*LLM_PROVIDER\s*=\s*(\S+)' |
        Select-Object -First 1
    # Guard the match too: .Matches[0] on an absent key is a null-array index.
    if ($providerLine -and $providerLine.Matches.Count -gt 0) {
        $llmProvider = $providerLine.Matches[0].Groups[1].Value.Trim().ToLower()
    }
}

Write-Host "[1/5] Ollama (LLM)" -ForegroundColor Cyan
if ($llmProvider -eq "hosted") {
    Write-Host "  Hosted -- the backend at $BackendUrl generates questions; skipping." -ForegroundColor Gray
} elseif ($llmProvider -eq "claude") {
    Write-Host "  LLM_PROVIDER=claude in backend/.env -- skipping Ollama." -ForegroundColor Gray
    Write-Host "  Nothing local is needed for question generation." -ForegroundColor Gray
} elseif (!(Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host "  Ollama not found. Install from https://ollama.com then re-run." -ForegroundColor Yellow
} else {
    $running = Invoke-Quiet { ollama list }
    if (!$running) {
        Start-Window "Ollama" $root "ollama serve"
        Start-Sleep -Seconds 3
    } else {
        Write-Host "  Ollama already running -- skipping." -ForegroundColor Gray
    }
    $models = Invoke-Quiet { ollama list }
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

if ($Hosted) { Set-HostedToken $eegEnv $backendEnv $frontendEnv $LearnerToken }
Update-SidecarTokens $eegEnv $backendEnv

if ($Muse) {
    Write-Host "[2/5] Native Muse Bridge" -ForegroundColor Cyan

    # Point EEGResearch at the real headband. Set-EnvKey adds a missing line, and appends after a
    # misspelt one (dotenv takes the last), so a hosted run can never fall back to the simulator.
    Set-EnvKey $eegEnv "EEG_SOURCE" "muse"

    # Stale counts as missing: an exe older than its source speaks a protocol the sidecar no longer does.
    $bridgeStale = (Test-Path $bridgeExe) -and [bool](Get-ChildItem (Join-Path $eegDir "native_bridge\src") -File |
        Where-Object { $_.LastWriteTime -gt (Get-Item $bridgeExe).LastWriteTime })
    if (!(Test-Path $bridgeExe) -or $bridgeStale) {
        Write-Host "  Bridge exe missing or older than its source -- building now..." -ForegroundColor Yellow
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

    # The supervisor's bounded restarts inherit the window's variables.
    $bridgeSupervisor = Join-Path $eegDir "scripts\run_bridge_supervised.ps1"
    $bridgeCmd = Get-BridgeCommand $Hosted.IsPresent $Optics.IsPresent $OpticsPreset $bridgeSupervisor $bridgeExe
    if ($Optics) {
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
        Set-EnvKey $eegEnv "EEG_SOURCE" "sim"
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
    Write-Host "  $(@(Get-Content -Encoding UTF8 $eegEnv) | Where-Object { $_ -match '^EEG_DEVICES=' } | Select-Object -Last 1)" -ForegroundColor Gray

    # The camera records only under push: face_signals' one writer is /api/signals/face.
    # Mode keys are written on both branches, so a stale push cannot disable a later poller.
    Set-EnvKey $eegEnv "PUSH_ENABLED" "true"
    Set-EnvKey $eegEnv "BACKEND_URL" "http://127.0.0.1:8000"
    # From the flag on both branches, so a hand-edited `local` cannot survive.
    Set-EnvKey $eegEnv "EEG_SPECTRUM_SOURCE" $spectrumSource
    # Hosted: the backend and the page are elsewhere, so only the sidecar is configured.
    if (-not $Hosted) {
        Set-EnvKey $backendEnv "INGEST_MODE" "push"
        # The page calls the sidecar with its API_TOKEN; unset, every browser call 401s.
        # Guard the file and the match: a fresh checkout has neither. -Last 1 as dotenv reads.
        $apiToken = Get-EnvValue $eegEnv "API_TOKEN"
        if ($apiToken) {
            Set-EnvKey $frontendEnv "VITE_EEG_LOCAL_TOKEN" $apiToken
        } else {
            # Only with no EEGResearch/.env at all: the tokens above are generated into an existing one.
            Write-Host "  No $eegEnv -- VITE_EEG_LOCAL_TOKEN not set." -ForegroundColor Yellow
            Write-Host "  Copy EEGResearch/.env.example to .env and re-run this script." -ForegroundColor Yellow
        }
        Write-Host "  INGEST_MODE = push (the camera's only writer is the push endpoint)" -ForegroundColor Gray
    }
} else {
    Set-EnvKey $eegEnv "FACE_ENABLED" "false"
    Set-EnvKey $eegEnv "FACE_GAZE_ENABLED" "false"
    Set-EnvKey $eegEnv "FACE_EMOTION_ENABLED" "false"
    # Back to pull: the backend polls the sidecar.
    Set-EnvKey $eegEnv "PUSH_ENABLED" "false"
    if (-not $Hosted) { Set-EnvKey $backendEnv "INGEST_MODE" "pull" }
    Set-EnvKey $eegEnv "EEG_SPECTRUM_SOURCE" $spectrumSource

    # Drops the camera entry and re-points the headband entry.
    $null = Update-DeviceRegistry $eegEnv $headband
}

# After both branches, so the hosted push target and origin win over their local values.
if ($Hosted) {
    Set-HostedSidecarEnv $eegEnv $BackendUrl $FrontendOrigin
    Write-Host "  Hosted: pushing to $BackendUrl, accepting calls from $FrontendOrigin" -ForegroundColor Gray
} else {
    Add-LocalOrigins $eegEnv $localOrigins
}

# 3. EEGResearch backend
Write-Host "[3/5] EEGResearch backend (port 8001)" -ForegroundColor Cyan
Check-Venv $eegDir
$eegCmd = Get-SidecarCommand $Hosted.IsPresent (Join-Path $eegDir ".venv\Scripts\python.exe") $eegDir
Start-Window "EEG Backend :8001" $eegDir $eegCmd
Start-Sleep -Seconds 2

if (-not $Hosted) {
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
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  All services started!"                 -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
if ($Hosted) {
    Write-Host "  Site:        $FrontendOrigin"      -ForegroundColor White
    Write-Host "  Backend:     $BackendUrl"          -ForegroundColor White
} else {
    Write-Host "  Frontend:    http://localhost:5173"    -ForegroundColor White
    Write-Host "  Website API: http://localhost:8000"    -ForegroundColor White
}
Write-Host "  EEG API:     http://localhost:8001"    -ForegroundColor White
if ($Muse) {
    Write-Host "  Muse Bridge: port 8765"            -ForegroundColor White
}
if ($Camera) {
    Write-Host "  Camera:      index $CameraIndex"   -ForegroundColor White
}
Write-Host ""
if ($Hosted) {
    Write-Host "  Open $FrontendOrigin in this machine's browser, sign in, and click Connect Headband." -ForegroundColor Yellow
} elseif ($Muse) {
    Write-Host "  Turn on your Muse S and click Connect Headband in the app." -ForegroundColor Yellow
} else {
    Write-Host "  Running in simulator mode. Use .\start.ps1 -Muse to enable the headband." -ForegroundColor Gray
}
if (!$Camera) {
    Write-Host "  No camera. Use .\start.ps1 -Camera to enable facial capture." -ForegroundColor Gray
}
Write-Host ""
