# Developer Setup Guide — Windows

This project is an adaptive learning platform for kids with learning disabilities. It adjusts math question difficulty in real time using EEG biometric data from a Muse S headband.

## Architecture Overview

```
Browser (:5173)
    │
    ▼
Website Backend (:8000)  ──────────────────►  EEGResearch Backend (:8001)
    │  FastAPI                                      │  FastAPI + signal processing
    │  Ollama LLM                                   │
    │  Supabase                                     ▼
    │                                     Muse Bridge (:8765)
    │                                          │  C++ exe (libMuse SDK)
    │                                          ▼
    │                                     Muse S Headband (BLE)
    ▼
Supabase (PostgreSQL + Auth)
```

| Service | Port | Purpose |
|---------|------|---------|
| Frontend | 5173 | Student/teacher/parent UI |
| Website backend | 8000 | LLM question generation, Supabase, auth |
| EEGResearch backend | 8001 | EEG signal processing, adaptation engine |
| Muse bridge | 8765 | libMuse SDK → TCP JSON stream |
| Ollama | 11434 | Local LLM (llama3.1:8b) |

---

## Prerequisites

### Required for everyone

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.11+ | https://python.org/downloads |
| Node.js | 18+ | https://nodejs.org |
| Ollama | latest | https://ollama.com |
| Git | any | https://git-scm.com |

### Required only for live headband support

| Tool | Notes |
|------|-------|
| Visual Studio 2022 or 2026 | Workload: **Desktop development with C++**. `scripts/run_native_bridge.ps1` defaults to the `Visual Studio 18 2026` CMake generator; on 2022 pass `-Generator "Visual Studio 17 2022"`, or CMake reports a missing generator that reads like a broken toolchain. |
| Windows SDK | Installed via VS Installer (10.0.22621.0 or newer) |
| Muse S headband | Athena hardware, firmware 3.1.x |
| Bluetooth adapter | Built-in or USB dongle |

---

## 1. Clone and configure

```powershell
git clone <repo-url> C:\AdaptiveLearning
cd C:\AdaptiveLearning
```

### Website backend `.env`

```powershell
cd Website\AdaptiveLearning\backend
Copy-Item .env.example .env
```

Open `.env` and fill in:

```env
SUPABASE_URL=https://<your-project>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<your-service-role-key>
BACKEND_PORT=8000
```

Get these values from your Supabase project → **Settings → API**.

### Frontend `.env`

Create `Website\AdaptiveLearning\frontend\.env`:

```env
VITE_SUPABASE_URL=https://<your-project>.supabase.co
VITE_SUPABASE_ANON_KEY=<your-anon-key>
VITE_API_URL=http://localhost:8000
VITE_EEG_DEBUG=true
```

Get `VITE_SUPABASE_ANON_KEY` from Supabase → **Settings → API → anon public**.

### EEGResearch `.env`

```powershell
cd C:\AdaptiveLearning\EEGResearch
Copy-Item .env.example .env
```

Simulator mode needs no other changes, but `API_TOKEN` and `ADMIN_TOKEN` are required — the app
fails to start without them, rather than falling back to a guessable default. Set real values for
both before continuing.

---

## 2. Start the stack (simulator mode)

```powershell
cd C:\AdaptiveLearning
.\start.ps1
```

Every flag the launcher takes (each is documented with its reason in CLAUDE.md, *Running and
testing*):

| Flag | What it does |
|---|---|
| `-Muse` | Real headband: builds the native bridge if needed and sets `EEG_SOURCE=muse`. Without it, the simulator. |
| `-Camera` / `-CameraIndex N` | Webcam device (FER+ emotion); switches ingestion to push mode. |
| `-Gaze` | Gaze and head-pose landmarks; implies `-Camera`. Needs `pip install -e ".[face,gaze]"` run **from `EEGResearch`** and fetches the landmark model at setup. |
| `-NoEmotion` | Turns FER+ off; only valid with `-Gaze` (gaze-only is a real, cheaper deployment). |
| `-Optics` / `-OpticsPreset 103N` | Headband optical channels for heart rate; refused without `-Muse`. Stay on the default rung — see the bandwidth cliff in CLAUDE.md. |
| `-LocalCalm` | Score calm from the sidecar's own spectrum (`EEG_SPECTRUM_SOURCE=local`); refused without `-Muse`; off by decision until a second wearer's capture. |

Every key these flags control is written to the `.env` files on **both** branches of each flag,
so a value from a previous run cannot survive into one that did not ask for it. `start.sh` takes
the same flags in `--kebab-case` (`--optics` is Windows-only in effect, and `--local-calm` is
refused there, since that launcher always runs the simulator). `-Camera` composes its entry onto
`EEG_DEVICES` rather than overwriting a hand-written multi-station list.

This will:
1. Start Ollama and pull `llama3.1:8b` if not already downloaded (takes a few minutes on first run).
   Skipped when `Website\AdaptiveLearning\backend\.env` sets `LLM_PROVIDER=claude`: every model
   call goes through `llm_client.py`, and that provider needs `ANTHROPIC_API_KEY` (plus the
   optional `CLAUDE_*` group — model, max tokens, retries — documented in CLAUDE.md under *Every
   model call goes through `llm_client`*). The default is Ollama so a fresh checkout bills nothing.
