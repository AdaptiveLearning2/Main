# Developer Setup Guide — macOS

This project is an adaptive learning platform for kids with learning disabilities. It adjusts math question difficulty in real time using EEG biometric data from a Muse S headband.

> **Note:** Live headband support (libMuse SDK) is Windows-only. On macOS the stack runs in simulator mode, which uses synthetic EEG data and supports the complete adaptive question flow.

## Architecture Overview

```
Browser (:5173)
    │
    ▼
Website Backend (:8000)  ──────────────────►  EEGResearch Backend (:8001)
    │  FastAPI                                      │  FastAPI + signal processing
    │  Ollama LLM                                   │  (simulator mode on macOS)
    │  Supabase                                     │
    ▼
Supabase (PostgreSQL + Auth)
```

| Service | Port | Purpose |
|---------|------|---------|
| Frontend | 5173 | Student/teacher/parent UI |
| Website backend | 8000 | LLM question generation, Supabase, auth |
| EEGResearch backend | 8001 | EEG signal processing, adaptation engine |
| Ollama | 11434 | Local LLM (llama3.1:8b) |

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.11+ | https://python.org/downloads or `brew install python` |
| Node.js | 18+ | https://nodejs.org or `brew install node` |
| Ollama | latest | https://ollama.com |
| Git | any | Xcode Command Line Tools: `xcode-select --install` |

---

## 1. Clone and configure

```bash
git clone <repo-url> ~/AdaptiveLearning
cd ~/AdaptiveLearning
```

### Website backend `.env`

```bash
cd Website/AdaptiveLearning/backend
cp .env.example .env
```

Open `.env` and fill in:

```env
SUPABASE_URL=https://<your-project>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<your-service-role-key>
BACKEND_PORT=8000
```

Get these values from your Supabase project → **Settings → API**.

### Frontend `.env`

Create `Website/AdaptiveLearning/frontend/.env`:

```env
VITE_SUPABASE_URL=https://<your-project>.supabase.co
VITE_SUPABASE_ANON_KEY=<your-anon-key>
VITE_API_URL=http://localhost:8000
VITE_EEG_DEBUG=true
```

Get `VITE_SUPABASE_ANON_KEY` from Supabase → **Settings → API → anon public**.

### EEGResearch `.env`

```bash
cd ~/AdaptiveLearning/EEGResearch
cp .env.example .env
```

Simulator mode needs no other changes, but `API_TOKEN` and `ADMIN_TOKEN` are required — the app
fails to start without them, rather than falling back to a guessable default. Set real values for
both before continuing.

---

## 2. Start the stack

```bash
cd ~/AdaptiveLearning
chmod +x start.sh
./start.sh
```

This will:
1. Start Ollama and pull `llama3.1:8b` if not already downloaded (takes a few minutes on first run)
2. Create Python venvs and install dependencies automatically if missing
3. Install frontend `node_modules` if missing
4. Open a Terminal.app window for each service

Once all windows are up, open **http://localhost:5173** in your browser.

> **Terminal.app note:** The script uses AppleScript to open new Terminal windows. Make sure Terminal.app is not blocked in System Settings → Privacy & Security → Automation.

---

## 3. Verify everything is working

```bash
# Website backend health
curl http://localhost:8000/healthz

# EEGResearch health
curl http://localhost:8001/healthz

# EEG state (start a session in the UI first)
curl -H "Authorization: Bearer $(grep '^API_TOKEN=' EEGResearch/.env | cut -d= -f2)" http://localhost:8001/api/v1/state
```

---

## 4. Stack layout

```
~/AdaptiveLearning/
├── start.sh                           ← launch everything from here
├── DEVELOPER_SETUP_MAC.md             ← this file
│
├── EEGResearch/                       ← EEG signal processing service
│   ├── src/app/
│   │   ├── main.py                    ← FastAPI app, endpoints
│   │   ├── config.py                  ← settings (reads .env)
│   │   └── services/
│   │       ├── eeg_ingestion.py       ← TCP bridge adapter + simulator
│   │       ├── signal_processing.py  ← focus/calm/confidence from EEG
│   │       ├── adaptation.py          ← maps features → question policy
│   │       └── stream_manager.py      ← orchestrates the pipeline
│   ├── docs/                          ← dev quickstart, pilot runbook
│   └── .env                           ← EEGResearch config
│
└── Website/AdaptiveLearning/
    ├── backend/                       ← website FastAPI backend
    │   ├── main.py                    ← question generation, sessions, auth
    │   ├── eeg_client.py              ← calls EEGResearch :8001
    │   ├── LLM_*_generation.py        ← Ollama question generators (10 topics)
    │   └── .env                       ← Supabase keys, port
    └── frontend/                      ← React + Vite
        ├── src/pages/student/
        │   └── Adaptive.jsx           ← core adaptive learning page
        └── .env                       ← Supabase anon key, API URL
```

---

## 5. Common tasks

### Add a Python dependency (EEGResearch)

```bash
cd ~/AdaptiveLearning/EEGResearch
source .venv/bin/activate
pip install <package>
# Then add to pyproject.toml under [project] dependencies
```

### Add a Python dependency (website backend)

```bash
cd ~/AdaptiveLearning/Website/AdaptiveLearning/backend
source .venv/bin/activate
pip install <package>
# Then add to requirements.txt
```

### Add a frontend dependency

```bash
cd ~/AdaptiveLearning/Website/AdaptiveLearning/frontend
npm install <package>
```

---

## 6. Ports at a glance

| Port | Service |
|------|---------|
| 5173 | Frontend (Vite dev server) |
| 8000 | Website backend (FastAPI) |
| 8001 | EEGResearch backend (FastAPI) |
| 11434 | Ollama (LLM) |
