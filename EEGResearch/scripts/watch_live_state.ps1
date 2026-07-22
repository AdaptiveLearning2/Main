param(
    [string]$HostName = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [string]$LearnerToken = $env:API_TOKEN,
    [ValidateRange(100, 600000)]
    [int]$IntervalMs = 5000
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($LearnerToken)) {
    throw "LearnerToken not set. Pass -LearnerToken or set `$env:API_TOKEN before running this script."
}

$stateUrl = "http://$HostName`:$Port/api/v1/state"
$headers = @{ Authorization = "Bearer $LearnerToken" }

Write-Host "Watching live EEG state from $stateUrl"
Write-Host "Press Ctrl+C to stop."
Write-Host ""

while ($true) {
    try {
        $resp = Invoke-RestMethod -Method Get -Uri $stateUrl -Headers $headers
        $ts = Get-Date -Format "HH:mm:ss"
        if ($resp.status -eq "ok" -and $resp.data) {
            $state = $resp.data.state.label
            $confidence = $resp.data.state.confidence
            $difficulty = $resp.data.question_policy.difficulty
            $action = $resp.data.question_policy.action
            $reason = $resp.data.state.reason
            Write-Host "[$ts] state=$state confidence=$confidence difficulty=$difficulty action=$action reason='$reason'"
        } else {
            Write-Host "[$ts] status=$($resp.status) message='$($resp.message)'"
        }
    } catch {
        $ts = Get-Date -Format "HH:mm:ss"
        Write-Host "[$ts] error=$($_.Exception.Message)"
    }
    Start-Sleep -Milliseconds $IntervalMs
}
