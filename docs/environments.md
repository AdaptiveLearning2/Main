# Environments

Part of the AdaptiveLearning conventions; `CLAUDE.md` is the root and holds what binds here as much
as anywhere — the canary, the four numbered rules, and how to run the three suites. Read those first.

**Read this file when you are touching** `start.ps1` or `start.sh` or a launcher flag, `EEG_DEVICES`,
a venv, a dependency pin or a `requirements*.lock`, the `Dependency scan` or `Secret scan` job,
`dependabot.yml`, or a setting's name or default in any `.env`.

## Launcher flags

| Flag | Effect |
| --- | --- |
| `-Muse` | Real headband: builds the native bridge if needed, copies `libmuse.dll` next to the exe, sets `EEG_SOURCE=muse`. Without it, `sim`. |
| `-Camera` (`-CameraIndex N`) | Adds the webcam device and selects `INGEST_MODE=push`. |
| `-Gaze` | Landmark channel; implies `-Camera`. |
| `-NoEmotion` | FER+ off, and skips the 35 MB model fetch entirely. |
| `-Optics` (`-OpticsPreset 103N`) | Headband optical channels. Refused without `-Muse`. |
| `-LocalCalm` | `EEG_SPECTRUM_SOURCE=local`. Refused without `-Muse`, and refused outright by `start.sh`. |
| `-Hosted` (`-BackendUrl`, `-FrontendOrigin`, `-LearnerToken`) | Student machine for the hosted site: bridge and sidecar only, pushing to the backend with the site's shared token. Refused without `-Muse` (the simulator streams unpaired, so it would push made-up EEG) and outright by `start.sh`. Tests: `test_launcher_hosted.py`. The student kit (`EEGResearch/src/kit`, see `docs/signals.md`) sets the same values from `kit.json`, checked against the same table (`tests/launcher_cases.py`). |

**Every model-backed flag provisions its model at setup, not on the first frame of a lesson** — a
4 MB download in front of a student reads as a broken feature rather than an incomplete install.

**`-Optics` and `-LocalCalm` are refused without `-Muse` rather than promoting it, the way `-Gaze`
promotes `-Camera`.** The alternative to a headband is the simulator, which models no optical
channel and whose local calm would be a placeholder all session — so guessing produces a run that
looks exactly like the flag not working. `start.sh --local-calm` is refused outright because that
launcher always forces `EEG_SOURCE=sim`. An `-OpticsPreset` outside `1031`–`1036` is refused too:
the bridge falls back to `1035` on its own stderr, in its own window, so the session would record
on a rung nobody chose. `1031`/`1032` warn and proceed, since reproducing the bandwidth cliff needs
them. `-Camera -NoEmotion` without `-Gaze` is refused — the adapter would refuse it too, and a flag
combination is a better place to say so than a sidecar that starts and then will not connect.

**Gaze needs `pip install -e ".[face,gaze]"`** — MediaPipe is its own extra, deliberately: ~50 MB
and a second ML runtime for a channel that is off by default. **`face` pins
`opencv-contrib-python`, not `opencv-python`**; they install the same `cv2`, contrib being the
superset, so having both means whichever landed last owns the import and the `<5` cap is silently
defeated. One distribution, one version. The cap's stated reason (`cv2.data.haarcascades` and the
`CAP_PROP_*` constants) is behaviour no test covers, so `EEGResearch/scripts/verify_landmarks.py`
cross-checks the Haar cascade against the mesh on the same frames — a Haar miss alone is ambiguous,
a Haar miss where the mesh saw a face is not. The scripts check for the landmarker whenever gaze is
asked for, because `ensure_model` imports nothing heavy: without it, setup succeeds, writes
`FACE_GAZE_ENABLED=true`, and the channel dies on the first frame as `landmarker_unavailable`.

## The device registry is composed, never overwritten

**A plain run re-points the `default:` headband entry in `EEG_DEVICES`, not just the camera one.**
The registry wins over `EEG_SOURCE` for the device it names, so a `-Muse -Camera` run writes
`default:muse@8765,camera:face@N`, and a cleanup that stripped only the camera entry left
`default:muse@8765` beside `EEG_SOURCE=sim` — every later plain run started the sidecar looking for
a bridge that was not running. `Update-DeviceRegistry` (`update_device_registry` in `start.sh`)
rewrites a `default:` entry to what this run asked for, and only if one is present; other stations
survive, and the `-Camera` branch composes its entry through the same function.

