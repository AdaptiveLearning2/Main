# Dev Quickstart

Minimal flow for verifying the EEGResearch backend locally. EEGResearch runs on **port 8001**.

## 1) Install and configure

```powershell
cd C:\AdaptiveLearning\EEGResearch
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
Copy-Item .env.example .env   # then set API_TOKEN and ADMIN_TOKEN to real values
```

`API_TOKEN`/`ADMIN_TOKEN` are required — the app now fails to start if they're missing, rather
than falling back to a guessable default. The scripts and commands below read the same tokens
from `$env:API_TOKEN`/`$env:ADMIN_TOKEN`, so set those in your shell to match `.env`:

```powershell
$env:API_TOKEN = "<value you set in .env>"
$env:ADMIN_TOKEN = "<value you set in .env>"
```

## 2) Run with simulator

```powershell
.\scripts\run_and_watch.ps1
```

This starts FastAPI on :8001, begins a simulated EEG session, and tails live state in the terminal.
(Pass `-LearnerToken`/`-AdminToken` explicitly instead if you'd rather not set the env vars above.)

## 3) Verify API responses

```powershell
$hLearner = @{ Authorization = "Bearer $env:API_TOKEN" }
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8001/api/v1/state" -Headers $hLearner | ConvertTo-Json -Depth 6
```

```powershell
$hAdmin = @{ Authorization = "Bearer $env:ADMIN_TOKEN" }
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8001/api/v1/metrics" -Headers $hAdmin | ConvertTo-Json -Depth 4
```

Expected `state` envelope:

- `status`: `ok` or `idle`
- `data`: interpreted payload or `null`
- `message`: human-readable status

## 4) Run tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 5) Stop everything

Press `Ctrl+C` in the watcher terminal, then:

```powershell
$hAdmin = @{ Authorization = "Bearer $env:ADMIN_TOKEN" }
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8001/api/v1/session/stop" -Headers $hAdmin
```

Force-kill if needed:

```powershell
Get-Process python,pythonw -ErrorAction SilentlyContinue | Stop-Process -Force
```