2. Create Python venvs and install dependencies automatically if missing
3. Install frontend `node_modules` if missing
4. Launch a terminal window for each service

Once all windows are up, open **http://localhost:5173** in your browser.

In simulator mode the EEG data is synthetic — you can use the full adaptive question flow without a headband.

---

## 3. Verify everything is working

```powershell
# Website backend health
Invoke-RestMethod http://localhost:8000/healthz

# EEGResearch health
Invoke-RestMethod http://localhost:8001/healthz

# EEG state (start a session in the UI first)
$apiToken = (Select-String -Path EEGResearch\.env -Pattern '^API_TOKEN=(.*)$').Matches[0].Groups[1].Value
$h = @{ Authorization = "Bearer $apiToken" }
Invoke-RestMethod http://localhost:8001/api/v1/state -Headers $h | ConvertTo-Json -Depth 4
```

---

## 4. Live headband mode (optional)

### One-time: build the C++ bridge

```powershell
cd C:\AdaptiveLearning\EEGResearch
.\scripts\run_native_bridge.ps1 -EnableLibMuse -BuildOnly
```

This compiles `muse_native_bridge.exe` using Visual Studio. Takes 1–2 minutes.

### Run with headband

```powershell
cd C:\AdaptiveLearning
.\start.ps1 -Muse
```

Then navigate to the Adaptive Learning page in the browser and click **Connect Headband**.

### Troubleshooting the headband

**"BadStateError: headband was already streaming" in the bridge terminal**
The headband's BLE state is stuck from a previous session. Power cycle it: hold the button until you hear two beeps, wait 10 seconds, turn it back on.

**Band powers all showing 0**
Make sure the C++ bridge is the process on port 8765. The bridge terminal should say `bridge_mode: libmuse`.

**Bridge terminal says "bind() failed on 127.0.0.1:8765"**
Another process is on that port. Run:
```powershell
Stop-Process -Name muse_native_bridge -Force -ErrorAction SilentlyContinue
```
Then restart `.\start.ps1 -Muse`.

---

## 5. Stack layout

```
C:\AdaptiveLearning\
├── start.ps1                          ← launch everything from here
├── DEVELOPER_SETUP_WINDOWS.md         ← this file
│
├── EEGResearch\                       ← EEG signal processing service
│   ├── src\app\
│   │   ├── main.py                    ← FastAPI app, endpoints
│   │   ├── config.py                  ← settings (reads .env)
│   │   └── services\
│   │       ├── eeg_ingestion.py       ← TCP bridge adapter + simulator
│   │       ├── signal_processing.py  ← focus/calm/confidence from EEG
│   │       ├── adaptation.py          ← features → learner-state label (diagnostic; difficulty is chosen by the website backend)
│   │       └── stream_manager.py      ← orchestrates the pipeline
│   ├── native_bridge\                 ← C++ bridge source + binary
│   ├── docs\                          ← dev quickstart, pilot runbook
│   └── .env                           ← EEGResearch config
│
└── Website\AdaptiveLearning\
    ├── backend\                       ← website FastAPI backend
    │   ├── main.py                    ← question generation, sessions, auth
    │   ├── eeg_client.py              ← calls EEGResearch :8001
    │   ├── LLM_*_generation.py        ← question generators (17 topics; Ollama or Claude via llm_client.py)
    │   └── .env                       ← Supabase keys, port
    └── frontend\                      ← React + Vite
        ├── src\pages\student\
        │   └── Adaptive.jsx           ← core adaptive learning page
        └── .env                       ← Supabase anon key, API URL
```

---

## 6. Common tasks

### Add a Python dependency (EEGResearch)

```powershell
cd C:\AdaptiveLearning\EEGResearch
.\.venv\Scripts\Activate.ps1
pip install <package>
# Then add to pyproject.toml under [project] dependencies
```

### Add a Python dependency (website backend)

```powershell
cd C:\AdaptiveLearning\Website\AdaptiveLearning\backend
.\.venv\Scripts\Activate.ps1
pip install <package>
# Then add to requirements.txt
```

### Add a frontend dependency

```powershell
cd C:\AdaptiveLearning\Website\AdaptiveLearning\frontend
npm install <package>
```

### Rebuild the C++ bridge after source changes

```powershell
Stop-Process -Name muse_native_bridge -Force -ErrorAction SilentlyContinue
cd C:\AdaptiveLearning
.\start.ps1 -Muse   # rebuilds automatically if exe is missing
```

---

## 7. Ports at a glance

| Port | Service |
|------|---------|
| 5173 | Frontend (Vite dev server) |
| 8000 | Website backend (FastAPI) |
| 8001 | EEGResearch backend (FastAPI) |
| 8765 | Muse C++ bridge (TCP) |
| 11434 | Ollama (LLM) |
