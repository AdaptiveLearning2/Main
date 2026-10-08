<#
.SYNOPSIS
Builds the student sensor kit's two installers: Setup (with kit.json and update.json) and Update (code only,
which the self-updater installs), from the libMuse bridge, the frozen sidecar and both camera models, after a
self-test of the result. Each installer's SHA-256 is written beside it.

.DESCRIPTION
Runs on the developer's Windows machine and needs Python 3.14 on PATH, Visual Studio with the C++
workload, CMake, Inno Setup 6 and the libMuse SDK folder; see DEVELOPER_SETUP_WINDOWS.md, "Building the
student kit". Intermediate files go to EEGResearch\build\kit and the installer to EEGResearch\dist\kit.
The arguments are checked as start.ps1 -Hosted checks its own, before anything is built.

.EXAMPLE
.\EEGResearch\scripts\build_student_kit.ps1 -BackendUrl https://name.onrender.com -FrontendOrigin https://name.pages.dev -LearnerToken <VITE_EEG_LOCAL_TOKEN> -DownloadKeyFile E:\kit-keys\download.key -Version 0.2.0
#>
param(
    [Parameter(Mandatory = $true)][string]$BackendUrl,
    [Parameter(Mandatory = $true)][string]$FrontendOrigin,
    [Parameter(Mandatory = $true)][string]$LearnerToken,
    # The file holding the update gate's DOWNLOAD_KEY (kit_release.py newdownloadkey), so the key is on no command line.
    [Parameter(Mandatory = $true)][string]$DownloadKeyFile,
    [Parameter(Mandatory = $true)][string]$Version,
    [ValidateSet("latest", "canary")][string]$UpdateFeed = "latest",
    [ValidateRange(0, 99)][int]$CameraIndex = 0,
    [string]$OpticsPreset = "",
    [string]$LibMuseSdkDir = "",
    # Optional signing: the arguments for `signtool sign` before the file name, e.g. Artifact Signing's /dlib and /dmdf.
    [string[]]$SignToolArgs = @(),
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$eeg = Split-Path $PSScriptRoot -Parent
$work = Join-Path $eeg "build\kit"
$out = Join-Path $eeg "dist\kit"
$installer = Join-Path $eeg "installer"

function Invoke-Step {
    param([string]$what, [scriptblock]$command)
    Write-Host "== $what" -ForegroundColor Cyan
    # Function-local: under Stop, an exe's stderr line is fatal once output is captured (*>, Tee-Object) on 5.1.
    $ErrorActionPreference = "Continue"
    $global:LASTEXITCODE = $null  # stays null if the exe never started, which Continue reports and moves past
    & $command
    if ($null -eq $LASTEXITCODE) { throw "$what failed: the command did not start" }
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" }
}

function Find-Tool {
    param([string]$name, [string[]]$candidates)
    foreach ($path in $candidates) { if ($path -and (Test-Path $path)) { return $path } }
    throw "$name not found; looked in: $($candidates -join '; ')"
}

function Get-KitArgs {
    # name=value: a token can start with "-" (1 in 64), and 5.1 drops an empty argument such as no preset.
    param([string]$backend, [string]$origin, [string]$token, [int]$camera, [string]$preset, [string]$version)
    return @("--backend-url=$backend", "--frontend-origin=$origin", "--learner-token=$token",
             "--camera-index=$camera", "--optics-preset=$preset", "--version=$version")
}

if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "-Version must look like 0.1.0" }
$kitArgs = Get-KitArgs $BackendUrl $FrontendOrigin $LearnerToken $CameraIndex $OpticsPreset $Version
if (-not (Test-Path $DownloadKeyFile)) { throw "-DownloadKeyFile $DownloadKeyFile does not exist" }
$downloadKey = (Get-Content -Raw $DownloadKeyFile).Trim()

