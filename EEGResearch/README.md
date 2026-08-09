# EEG Research Learning Platform

Python-first service for ingesting real-time Muse S EEG data, interpreting learner cognitive state, and adapting question difficulty.

Runs on **port 8001**. The full stack (frontend + website backend + this service + bridge) is launched from `C:\AdaptiveLearning\start.ps1`.

## Overview

- FastAPI backend with token-protected learner/admin endpoints.
- EEG ingestion from simulator (`EEG_SOURCE=sim`) or native C++ Muse bridge (`EEG_SOURCE=muse`).
- Signal processor computes focus/calm/confidence from raw EEG channels and libMuse band powers.
- Adaptation engine maps features to learner state and question difficulty policy.
- Live state via REST (`GET /api/v1/state`) and WebSocket (`/ws/live`).

## Repo Layout

- `src/app/` — FastAPI app, config, services (ingestion, signal processing, adaptation, stream manager)
- `native_bridge/` — Windows C++ bridge: libMuse SDK → TCP :8765 JSON stream
- `scripts/` — standalone run helpers (bridge, pilot flow, watcher, smoke checks)
- `docs/` — dev quickstart and pilot runbook

## Quick Start

### Recommended: full stack from repo root

```powershell
# Simulator mode
cd C:\AdaptiveLearning
.\start.ps1

# Live Muse S headband
.\start.ps1 -Muse
```

### Standalone EEGResearch only

```powershell
cd C:\AdaptiveLearning\EEGResearch
.\.venv\Scripts\Activate.ps1
uvicorn src.app.main:app --host 127.0.0.1 --port 8001 --reload
```

### Standalone with live bridge

```powershell
# Terminal 1 — build and run bridge
.\scripts\run_native_bridge.ps1 -EnableLibMuse

# Terminal 2 — EEGResearch with muse source
$env:EEG_SOURCE = "muse"
uvicorn src.app.main:app --host 127.0.0.1 --port 8001 --reload
```

## Install

```powershell
cd C:\AdaptiveLearning\EEGResearch
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

## Configuration (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `EEG_SOURCE` | `sim` | `sim` for simulator, `muse` for live bridge |
| `EEG_SAMPLE_HZ` | `4` | Rate the stream manager dequeues EEG samples |
| `MUSE_BRIDGE_HOST` | `127.0.0.1` | Bridge TCP host |
| `MUSE_BRIDGE_PORT` | `8765` | Bridge TCP port |
| `MUSE_BRIDGE_TIMEOUT_SECONDS` | `5` | Timeout waiting for an EEG frame |
| `API_TOKEN` | — | Learner bearer token |
| `ADMIN_TOKEN` | — | Admin bearer token |

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/session/start` | admin | Begin EEG streaming session |
| `POST` | `/api/v1/session/stop` | admin | End session |
| `GET` | `/api/v1/state` | learner | Current EEG features + question policy |
| `GET` | `/api/v1/muse/status` | learner | Bridge connection and device info |
| `POST` | `/api/v1/muse/refresh` | admin | Trigger BLE device scan |
| `POST` | `/api/v1/muse/connect` | admin | Connect to named headband |
| `POST` | `/api/v1/muse/disconnect` | admin | Disconnect headband |
| `GET` | `/api/v1/metrics` | admin | Sample counts and error stats |
| `GET` | `/ws/live?token=<token>` | learner | WebSocket live state stream |

## Payload Notes

`/api/v1/state` response includes:

- `channels`: raw EEG `tp9`, `af7`, `af8`, `tp10`
- `features`: `focus_score`, `calm_score`, `confidence` (0–100), `signal_quality`
- `state`: `label`, `reason`, `confidence`, `focus_score`, `calm_score`
- `ingestion`: connection state, device name, bridge mode
- `bands`: `delta`, `theta`, `alpha`, `beta`, `gamma` (non-zero when bridge provides them)

## Security Note

For beta, keep deployments local/trusted and do not commit real secrets. See `SECURITY.md` for scope and guidance.
