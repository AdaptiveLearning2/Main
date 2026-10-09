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
| Node.js | 24.15+ (or 22.22+) | https://nodejs.org |
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

Simulator mode needs no other changes. `API_TOKEN` and `ADMIN_TOKEN` are required, and the sidecar
refuses to start on the `replace-me` placeholders the file ships with, or on two identical tokens.
`./start.ps1` replaces placeholders with generated tokens on its first run and gives the backend the
same pair; if you start the sidecar by hand, set your own (32+ characters) first.

---

## 2. Start the stack (simulator mode)

```powershell
cd C:\AdaptiveLearning
.\start.ps1
```

Every flag the launcher takes (each is documented with its reason in `docs/environments.md`,
*Launcher flags*):

| Flag | What it does |
|---|---|
| `-Muse` | Real headband: builds the native bridge if needed and sets `EEG_SOURCE=muse`. Without it, the simulator. |
| `-Camera` / `-CameraIndex N` | Webcam device (FER+ emotion); switches ingestion to push mode. |
| `-Gaze` | Gaze and head-pose landmarks; implies `-Camera`. Needs `pip install -e ".[face,gaze]"` run **from `EEGResearch`** and fetches the landmark model at setup. |
| `-NoEmotion` | Turns FER+ off; only valid with `-Gaze` (gaze-only is a real, cheaper deployment). |
| `-Optics` / `-OpticsPreset 103N` | Headband optical channels for heart rate; refused without `-Muse`. Stay on the default rung — see the bandwidth cliff in `docs/signals.md`. |
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
   optional `CLAUDE_*` group — model, max tokens, retries — documented in `docs/question-generation.md`
   under *Every model call goes through `llm_client`*). The default is Ollama so a fresh checkout bills nothing.
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

### A student machine for the hosted site

The website and its backend are hosted; a student machine runs only the bridge and the EEG sidecar,
which pushes readings to the hosted backend. That backend must run `INGEST_MODE=push`, since it
cannot reach a student's machine.

**With the student kit (no toolchain).** Run `AdaptiveLearningSensors-Setup-<version>.exe` once as
an administrator, for all users, leave **Keep the sensors up to date automatically** ticked, and turn
Bluetooth on. The sensors then start at every sign-in with no window: the student opens the site,
signs in and clicks **Connect Headband**. The webcam stays off until the page starts a session.
**Stop sensors** and **Start sensors** are in the Start menu, and the logs are in
`%LOCALAPPDATA%\AdaptiveLearning\Sensors\logs`. Windows' camera access for desktop apps has to be on,
and a Windows N edition needs the Media Feature Pack. Only one person signed in at a time gets
sensors. A "just for me" install works too, but never updates itself. Building and publishing the kit
are below; why it works as it does is `docs/student-kit.md`.

**From a checkout (developers).** Each machine needs the headband setup above, plus the sidecar's
`.env` and camera extras once:

```powershell
cd C:\AdaptiveLearning\EEGResearch
copy .env.example .env
python -m venv .venv; .\.venv\Scripts\pip install -e ".[face,gaze]"
```

Then, with every feature on (headband EEG and heart rate, camera emotion, gaze):

```powershell
cd C:\AdaptiveLearning
.\start.ps1 -Hosted -Muse -Optics -Camera -Gaze `
    -BackendUrl https://<backend>.onrender.com `
    -FrontendOrigin https://<site>.pages.dev `
    -LearnerToken <the site's VITE_EEG_LOCAL_TOKEN>
```

Open the site in this machine's browser, sign in, and click **Connect Headband**. Drop `-Optics`,
`-Camera` or `-Gaze` to leave that feature off. `-LearnerToken` must equal the site's
`VITE_EEG_LOCAL_TOKEN` exactly, or every call from the page to the sidecar is refused. The machine
keeps its own `ADMIN_TOKEN`. `-Hosted` is refused without `-Muse`, because the simulator would push
made-up readings to real records.

### Building the student kit

Needs Python 3.14 on PATH, Visual Studio 2026 with the C++ workload, CMake, the libMuse SDK folder,
and Inno Setup 6.5 or later (`winget install --id JRSoftware.InnoSetup -e --scope user`). From the
repo root:

```powershell
.\EEGResearch\scripts\build_student_kit.ps1 -BackendUrl https://<backend>.onrender.com `
    -FrontendOrigin https://<site>.pages.dev -LearnerToken <the site's VITE_EEG_LOCAL_TOKEN> `
    -DownloadKeyFile E:\kit-keys\download.key -Version 0.2.0
