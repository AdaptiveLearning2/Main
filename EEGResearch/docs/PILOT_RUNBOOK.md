# Pilot Runbook

EEGResearch runs on **port 8001**. The full stack is launched from `C:\AdaptiveLearning\start.ps1 -Muse`.

## Pre-Flight Checks

- Verify `.env` has non-default `API_TOKEN` and `ADMIN_TOKEN`.
- Confirm host is local/internal only.
- Run `pytest` and ensure all tests pass.
- Muse S headband is charged and not connected to another app (phone app closed, GettingData32 closed).
- If switching from the Python bridge to the C++ bridge: power cycle the headband first to clear BLE state.

## Starting the Stack

```powershell
cd C:\AdaptiveLearning
.\start.ps1 -Muse
```

This launches (in order): Ollama, C++ Muse bridge (:8765), EEGResearch (:8001), website backend (:8000), frontend (:5173).

## Connecting the Headband

1. Open the frontend at `http://localhost:5173` and log in.
2. Navigate to the Adaptive Learning page.
3. Click **Connect Headband** — this triggers a BLE scan then connects via the C++ bridge.
4. Watch the bridge terminal for `CONNECTED` state.

## Session Operations

```powershell
$hAdmin = @{ Authorization = "Bearer admin-token-123" }
$hLearner = @{ Authorization = "Bearer learner-token-123" }
$base = "http://127.0.0.1:8001"

# Start session
Invoke-RestMethod -Method Post -Uri "$base/api/v1/session/start" -Headers $hAdmin

# Check learner state
Invoke-RestMethod -Method Get -Uri "$base/api/v1/state" -Headers $hLearner | ConvertTo-Json -Depth 6

# Check muse connection
Invoke-RestMethod -Method Get -Uri "$base/api/v1/muse/status" -Headers $hLearner | ConvertTo-Json -Depth 4

# WebSocket live stream
# ws://127.0.0.1:8001/ws/live?token=learner-token-123

# Stop session
Invoke-RestMethod -Method Post -Uri "$base/api/v1/session/stop" -Headers $hAdmin
```

## Incident Steps

- **Stream stalls / no EEG data:** check bridge terminal for errors; try disconnect + reconnect headband.
- **BadStateError on connect (`rc=3`):** headband BLE state is stuck. Power cycle the headband (hold button until two beeps, wait 10s), then reconnect.
- **Band powers all zero:** confirm `EEG_SOURCE=muse` in `.env` and that the C++ bridge (not Python bridge) is on port 8765.
- **Token leak suspected:** rotate tokens in `.env` and restart service.
- **Adaptation appears unstable:** check `signal_quality` in state; `poor` quality means insufficient EEG signal — adjust headband fit.
- **`Unable to connect to remote server`:** confirm EEGResearch is up (`GET /healthz`) and no stale watcher loops are running.

## Go/No-Go Gates

- Bridge terminal shows `CONNECTED` for Muse S.
- `/api/v1/state` returns non-zero `focus_score` and `calm_score`.
- `bands` in state payload shows non-zero values (delta/theta/alpha/beta/gamma).
- Adaptation explanation remains clear and non-medical.

**Post-beta:** add security-focused gates (auth, TLS, audit log) before any public or sensitive rollout.
