param(
    [string]$HostName = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [string]$LearnerToken = $env:API_TOKEN,
    [string]$AdminToken = $env:ADMIN_TOKEN
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($LearnerToken)) {
    throw "LearnerToken not set. Pass -LearnerToken or set `$env:API_TOKEN before running this script."
}
if ([string]::IsNullOrWhiteSpace($AdminToken)) {
    throw "AdminToken not set. Pass -AdminToken or set `$env:ADMIN_TOKEN before running this script."
}

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (!(Test-Path $python)) {
    throw "Python not found at $python. Create the venv first."
}

$env:API_TOKEN = $LearnerToken
$env:ADMIN_TOKEN = $AdminToken
$env:HOST = $HostName
$env:PORT = "$Port"

Write-Host "Starting FastAPI server..."
$server = Start-Process -FilePath $python -ArgumentList "-m","uvicorn","src.app.main:app","--host",$HostName,"--port",$Port -WorkingDirectory $root -PassThru

$baseUrl = "http://$HostName`:$Port"
$healthUrl = "$baseUrl/healthz"
$startUrl = "$baseUrl/api/v1/session/start"
$isHealthy = $false

Write-Host "Waiting for API health..."
for ($i = 0; $i -lt 40; $i++) {
    try {
        $health = Invoke-RestMethod -Method Get -Uri $healthUrl
        if ($health.status -eq "ok") {
            $isHealthy = $true
            break
        }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}

if (-not $isHealthy) {
    if ($server -and !$server.HasExited) {
        Stop-Process -Id $server.Id -Force
    }
    throw "API failed health check at $healthUrl"
}

$headers = @{ Authorization = "Bearer $AdminToken" }
Invoke-RestMethod -Method Post -Uri $startUrl -Headers $headers | Out-Null

Write-Host ""
Write-Host "Simulator started."
Write-Host "State API: $baseUrl/api/v1/state   (Authorization: Bearer $LearnerToken)"
Write-Host "WebSocket: ws://$HostName`:$Port/ws/live?token=$LearnerToken"
Write-Host "Stop simulator:  Invoke-RestMethod -Method Post -Uri '$baseUrl/api/v1/session/stop' -Headers @{ Authorization = 'Bearer $AdminToken' }"
Write-Host "Stop server PID: $($server.Id)  (example: Stop-Process -Id $($server.Id))"