```

It refuses the arguments for the reasons `-Hosted` refuses its own, then builds the bridge with
libMuse, freezes the sidecar from a fresh venv installed from `requirements-gaze.lock` and
`installer\requirements-kit.lock`, fetches and verifies both camera models, and checks every bundled
DLL. It then self-tests the result: the models on a real face, the bridge as a libMuse build loading
its C++ runtime from its own folder, and the updater verifying a signed feed. Two installers and
their SHA-256 files land in `EEGResearch\dist\kit`:

- `AdaptiveLearningSensors-Setup-<version>.exe`, with `kit.json` (site, token) and `update.json`
  (download key, feed). For a new computer.
- `AdaptiveLearningSensors-Update-<version>.exe`, the same code with neither file, checked for both
  secrets. What the self-updater installs, and what school IT pushes.

The self-test fails while `EEGResearch/src/kit/update_keys.py` holds no signing key; making the keys
is below.

- `-UpdateFeed canary` makes a kit that follows the canary feed: for a test computer.
- `-SkipInstaller` stops after the self-test.
- `-SignToolArgs` signs both exes and both installers, for example with Artifact Signing. An unsigned
  kit makes SmartScreen warn on first run.
- The site address and token are in `kit.json` beside the installed exe. To change them, build
  again, or edit that file as an administrator and restart the sensors.

### Publishing a kit update

Kits fetch a signed feed and the Update installer from the update gate, a Cloudflare Worker
(`EEGResearch/installer/update_gate`) in front of the private R2 bucket `adaptivelearning-kit`.

**Once.** On the Cloudflare account: two-factor sign-in on, and an API token with **R2 edit** only.
Then, from the repo root:

1. Two signing keys, each with its own passphrase: `everyday`, and `recovery`, which lives offline
   (a USB stick) and replaces a lost or leaked everyday key. Keep both off the repo and out of
   OneDrive. Each prints a public key to add to `PUBLIC_KEYS` in `update_keys.py`, through a PR.

   ```powershell
   EEGResearch\.venv\Scripts\python.exe EEGResearch\installer\kit_release.py newkey E:\kit-keys\everyday.pem
   ```

2. The download key, into a file beside the signing keys, so it is never on a command line or the
   clipboard. It goes in every Setup installer (`-DownloadKeyFile`), so changing it means
   reinstalling Setup everywhere.

   ```powershell
   EEGResearch\.venv\Scripts\python.exe EEGResearch\installer\kit_release.py newdownloadkey E:\kit-keys\download.key
   ```

3. The gate, signed in with `npx wrangler login`, then its key from that file:

   ```powershell
   cd EEGResearch\installer\update_gate; npx --yes wrangler@4.148.0 deploy
   Get-Content -Raw E:\kit-keys\download.key | npx --yes wrangler@4.148.0 secret put DOWNLOAD_KEY
   ```

4. The link secret, for the admin page's **Sensors kit** download: the gate's `LINK_SECRET`, and
   the backend's `KIT_LINK_SECRET` beside `KIT_GATE_URL` (the gate's address) on Render. Paste it
   into Render's form from the file and clear the clipboard after; unset, the page says so.

   ```powershell
   EEGResearch\.venv\Scripts\python.exe EEGResearch\installer\kit_release.py newlinksecret E:\kit-keys\link.secret
   ```

   ```powershell
   Get-Content -Raw E:\kit-keys\link.secret | npx --yes wrangler@4.148.0 secret put LINK_SECRET
   ```

**Each release.** Build it, then publish the Update installer to the canary feed, which only kits
built with `-UpdateFeed canary` follow:

```powershell
.\EEGResearch\scripts\publish_kit_update.ps1 -Version 0.2.1 -SigningKey E:\kit-keys\everyday.pem -DownloadKeyFile E:\kit-keys\download.key
```

It asks for the key's passphrase, and for the API token, read from the clipboard when you press
Enter and then removed from it and from clipboard history (Win+V). Once the canary computer has the version and works with a real headband,
promote it to a tenth of computers, then to all:

```powershell
.\EEGResearch\scripts\publish_kit_update.ps1 -Version 0.2.1 -SigningKey E:\kit-keys\everyday.pem -DownloadKeyFile E:\kit-keys\download.key -Promote -Rollout 10
```

```powershell
.\EEGResearch\scripts\publish_kit_update.ps1 -Version 0.2.1 -SigningKey E:\kit-keys\everyday.pem -DownloadKeyFile E:\kit-keys\download.key -Promote -Rollout 100
```

At 100%, add `-Setup -LinkSecretFile E:\kit-keys\link.secret` to also offer that version's Setup
installer on the admin page; it is read back through a signed link before the script reports success.

Each feed lists every installer the published feeds have named, and a computer installs only
when its own version is among them, since that is where a failed check rolls it back to. So
publish every version that reaches a computer, starting with 0.2.0 (`-Promote`): with nothing
published before it, it lists no way back and no kit installs it. A published installer is never
replaced; a fix is a new version.

**What a computer did** is in `C:\Program Files\AdaptiveLearning Sensors\updates\status.json`
(`state` says what this run did: `up_to_date`, `staged`, `installing`, `blocked`, `not_in_rollout`,
`no_rollback`, `not_set_up` or `failed`, or, finishing an earlier install, `checking`,
`rolling_back` or `repairing`, each with a `_waiting` form while someone is mid-lesson; `detail`
says why), beside `update.log` and the installers' own logs.

### For school IT

The Update installer carries no site address, token or key, and only updates a kit Setup installed.
An admin downloads the Setup installer from the site's admin console, **Sensors kit**.

- **First install, IT-managed:** `AdaptiveLearningSensors-Setup-<version>.exe /VERYSILENT
  /SUPPRESSMSGBOXES /NORESTART /ALLUSERS /MERGETASKS="!autoupdate"`, so no update task is
  registered.
- **Each update:** `AdaptiveLearningSensors-Update-<version>.exe /VERYSILENT /SUPPRESSMSGBOXES
  /NORESTART`. A silent install leaves the sensors stopped; they start at the next sign-in.
- **Self-updating computers** reach one host, `kit-updates.akashravi04.workers.dev`, over https;
  allow it through the proxy. A WinHTTP proxy (`netsh winhttp set proxy`) is used when set.

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
