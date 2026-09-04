<#
.SYNOPSIS
Runs muse_native_bridge.exe and restarts it when it exits, a bounded number
of times, printing why each time.

.DESCRIPTION
Nothing else supervises the bridge: start.ps1 launched it once, in its own
window, and if it exited the sidecar backed off to 5s between attempts for
ever while the page read "not answering". A classroom will not have anyone
reading that window.

Bounded, so a persistent failure -- a missing libmuse.dll, port 8765 taken --
cannot loop for ever: more than MaxRestarts exits inside RestartWindowSeconds
stops the loop and leaves the last exit code on screen. Every exit is printed
with its time and code, so the crash a restart hides from the student is still
readable here.

Environment is inherited from the window that runs this, which is where
start.ps1 sets MUSE_ENABLE_OPTICS and friends; nothing here reads or sets
them, so a restart lands on exactly the configuration the session was launched
with. The sidecar reconnects its TCP socket on its own once the bridge is
listening again, and the student's page treats the restarted bridge's
"not connected" as a drop and runs its reconnect.
#>
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [int]$MaxRestarts = 5,
    [int]$RestartWindowSeconds = 600,
    [int]$RestartDelaySeconds = 3
)

$ErrorActionPreference = "Continue"

if (!(Test-Path $Exe)) {
    Write-Host "bridge supervisor: exe not found: $Exe" -ForegroundColor Red
    exit 2
}

$exits = New-Object System.Collections.Generic.List[datetime]
$run = 0
while ($true) {
    $run += 1
    $started = Get-Date
    Write-Host ("bridge supervisor: run {0} starting at {1:HH:mm:ss}" -f $run, $started) -ForegroundColor Cyan
    # Cleared first: an exe that exists but cannot start (a build interrupted
    # mid-cmake, an antivirus-truncated file, a wrong-architecture binary)
    # raises instead of running, and $LASTEXITCODE keeps whatever it held --
    # $null on the first run, the previous run's code after that. Either
    # would have printed as a blank or stale code and `exit $null` reports
    # success for a bridge that never started.
    $LASTEXITCODE = $null
    & $Exe
    $code = $LASTEXITCODE
    $ended = Get-Date
    $lived = [int]($ended - $started).TotalSeconds
    if ($null -eq $code) {
        # PowerShell's own error is printed just above this line.
        Write-Host ("bridge supervisor: did not start at {0:HH:mm:ss} (exe present but not runnable)" -f $ended) -ForegroundColor Red
        $code = 3
    }
    Write-Host ("bridge supervisor: exited at {0:HH:mm:ss} with code {1} after {2}s" -f $ended, $code, $lived) -ForegroundColor Yellow

    # Ctrl+C in the window reaches the exe first and reads as a clean exit;
    # a person stopping it is not a crash to recover from.
    if ($code -eq 0) {
        Write-Host "bridge supervisor: clean exit; not restarting" -ForegroundColor Gray
        exit 0
    }

    $exits.Add($ended)
    $cutoff = $ended.AddSeconds(-$RestartWindowSeconds)
    $recent = @($exits | Where-Object { $_ -ge $cutoff })
    if ($recent.Count -gt $MaxRestarts) {
        Write-Host ("bridge supervisor: {0} exits in {1}s; giving up. Read the lines above, fix the cause, and relaunch." -f $recent.Count, $RestartWindowSeconds) -ForegroundColor Red
        exit $code
    }
    Write-Host ("bridge supervisor: restarting in {0}s ({1} of {2} restarts used in the last {3}s)" -f $RestartDelaySeconds, $recent.Count, $MaxRestarts, $RestartWindowSeconds) -ForegroundColor Gray
    Start-Sleep -Seconds $RestartDelaySeconds
}
