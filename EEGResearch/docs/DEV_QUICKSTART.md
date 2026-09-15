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
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8001/api/v1/muse/status" -Headers $hLearner | ConvertTo-Json -Depth 4
```

(An earlier version of this file curled `/api/v1/metrics`, which does not exist. `muse/status`
takes the learner token and answers under the simulator too: `muse_connected: false` with the
ingestion health fields.)

Expected `state` envelope:

- `status`: `ok` or `idle`
- `data`: interpreted payload or `null`
- `message`: human-readable status

## 4) Run tests

From the **repo root**, not from `EEGResearch` — `Settings` loads `.env` relative to the cwd, and
the `.env` you just edited would override field defaults for every test that constructs
`Settings()`, producing `test_face_*` failures that read as a code regression (CLAUDE.md, *Running
and testing*):

```powershell
cd C:\AdaptiveLearning
$env:EEG_SOURCE = "sim"; $env:API_TOKEN = "t"; $env:ADMIN_TOKEN = "a"
EEGResearch\.venv\Scripts\python.exe -m pytest EEGResearch\tests -q
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