# The same check the launcher runs on kit.json, before anything slow happens. Standard library only.
Push-Location $eeg
$env:KIT_DOWNLOAD_KEY = $downloadKey  # src.kit.update_settings reads it from here, never from its arguments
try {
    Invoke-Step "Checking the arguments" { python -m src.kit.config check @kitArgs }
    Invoke-Step "Checking the update settings" { python -m src.kit.update_settings check "--feed=$UpdateFeed" }
} finally { Pop-Location; Remove-Item Env:KIT_DOWNLOAD_KEY }
$pythonVersion = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ($pythonVersion -ne "3.14") { throw "Python 3.14 must be first on PATH (found $pythonVersion): the locks are resolved for it" }

if (-not $LibMuseSdkDir) { $LibMuseSdkDir = Join-Path $eeg "libmuse_windows_8.0.5" }
if (-not (Test-Path $LibMuseSdkDir)) { throw "libMuse SDK not found at $LibMuseSdkDir; pass -LibMuseSdkDir" }
Invoke-Step "Building the bridge with libMuse" {
    & (Join-Path $PSScriptRoot "run_native_bridge.ps1") -EnableLibMuse -LibMuseSdkDir $LibMuseSdkDir -BuildOnly
}
$bridgeExe = Join-Path $eeg "native_bridge\build\Release\muse_native_bridge.exe"