**A named station already on the headband's bridge address refuses the run**, with nothing written.
The parser refuses two muse devices on one host:port, and the website backend drives the `default`
device on every lifecycle call (`eeg_client.DEFAULT_DEVICE_ID`) — so dropping the `default:` entry
instead trades a sidecar that will not start for a stack that starts clean and 404s on Connect.
Only the user can say whether that station moves or goes. `sim` entries are exempt.

Order matters and is pinned by `EEGResearch/tests/test_launcher_device_registry.py`: the check
(`-DryRun` / `check`) runs **before any key in either `.env` is written**, so a refusal leaves both
files untouched; the composed value is applied **after** camera model provisioning, since applying
it early left a failed download with a camera entry in the registry and `FACE_ENABLED` still false.
The run summary reads the key back rather than rebuilding it from two variables.

**Every `FACE_*` key and `INGEST_MODE` is written on *both* branches, from the flag.**
`FACE_EMOTION_ENABLED` defaults to `true` in config, so leaving it unwritten made emotion silently
on whenever the camera was, and put a third of the camera's configuration in a Python default
rather than in the `.env` a reader checks. `INGEST_MODE` likewise: a stale `push` from a camera run
would disable the poller on a later headband-only run.

## Two `start.ps1` rules that cost whole runs

**Guard every read of a `.env` with `Test-Path`, and write one only through `Set-EnvKey`.**
`Set-EnvKey` returns silently when the file is missing, so nothing before the read notices, and
`Select-String -Path` on a missing file is a *terminating* error under `start.ps1`'s
`$ErrorActionPreference`. Guard the *match* too: `.Matches[0].Groups[1]` on an absent key indexes a
null array and fails the same way one step later. `start.sh` carries the same guard. 5.1's
`Set-Content` writes ANSI, which the sidecar cannot decode once an install path is non-ASCII.

**Redirect a native command's stderr only through `Invoke-Quiet`.** PowerShell 5.1 wraps each
stderr line from an exe in an ErrorRecord, which `$ErrorActionPreference = "Stop"` makes
terminating — so `python -c "import cv2" 2>$null` killed the script at the failing import, before
the block that exists to explain it, as a bare `NativeCommandError` naming neither module nor fix.
`Invoke-Quiet { ... }` runs the command under a function-local `Continue`, so the caller keeps
`Stop`; a bare `2>$null` anywhere else is the bug. Where the stderr *is* the diagnosis, silence it
inside Python instead (`import sys, os; sys.stderr = open(os.devnull, 'w'); import cv2`) and probe
**one module per call**, so the error can say which import failed.

## Three venvs, and `start.ps1` uses two

`EEGResearch/.venv` is the sidecar's; `Website/AdaptiveLearning/backend/.venv` is the website
backend's — that is what `uvicorn main:app` runs under, so it is where `backend/requirements.txt`
has to be installed. A third `.venv` at the repo root, with a different OpenCV, is what `pytest`
runs under. `pip install -e ".[face,gaze]"` has to run *from* `EEGResearch`, or pip resolves `.` to
the repo root and reports "neither setup.py nor pyproject.toml found".

**A package present in the root venv says nothing about the backend's.** The suite passing is not
evidence the app can import something: `anthropic` was in the root venv and absent from
`backend/.venv` for the whole Claude migration, so every test passed while a live
`LLM_PROVIDER=claude` run would have died on the first question a student asked — `llm_client`
imports it lazily, so not at boot. Install a new runtime dependency into `backend/.venv` in the
same change that pins it.

All three venvs are on Python 3.14.7, **and so is CI** — `ci.yml`'s six `setup-python` pins say
`3.14`, the minor rather than the patch, because `setup-python` fails outright on an exact version
the runner image does not have. Keep them together, or CI tests an interpreter nobody runs.
`EEGResearch/requirements*.lock` are generated under the same version, and its header records which one.

**Check the wheels on both platforms, not just this one.** Everything an install resolves —
**transitive as well as direct** — ships a `cp314` wheel for each platform *it is resolved on*
(`win_amd64` here, `manylinux_x86_64` in CI), or is version-agnostic. Scoped that way on purpose: a
package only one platform resolves needs a wheel only there, and **`uvloop` publishes none for
Windows at all**, which is fine because Windows never installs it. What is *not* fine is that
`uvicorn[standard]` pulls it on Linux, so a check run here passes without ever looking at a package
the ubuntu jobs do install — and the example is transitive rather than direct, which is why the rule
has to say so.

