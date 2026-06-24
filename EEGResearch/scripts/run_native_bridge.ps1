param(
    [switch]$EnableLibMuse,
    [string]$LibMuseSdkDir = "",
    [ValidateRange(0, 65535)]
    [int]$Port = 0,
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release",
    [string]$Generator = "Visual Studio 18 2026",
    [string]$Architecture = "x64"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path $PSScriptRoot -Parent
$sourceDir = Join-Path $repoRoot "native_bridge"
$buildDir = Join-Path $sourceDir "build"

function Resolve-CMakeExe {
    $cmd = Get-Command cmake -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    $candidates = @(
        "C:\Program Files\CMake\bin\cmake.exe",
        "C:\Program Files (x86)\CMake\bin\cmake.exe",
        "C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe",
        "C:\Program Files\Microsoft Visual Studio\2022\Professional\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe",
        "C:\Program Files\Microsoft Visual Studio\2022\Enterprise\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe",
        "C:\Program Files\Microsoft Visual Studio\2026\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe",
        "C:\Program Files\Microsoft Visual Studio\2026\Professional\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe",
        "C:\Program Files\Microsoft Visual Studio\2026\Enterprise\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
    )

    foreach ($path in $candidates) {
        if (Test-Path $path) {
            return $path
        }
    }

    throw @"
CMake was not found.

Install one of:
1) CMake from https://cmake.org/download/
2) Visual Studio C++ CMake tools component

Then reopen terminal and retry:
.\scripts\run_native_bridge.ps1 -EnableLibMuse
"@
}

$cmakeExe = Resolve-CMakeExe

function Resolve-Generator([string]$RequestedGenerator, [string]$CmakePath) {
    if (![string]::IsNullOrWhiteSpace($RequestedGenerator)) {
        return $RequestedGenerator
    }

    $helpText = & $CmakePath --help
    if ($helpText -match "Visual Studio 18 2026") {
        return "Visual Studio 18 2026"
    }
    if ($helpText -match "Visual Studio 17 2022") {
        return "Visual Studio 17 2022"
    }

    throw "No supported Visual Studio generator found in CMake. Install Visual Studio C++ build tools."
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $FilePath $($Arguments -join ' ')"
    }
}

if (!(Test-Path $sourceDir)) {
    throw "Missing native bridge source directory: $sourceDir"
}

if ([string]::IsNullOrWhiteSpace($LibMuseSdkDir)) {
    $LibMuseSdkDir = Join-Path $repoRoot "libmuse_windows_8.0.5"
}
if ($EnableLibMuse -and !(Test-Path $LibMuseSdkDir)) {
    throw "LibMuse SDK directory not found: $LibMuseSdkDir"
}

$resolvedGenerator = Resolve-Generator -RequestedGenerator $Generator -CmakePath $cmakeExe

$configureArgs = @(
    "-S", $sourceDir,
    "-B", $buildDir,
    "-G", $resolvedGenerator,
    "-A", $Architecture
)

if ($EnableLibMuse) {
    $configureArgs += "-DENABLE_LIBMUSE=ON"
    $configureArgs += "-DLIBMUSE_SDK_DIR=$LibMuseSdkDir"
    Write-Host "Configuring native bridge with libMuse support..."
} else {
    Write-Host "Configuring native bridge (synthetic mode)..."
}

$cacheFile = Join-Path $buildDir "CMakeCache.txt"
if (Test-Path $cacheFile) {
    $cacheText = Get-Content -Raw $cacheFile
    $cachedGenerator = ""
    if ($cacheText -match "CMAKE_GENERATOR:INTERNAL=(.+)") {
        $cachedGenerator = $Matches[1].Trim()
    }
    if ($cachedGenerator -and $cachedGenerator -ne $resolvedGenerator) {
        Write-Host "CMake generator changed ($cachedGenerator -> $resolvedGenerator). Recreating build directory..."
        Remove-Item -Recurse -Force $buildDir
    }
}

Invoke-CheckedCommand -FilePath $cmakeExe -Arguments $configureArgs

Write-Host "Building native bridge ($Configuration)..."
Invoke-CheckedCommand -FilePath $cmakeExe -Arguments @("--build", $buildDir, "--config", $Configuration)

$exePath = Join-Path $buildDir "$Configuration\muse_native_bridge.exe"
if (!(Test-Path $exePath)) {
    throw "Expected binary not found: $exePath"
}

Write-Host "Starting native bridge: $exePath"
if ($Port -ne 0) {
    $env:MUSE_BRIDGE_PORT = "$Port"
    Write-Host "MUSE_BRIDGE_PORT=$($env:MUSE_BRIDGE_PORT)"
} elseif ($env:MUSE_BRIDGE_PORT) {
    Write-Host "MUSE_BRIDGE_PORT=$($env:MUSE_BRIDGE_PORT) (inherited from environment)"
} else {
    Write-Host "MUSE_BRIDGE_PORT not set (default 8765 inside muse_native_bridge.exe)"
}
Write-Host "Press Ctrl+C to stop."
& $exePath