# A fresh venv from the locks every time: the dev venv drifts from them, and the kit must match what was reviewed.
$venv = Join-Path $work "venv"
if (Test-Path $venv) { Remove-Item -Recurse -Force $venv }
New-Item -ItemType Directory -Force $work | Out-Null
Invoke-Step "Creating the build venv" { python -m venv $venv }
$py = Join-Path $venv "Scripts\python.exe"
Invoke-Step "Installing the locked sidecar and build tools" {
    & $py -m pip install --quiet --require-hashes -r (Join-Path $eeg "requirements-gaze.lock") `
        -r (Join-Path $installer "requirements-kit.lock")
}

$models = Join-Path $work "models"
Invoke-Step "Fetching and verifying the camera models" { & $py (Join-Path $installer "kit_build.py") models $models }
Invoke-Step "Freezing the launcher and sidecar" { & $py (Join-Path $installer "kit_build.py") freeze $work (Join-Path $work "dist") }
Invoke-Step "Staging the kit folder" {
    & $py (Join-Path $installer "kit_build.py") stage (Join-Path $work "dist") $bridgeExe $models (Join-Path $work "stage")
}
$app = Join-Path $work "stage\AdaptiveLearningSensors"
# The installed version, read by the updater: kit.json's goes stale once an update replaces the code.
Set-Content -Encoding ascii -NoNewline -Path (Join-Path $app "version.txt") -Value $Version
Push-Location $eeg
$env:KIT_DOWNLOAD_KEY = $downloadKey
try {
    Invoke-Step "Writing kit.json" { & $py -m src.kit.config write (Join-Path $app "kit.json") @kitArgs }
    Invoke-Step "Writing update.json" { & $py -m src.kit.update_settings write (Join-Path $app "update.json") "--feed=$UpdateFeed" }
} finally { Pop-Location; Remove-Item Env:KIT_DOWNLOAD_KEY }
Invoke-Step "Auditing every bundled binary's DLL imports" { & $py (Join-Path $installer "kit_build.py") audit $app }

$report = Join-Path $work "selftest.json"
if (Test-Path $report) { Remove-Item $report }
Write-Host "== Running the kit's self-test" -ForegroundColor Cyan
$savedPath = $env:Path
# Windows' own folders only, as on a student machine: a DLL on this machine's PATH would stand in for a missing one.
$env:Path = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem"
try {
    # A windowed exe does not set $LASTEXITCODE, so the exit code comes from the process object.
    $run = Start-Process -FilePath (Join-Path $app "AdaptiveLearningSensors.exe") `
        -ArgumentList "--self-test", "`"$report`"" -PassThru
} finally { $env:Path = $savedPath }
$null = $run.Handle  # held now, or 5.1 reports no ExitCode once the process has gone
if (-not $run.WaitForExit(600000)) {
    Stop-Process -Id $run.Id -Force
    throw "the self-test did not finish in 10 minutes; see $work\selftest.console.log"
}
if (-not (Test-Path $report)) { throw "the self-test wrote no report (exit $($run.ExitCode)); see $work\selftest.console.log" }
$result = Get-Content -Raw -Encoding UTF8 $report | ConvertFrom-Json
foreach ($check in $result.checks) {
    $colour = if ($check.ok) { "Green" } else { "Red" }
    Write-Host ("  {0,-5} {1}" -f $(if ($check.ok) { "PASS" } else { "FAIL" }), $check.name) -ForegroundColor $colour
    if (-not $check.ok) { Write-Host "        $($check.detail.error)" -ForegroundColor Yellow }
    if ($check.detail.foreign) { Write-Host "        not the kit's, not failed: $($check.detail.foreign -join ', ')" }
}
if ($run.ExitCode -ne 0 -or -not $result.ok) { throw "the self-test failed; the full report is $report" }
if ($SkipInstaller) { Write-Host "Kit staged and self-tested at $app (no installer: -SkipInstaller)."; return }

$signTool = $null
if ($SignToolArgs.Count -gt 0) {
    $signTool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName | Select-Object -Last 1 -ExpandProperty FullName
    if (-not $signTool) { throw "signtool.exe not found under Windows Kits\10; install the Windows SDK" }
    foreach ($exe in @((Join-Path $app "AdaptiveLearningSensors.exe"), (Join-Path $app "bridge\muse_native_bridge.exe"))) {
        Invoke-Step "Signing $(Split-Path $exe -Leaf)" { & $signTool sign @SignToolArgs $exe }
    }
}

# The Update installer's folder: the signed, self-tested kit less both settings files, checked for both secrets.
$updateStage = Join-Path $work "stage-update"
$updateApp = Join-Path $updateStage "AdaptiveLearningSensors"
if (Test-Path $updateStage) { Remove-Item -Recurse -Force $updateStage }
New-Item -ItemType Directory -Force $updateStage | Out-Null
Copy-Item -Recurse -Path $app -Destination $updateApp
Remove-Item (Join-Path $updateApp "kit.json"), (Join-Path $updateApp "update.json")
$env:KIT_SCAN_SECRETS = "$LearnerToken`n$downloadKey"
try {
    Invoke-Step "Checking the Update copy holds neither secret" { & $py (Join-Path $installer "kit_build.py") scan $updateApp }
} finally { Remove-Item Env:KIT_SCAN_SECRETS }

$iscc = Find-Tool "Inno Setup 6 (ISCC.exe)" @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")
New-Item -ItemType Directory -Force $out | Out-Null
Invoke-Step "Compiling the Setup installer" {
    & $iscc /Q "/DAppVersion=$Version" "/DSourceDir=$app" "/DOutputDir=$out" (Join-Path $installer "student_kit.iss")
}
Invoke-Step "Compiling the Update installer" {
    & $iscc /Q "/DAppVersion=$Version" "/DSourceDir=$updateApp" "/DOutputDir=$out" /DUpdateOnly (Join-Path $installer "student_kit.iss")
}
foreach ($kind in "Setup", "Update") {
    $built = Join-Path $out "AdaptiveLearningSensors-$kind-$Version.exe"
    if ($signTool) { Invoke-Step "Signing the $kind installer" { & $signTool sign @SignToolArgs $built } }
    # publish_kit_update.ps1 accepts only an installer that matches the hash recorded here.
    $hash = (Get-FileHash -Algorithm SHA256 $built).Hash.ToLower()
    Set-Content -Encoding ascii -Path "$built.sha256" -Value "$hash  $(Split-Path $built -Leaf)"
    Write-Host "Built $built" -ForegroundColor Green
    Write-Host "SHA-256 $hash"
}