**`EEGResearch/requirements*.lock` are resolved on Windows, and uvloop's absence from them is how
you can tell.** So installing them on Linux gives an environment `pyproject` would not — missing
`uvloop` and anything else platform-gated. That is a live trap rather than a curiosity, because the
obvious fix for the nested workflow below never running is to move it to the repository root, and
that would run exactly this lock on ubuntu. **Regenerate them on the platform that will install
them** — that is the whole of the available fix. A single lock covering both platforms is not one:
`pip-compile` has no `--universal`, in 7.6.1 or any version, so that route is a toolchain change
rather than a flag. **They carry hashes**: regenerate with `--generate-hashes`, and the dev lock with
`--allow-unsafe` too, or its unpinned `pip`/`setuptools` make a hashed install refuse.
One pre-existing gap: only the dev lock carries `setuptools`, and at a version without `pkg_resources`, so `import rppg`
/ `import heartpy` fail on a missing `pkg_resources` against a persistent venv. (`keras`/`jax` load fine once
`KERAS_BACKEND` is set the way `rppg/models.py` already sets it at import.) The `open-rppg`
measurements were always done in a throwaway `pip install --target ... "setuptools<81"` env.

## The two scanners, and what they are allowed to be red about

**`Dependency scan` is blocking.** A CVE is not a style backlog to burn down: every
step names the advisory and the fixed version, so a red run says what to do. Tuning the threshold
until it passes is tuning the detector to the disease.

It audits **three Python sets, because they are three different installs** — the backend's pinned
`requirements-dev.txt`, the sidecar's `pyproject`, and the five committed `.lock` files (the kit's is `installer/`). The first
two are audited *as installed*, not as files: `requirements.txt` pins direct dependencies only, so
auditing the file alone would miss the transitive tree, which is where `starlette`, `idna` and
`urllib3` live. The locks are audited `--no-deps`, since the point of a lock is that it already
names every version, and `--disable-pip`: they are hashed and Windows-resolved, so letting
pip-audit install one on the ubuntu runner fails on Linux-only extras (see *Three venvs*).

`npm audit` runs at `--audit-level=high`: npm reports transitive dev-only findings in build tooling
that never reaches a browser, and a job red for those is one nobody reads by the time a real one
lands. **Check `--omit=dev` before judging severity** — that is what separates a shipped advisory
from one in a bundler.

**`Secret scan` runs `gitleaks` over the whole history**, not the diff: a key committed and then
removed is still leaked, because the commit is what GitHub serves. It installs the pinned binary
rather than `gitleaks/gitleaks-action`, which requires a `GITLEAKS_LICENSE` for org-owned
repositories — the action would go red on a missing secret rather than on a finding, which is the
worst way for a security job to fail. `--redact`, so a finding does not reprint the secret into a
build log and leak it to a wider audience than the commit did.

**A made-up credential in a test fails it too, and a later commit cannot clear it.** `generic-api-key`
fires on a credential-ish name (`token`, `key`, `auth`, `secret`…) assigned a high-entropy literal, so
`token = "eyJ…"` is a finding. Use a low-entropy fake or build the value at runtime. One already pushed to
an unmerged branch is rewritten out of it (amend, force-push with a lease), not fingerprinted: a fingerprint
names the commit SHA, and "Rebase and merge" gives main a new SHA, which turns main's scan red.

**`dependabot.yml` covers four ecosystems** and deliberately does **not** manage the `.lock` files:
Dependabot does not regenerate `pip-compile` output, so a lock left behind by a bump it opens stays
stale silently. That is why the scan audits the locks separately.

## Configuration

**Read numeric settings through `env_config.env_number(name, default, cast, minimum=...)`, never
`int(os.getenv(…))`**, in every backend module — one copy, in its own module so `main`'s imports can
use it without a cycle. These are read at import, so a typo would otherwise take every endpoint down
over a tuning knob for one optional feature. It falls back on unparseable and non-finite values
(`inf` passes a `minimum` check, `nan` fails every comparison, and both break call sites in ways
that look like the feature being off) and clamps below the floor. **Give every one a floor:** a
number is not automatically a usable setting. The sidecar's tuning settings take the same tolerant
treatment in `config.py` — a validator warns and falls back rather than refusing the boot. Its secrets
do not: a `replace-me` token, two equal tokens, or push over plain `http://` to another host refuse it.

