# Dev Quickstart

Minimal copy-paste flow for local backend verification. **Beta focus is EEG processing and API shape**, not production security—use on a trusted machine/network.

## 1) Install and configure

```powershell
.\.venv\Scripts\python.exe -m pip install -e .[dev]
Copy-Item .env.example .env
```

Set tokens in `.env` (or pass them on script arguments):

- `API_TOKEN=learner-token-123`
- `ADMIN_TOKEN=admin-token-123`

## 2) Run simulator and watcher

```powershell
.\scripts\run_and_watch.ps1 -LearnerToken "learner-token-123" -AdminToken "admin-token-123"
```

This starts:

- FastAPI server
- EEG simulator session
- Terminal live state watcher

## 3) Verify API responses

In another terminal:

```powershell
$hLearner = @{ Authorization = "Bearer learner-token-123" }
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/api/v1/state" -Headers $hLearner | ConvertTo-Json -Depth 6
```

```powershell
$hAdmin = @{ Authorization = "Bearer admin-token-123" }
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/api/v1/metrics" -Headers $hAdmin | ConvertTo-Json -Depth 4
```

Expected `state` envelope shape:

- `status`: `ok` or `idle`
- `data`: interpreted payload object or `null`
- `message`: human-readable status

## 4) Run tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 5) Stop everything

- Press `Ctrl+C` in the watcher terminal.
- Stop simulator session:

```powershell
$hAdmin = @{ Authorization = "Bearer admin-token-123" }
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/session/stop" -Headers $hAdmin
```

- Force stop backend if needed:

```powershell
Get-Process python,pythonw -ErrorAction SilentlyContinue | Stop-Process -Force
```