**Backend.** `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (required), `AUTH_CHECK_TIMEOUT`, `BACKEND_PORT`, `EEG_API_URL`,
`EEG_API_TOKEN`, `EEG_ADMIN_TOKEN`, `EEG_POLL_HZ`, `INGEST_MODE`, `INGEST_MAX_BATCH` /
`INGEST_RATE_LIMIT` / `INGEST_RATE_WINDOW` / `INGEST_MAX_ROWS_PER_MINUTE` (per student and channel), `SESSION_ABANDONED_AFTER_HOURS` /
`STALE_SWEEP_INTERVAL_SECONDS` (the second is `0` to disable the sweep and its chart catch-up), `STALE_SWEEP_FIRST_DELAY_SECONDS`
(60 in production, else 0; a cold start serves requests before the first sweep), `QUESTIONS_CACHE_TTL`,
`QUESTION_QUEUE_SIZE`, `PAIRING_IDLE_SECONDS` (120; a headband pairing its pairer's page stopped polling is released),
`CLASS_JOIN_MISSES_PER_ADDRESS` (1000 wrong class codes an hour), the `ENV` / `ALLOWED_ORIGINS` / `MAX_BODY_BYTES` / `INGEST_MAX_SAMPLE_BYTES` /
`PUBLIC_*_RATE_*` / `TRUSTED_PROXY_HOPS` group under *The network edge* in `CLAUDE.md`, the `STRATEGY_*` / `CHART_SUMMARY_*` groups under *The two model-backed panels* in `docs/reporting.md`,
the `LLM_PROVIDER` / `CLAUDE_*` / `GENERATION_*` groups in `docs/question-generation.md`, and the `SOLVE_*` group in
`docs/solvers.md`.

`QUESTIONS_CACHE_TTL` (30 s) fronts `GET /api/questions`, bounded at 32 entries **and** by `_QUESTIONS_MAX`
on `limit`: the entry count bounds how many, the clamp how big, and the key is built from the clamped,
normalised values that decide the query. The ingest bounds matter because the sidecar posts with the *student's* token:
that endpoint is a trust boundary, and neither the session check nor the consent check bounds volume.

**Frontend.** `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, `VITE_API_URL`, `VITE_EEG_LOCAL_URL`, `VITE_EEG_DEBUG`,
`VITE_EEG_LOCAL_TOKEN`. A build refuses to run without the first and third; the CSP is made from the URLs.

**EEGResearch** reads `.env` through `src/app/config.py`: `API_TOKEN` and `ADMIN_TOKEN` required,
`EEG_SOURCE` picks sim vs muse, `EEG_DEVICES` (`station1:muse@8765,...`) drives the multi-headband
registry, `PUSH_ENABLED` / `BACKEND_URL` drive the push client, `ALLOWED_ORIGINS` must name the
**frontend** origin (getting it wrong fails every local call on CORS while the sidecar looks
healthy), `EEG_SIM_OPTICS`, `EEG_SPECTRUM_SOURCE`, `EEG_SPECTRUM_POISON_SECONDS`,
`EEG_CALM_CENTRE_ON_ARM`, `FACE_*`, `SIDECAR_DOCS` (off: no `/docs` or `/openapi.json`, since
`APP_ENV` cannot tell a student's laptop apart), and `EEG_SAMPLE_HZ`, which warns under push when
it would pass half the backend's `INGEST_MAX_ROWS_PER_MINUTE` default.

**The native bridge reads its own env directly, not through `config.py`**: `MUSE_BRIDGE_PORT`
(8765), `MUSE_ENABLE_OPTICS` (off), `MUSE_OPTICS_PRESET` (`1035`), `MUSE_AUTO_RECONNECT`, `MUSE_LIVENESS_TIMEOUT_MS`
(8000), `MUSE_SCAN_IDLE_MS` (120000). **Set them with the launcher flag, never by editing a `.env`** —
the bridge is a C++ process calling `getenv`, so a `MUSE_ENABLE_OPTICS` line in `EEGResearch/.env`
is read by nothing. That is the version of this mistake that looks like it worked.
