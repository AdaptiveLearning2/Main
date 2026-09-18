# Project conventions

AdaptiveLearning is an EEG- and camera-assisted adaptive maths platform: students answer
LLM-generated questions while a Muse headband and a webcam feed cognitive and facial signals into
per-session records; teachers and parents read those back as live views and weekly reports.

## How to edit this file

It is loaded in full into every session, which is the only reason it earns its size. It reached
5,000 lines once by accumulating incident reports; these rules are what stop that happening again.

- **One entry = one rule, its reason, and where it is enforced.** A third paragraph means it
  belongs in a doc beside the code, cited from here.
- **Write the rule, not the incident.** No date stamps, no PR numbers, no account of what the rule
  used to be. A correction *replaces* the entry it corrects and is never appended beside it.
- **Measurements live in `EEGResearch/tests/fixtures/*.md` and `EEGResearch/docs/*.md`.** Here: the
  verdict and the pointer. Keep a date only on a measurement, or on a decision deferred to a
  future capture.
- **File it under one of the six parts below**, not at the end of the file.
- **Re-check a count or a path before trusting it.** This file has been wrong about `main.py`'s
  size and about seven script paths.
- **Ceiling: 2,500 lines.** Past it, a new entry means an older one is merged, cut, or moved to a
  doc. A number, because "keep entries short" has already failed once.

The six parts: **Orientation**, **Database**, **Signals**, **Privacy**, **Reporting and UI**,
**Question generation**.

---

# Orientation

## Layout

| Path | What it is |
| --- | --- |
| `Website/AdaptiveLearning/backend` | FastAPI app (`main.py`, ~9k lines) — the product API on port 8000. Also the `LLM_*_generation.py` question generators and `LLM_topic_decider.py`, which reach a model through `llm_client.py` — a local Ollama by default, the Claude API when `LLM_PROVIDER` says so. |
| `Website/AdaptiveLearning/frontend` | React 19 + Vite + Tailwind SPA on port 5173. Routed by role: `src/pages/{student,teacher,parent,auth}`, one layout each. `src/lib/api.js` wraps the backend; `src/lib/supabase.js` holds the anon client. |
| `EEGResearch` | Separate FastAPI sidecar on port 8001 (`src/app`), packaged as `eeg-learning-platform`. Owns headband access and signal derivation; the website backend talks to it over HTTP only, via `backend/eeg_client.py`. |
| `EEGResearch/native_bridge` | C++ bridge to the libMuse SDK, TCP on 8765. Windows-only (`winsock2`), and the interesting half is behind `ENABLE_LIBMUSE`. |
| `EEGResearch/scripts` | The sidecar's capture, replay and run scripts. **Not** the root `scripts/`, which also exists and holds the database and load-test tooling — cite both by full path. |
| `FacialRecg` | Vendored rPPG / facial-recognition reference code. |
| `supabase/migrations` | The schema. Timestamp-prefixed, applied in order. |

Two backends, deliberately: the website backend never reads a headband directly, and every caller
gates on `eeg_client.is_alive()` first, so the whole EEG stack is optional at runtime. Don't add a
hard dependency on port 8001 to a path that must work without hardware.

## Canary: is this session still working properly?

Long sessions degrade before they fail, and the agent cannot feel it from the inside. So before
the first edit of every task and every review round, write one line to the user, built from
fresh tool output and not from memory:

```
CANARY: <branch> <short hash of HEAD> | tree: clean|dirty(<n> files) | last suites: sidecar <n> / backend <n> / frontend <n>
```

`git status --short` and `git log --oneline -1` supply the first two; the suite counts are the
last run *in this session*, or `none` if there has not been one. **Record the total, not the pass
count**: a run reporting `712 passed, 1 failed` is a 713-test suite, and the one failure is usually
the documented real-timer flake. Written as 712 in a handoff, a genuinely deleted test later reads
as agreeing with the record. Then check it against the previous canary. **Any of these means stop,
re-read this section and `git status`, and say so before touching a file:**

- The tree is dirty and you cannot name, from the conversation, what each modified file holds. Do
  not build on an unexplained diff; restore it from HEAD by copy and report it.
- A suite count went down, or a count is quoted that no tool call in this session produced.
- The working directory reported by the harness is not the repo root. (`cd` inside a compound
  command moves it for later calls; the symptom is `vitest` finding no config, or `ls` failing
  on a path that exists.)
- You are about to edit a file whose relevant region you have not read in this context, or to
  restore a file with `git checkout --` (use a copy; see the mutation-check rule).
- You are about to assert on source text where the behaviour is testable, or to skip the
  mutation check on a new test, or to run it before committing.
- A heredoc append failed and you are about to retry it the same way (use the Write tool).
- You cannot restate, without looking, the three standing rules: fusion asymmetry is
  untouchable; held / rejected / low are three states and zeros are never written for an
  absence; `stress` is `1 − calm`.

A canary that cannot be written from tool output is itself the signal. Ask for `/compact` or a
fresh session rather than continuing on inference; the cost of a wrong edit here is a wrong
number on a child's record, not a retry.

## Four rules the rest of this file keeps citing

State them once here; sections below reference them rather than re-deriving them.

1. **Three states, never two.** "No data", "not requested" and "not retrieved" are different
   facts, and so are held / rejected / low, a revoked channel and an unread one, a failed read and
   a quiet week. Any surface that renders an absence has to consult all of them.
2. **An absence is never a zero.** A disconnected headband reports zeroed scores; averages include
   zeros and exclude nulls, so a zero written for an absence becomes a measurement. `pct || null`
   and `or 0` are where this enters.
3. **Consent and access fail closed; reporting fails open.** A consent or role check that degrades
   to permissive records data against a refusal. A dashboard that degrades to empty is fine — but
   it must say `retrieved: false` rather than claim nothing happened.
4. **A fixture written from the same misreading as the code cannot fail against it.** Build test
   fixtures from what the endpoint or table actually returns, and assert on the *request* (which
   table was read, which filter was applied), not only on the payload.

## Running and testing

Whole stack, Windows (Ollama, EEG sidecar, backend, frontend, each in its own window):

```bash
./start.ps1
```

`start.sh` is the mac equivalent and is kept at flag parity; per-machine setup lives in
`DEVELOPER_SETUP_{MAC,WINDOWS}.md`. Individually, from each directory:

```bash
uvicorn main:app --reload --port 8000
```

```bash
uvicorn src.app.main:app --host 127.0.0.1 --port 8001 --reload
```

```bash
npm run dev
```

### Launcher flags

| Flag | Effect |
| --- | --- |
| `-Muse` | Real headband: builds the native bridge if needed, copies `libmuse.dll` next to the exe, sets `EEG_SOURCE=muse`. Without it, `sim`. |
| `-Camera` (`-CameraIndex N`) | Adds the webcam device and selects `INGEST_MODE=push`. |
| `-Gaze` | Landmark channel; implies `-Camera`. |
| `-NoEmotion` | FER+ off, and skips the 35 MB model fetch entirely. |
| `-Optics` (`-OpticsPreset 103N`) | Headband optical channels. Refused without `-Muse`. |
| `-LocalCalm` | `EEG_SPECTRUM_SOURCE=local`. Refused without `-Muse`, and refused outright by `start.sh`. |

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

### The device registry is composed, never overwritten

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

### Two `start.ps1` rules that cost whole runs

**Guard every read of a `.env` with `Test-Path`.** `Set-EnvKey` returns silently when the file is
missing, so nothing before the read notices, and `Select-String -Path` on a missing file is a
*terminating* error under this file's `$ErrorActionPreference` — a first-ever `-Camera` run on a
fresh checkout aborted the launcher before anything started. Guard the *match* too:
`.Matches[0].Groups[1]` on an absent key indexes a null array and fails the same way one step later.
`start.sh` carries the same guard.

**Never redirect a native command's stderr.** PowerShell 5.1 wraps each stderr line from an exe in
an ErrorRecord, which `$ErrorActionPreference = "Stop"` makes terminating — so
`python -c "import cv2" 2>$null` killed the script at the failing import, before the block that
exists to explain it, as a bare `NativeCommandError` naming neither module nor fix. Silence it
inside Python instead (`import sys, os; sys.stderr = open(os.devnull, 'w'); import cv2`) and probe
**one module per call**, so the error can say which import failed.

### Three venvs, and `start.ps1` uses two

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

All three venvs are on Python 3.14.7; every direct dependency ships a `cp314`/`win_amd64` or
version-agnostic wheel. One pre-existing gap: none has ever carried `setuptools`, so `import rppg`
/ `import heartpy` fail on a missing `pkg_resources` against a persistent venv. (`keras`/`jax` load fine once
`KERAS_BACKEND` is set the way `rppg/models.py` already sets it at import.) The `open-rppg`
measurements were always done in a throwaway `pip install --target ... "setuptools<81"` env.

### The suites

CI (`.github/workflows/ci.yml`) runs **six** jobs on PRs and pushes to `main`: `EEGResearch tests`,
`Native bridge build`, `Website backend tests`, `Database grants`, `Database migrations`,
`Frontend tests, build & lint`. Counted by name, so a seventh on the PR page is new or undocumented
rather than a stale number. (The `Supabase Preview` check is the integration's, not CI's, and is
always skipped.)

Locally, **all three from the repo root**, each under its own venv and with the env it needs:

```bash
EEG_SOURCE=sim API_TOKEN=t ADMIN_TOKEN=a EEGResearch/.venv/Scripts/python.exe -m pytest EEGResearch/tests -q
```

```bash
SUPABASE_URL=http://localhost:54321 SUPABASE_SERVICE_ROLE_KEY=x .venv/Scripts/python.exe -m pytest Website/AdaptiveLearning/backend/tests -q
```

```bash
cd Website/AdaptiveLearning/frontend && npm test
```

**Not from `EEGResearch`**, where a `tests/` directory does exist: `Settings` loads `.env` relative
to the cwd, so a locally edited `EEGResearch/.env` (e.g. `FACE_EMOTION_ENABLED=false` left from a
camera-off run) overrides field defaults and produces a dozen `test_face_*` failures that read as a
code regression. There is no `tests/` at the repo root, so `pytest tests/` from here reports "no
tests ran" and reads as a clean run.

Backend tests need `SUPABASE_URL` **URL-shaped** — the client validates it at import, so a
placeholder like `x` fails collection with "Invalid URL" — and any non-empty
`SUPABASE_SERVICE_ROLE_KEY`. They never reach a real database. EEGResearch tests need
`EEG_SOURCE=sim`, `API_TOKEN`, `ADMIN_TOKEN`.

The native bridge is compile-checked on `windows-latest` with `ENABLE_LIBMUSE=OFF`, which covers
syntax and signatures but **not** the packet handling inside the guards. The SDK is vendored (and
gitignored) at `EEGResearch/libmuse_windows_8.0.5`, so a real build is worth running on any change
inside an `ENABLE_LIBMUSE` guard:

```bash
cmake -S . -B build_on -DENABLE_LIBMUSE=ON -DLIBMUSE_SDK_DIR=../libmuse_windows_8.0.5 && cmake --build build_on --config Release
```

It compiles enum values, SDK signatures and the guarded packet handling. It still proves nothing
about a real headband.

`npm run lint` is non-blocking against a backlog of **14** pre-existing errors (7
`react-refresh/only-export-components`, 5 `no-undef` on `process`/`global` in tests, 1 `no-empty`,
1 `react-hooks/rules-of-hooks`) — none of them `no-unused-vars` or
`react-hooks/set-state-in-effect`. Don't add to it, and don't make it blocking until it is gone.
**The count is the check, so keep it current**: against a stale 11, a reviewer concludes the change
in front of them added three errors it did not. `coverage/` is ignored by the config for the same
reason — linted, the number depended on whether coverage had ever been run on that checkout.

Dependencies are pinned: `backend/requirements.txt` (runtime, direct deps only, cross-platform by
design — no `pip freeze`), `requirements-dev.txt` adds pytest. EEGResearch uses `pyproject.toml`
plus `requirements*.lock`.

### Two test-writing rules that came from real flakes

**Assert an ordering, not a duration, when a test synchronises on a thread.** Windows' default
timer resolution is ~15.6 ms, so a `threading.Timer(0.15)` fires a hair early and an exact-boundary
`monotonic() - started >= 0.15` fails *against a correct implementation* — measured at 2 failures in
5 runs on a clean checkout, and 3 in 5 with an unrelated change present. Identical rates, but the
second reads as a regression you just caused. The claim was never about duration: it was that the
call could not proceed until the slot was free, which is an ordering. The releasing thread records
`monotonic()` as it lets go and the test asserts the call returned *after* that — two readings of
one clock with a real happens-before, exact at any resolution. CI is Linux and never failed the old
form, so the whole cost landed on local runs. Same rule wherever a test waits on something it does
not drive (`test_time_spent_queueing_comes_out_of_the_budget_it_was_promised`).

**A daemon thread that prints must be joined before the process exits.** A print landing during
interpreter shutdown, while the stdout `BufferedWriter` lock is held, is a fatal
`_enter_buffered_busy` abort — exit code 134 *after* every test passed, which reads as unrelated
flake. `eeg_poller.stop_all()` is the join, called from `main._lifespan` and from an autouse fixture
in `backend/tests/conftest.py`. Loops in such threads wait on the stop event rather than
`time.sleep`, so `stop()` is not a poll interval away from taking effect.

### Frontend tests mock through `src/test/`, not a hand-rolled `vi.fn()`

`src/test/mocks/apiFetch.js` and `src/test/mocks/supabase.js` are the shared doubles, reached by
pointing the factory at the file so the mocked module and the handle driving it are one instance:

```bash
vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
```

**`apiFetch`'s double is a router, and an unmatched path throws.** Most pages fetch two to four
endpoints in parallel on mount and the interesting tests need *one* to fail; `mockResolvedValueOnce`
chains express that by the order `Promise.all` happens to start them in, so such a test passes for a
reason unrelated to what it claims and breaks when a page adds a fetch. `mockApi({...})` registers
the happy path, `overrideApi(path, fn)` layers one failure over it. Throwing on an unrouted path is
load-bearing: a silent `undefined` reaches a page as a successful read of nothing, which is the
state most of this suite exists to tell apart from a failure. **Method-scoped routes are tried
before methodless ones**, whatever order they were written in — first-match-wins alone made
`{'/api/x': …, 'PUT /api/x': …}` answer the write with the read. Reset with `resetApi()`, never
`mockReset()`, which drops the implementation and every route with it.

Mocking `lib/supabase` as a *module* also sidesteps its import-time throw on missing
`VITE_SUPABASE_*`, the normal state under `vitest` (CI supplies those to the build step only).
`fireAuthEvent(event, session)` reaches the properties that only exist post-mount: the `SIGNED_OUT`
cleanup an expired refresh token triggers with nobody calling `signOut()`, and the
`TOKEN_REFRESHED` handling that must not await anything reading the session.

**`lib/api.test.js` is the one place `apiFetch` runs for real**, with only `fetch` and
`lib/supabase` mocked. Every other test replaces it wholesale, so nothing otherwise exercises the
URL it builds, whether the bearer is attached, or how a non-2xx becomes an `Error` carrying
`.status`. Fixtures live in `src/test/fixtures/` as **builders**, not constants
(`buildWeeklyReport`, `buildConsentState`, `buildChartArchive`, …): every interesting case is one
field off the happy path, and a test that restates a whole payload to move one field tends to move
two. `CHANNEL_REASONS` there is the `offLabel` four-state matrix, named for the state each input
must produce rather than for its field values.

**`asyncUtilTimeout` is 5000 in `src/test/setup.js`, not Testing Library's 1000.** That default is
chosen for pure components; a query for something that legitimately arrives on the *second* 5 s poll
races a budget unrelated to what it waits for, and only passed because the machine was idle. Under
the full run the same query misses by a few hundred milliseconds. Raising it costs nothing on a
passing assertion, since `waitFor` returns as soon as the condition holds.

**A timeout does not fix an assertion anchored to elapsed time.** One contact-hint test slept 6 s
and asserted "exactly one 5 s poll has landed" — true only if the interval was in the right part of
its cycle; land two and the hint is correctly on screen and the assertion fails against working
code. It now waits for the *read count* to advance by one, which gives it a whole poll of slack.

**`AdaptiveReconnect.test.jsx` runs on real timers, and every fake-clock version hung.** The pairing
sequence is a chain of 1–1.5 s waits noticed by a 5 s poll; under `vi.useFakeTimers()` — with or
without `shouldAdvanceTime` — `await act(async () => advanceTimersByTimeAsync(…))` never resolved.
Each of those tests costs 10–20 real seconds and declares a 60 s timeout. `Overview.test.jsx`'s
fake-clock pattern works for a 300 ms debounce and did not survive this component.

**Clear persisted view state in `beforeEach`.** `viewPrefs.js` and `al_sidebar_collapsed:<scope>`
write to `localStorage`, which jsdom keeps for the whole file — so every test declared *after* one
that flips a switch renders with it already flipped, silently, which reads as one of the later tests
being broken. `StudentReport.test.jsx` and `layoutAccessibility.test.jsx` are where that bites —
the latter's account describe collapses the sidebar in its **first** test, so the two after it fail without the
clear. `clearViewPrefs()` is the guard, and it needs a test standing **downstream of the
leak** to have teeth: with the switching test last in the file, removing the guard breaks nothing.

## Configuration

**Read numeric settings through `_env_number(name, default, cast, minimum=...)`, never
`int(os.getenv(…))`.** These are read at import, so a typo would otherwise take every endpoint down
over a tuning knob for one optional feature. It falls back on unparseable and non-finite values
(`inf` passes a `minimum` check, `nan` fails every comparison, and both break call sites in ways
that look like the feature being off) and clamps below the floor. **Give every one a floor:** a
number is not automatically a usable setting. The sidecar's boot settings take the same tolerant
treatment in `config.py` — a validator warns and falls back rather than refusing the boot.

**Backend.** `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (required), `BACKEND_PORT`, `EEG_API_URL`,
`EEG_API_TOKEN`, `EEG_ADMIN_TOKEN`, `EEG_POLL_HZ`, `INGEST_MODE`, `INGEST_MAX_BATCH` /
`INGEST_RATE_LIMIT` / `INGEST_RATE_WINDOW`, `SESSION_ABANDONED_AFTER_HOURS` /
`STALE_SWEEP_INTERVAL_SECONDS` (the second is `0` to disable the sweep), `QUESTIONS_CACHE_TTL`,
`QUESTION_QUEUE_SIZE`, the `ENV` / `ALLOWED_ORIGINS` / `MAX_BODY_BYTES` / `INGEST_MAX_SAMPLE_BYTES` group under
*The network edge*, and the `LLM_PROVIDER` / `CLAUDE_*` / `GENERATION_*` / `STRATEGY_*` / `CHART_SUMMARY_*` /
`SOLVE_*` groups described under *Question generation*.

`QUESTIONS_CACHE_TTL` (30 s) fronts `GET /api/questions` and is bounded at 256 entries, so a sweep
of distinct `limit`/`subject`/`difficulty` combinations from that unauthenticated endpoint cannot
grow it unboundedly. The ingest bounds matter because the sidecar posts with the *student's* token:
that endpoint is a trust boundary, and neither the session check nor the consent check bounds volume.

**Frontend.** `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, `VITE_API_URL`, `VITE_EEG_DEBUG`,
`VITE_EEG_LOCAL_TOKEN`.

**EEGResearch** reads `.env` through `src/app/config.py`: `API_TOKEN` and `ADMIN_TOKEN` required,
`EEG_SOURCE` picks sim vs muse, `EEG_DEVICES` (`station1:muse@8765,...`) drives the multi-headband
registry, `PUSH_ENABLED` / `BACKEND_URL` drive the push client, `ALLOWED_ORIGINS` must name the
**frontend** origin (getting it wrong fails every local call on CORS while the sidecar looks
healthy), `EEG_SIM_OPTICS`, `EEG_SPECTRUM_SOURCE`, `EEG_SPECTRUM_POISON_SECONDS`,
`EEG_CALM_CENTRE_ON_ARM`, `FACE_*`.

**The native bridge reads its own env directly, not through `config.py`**: `MUSE_BRIDGE_PORT`
(8765), `MUSE_ENABLE_OPTICS` (off), `MUSE_OPTICS_PRESET` (`1035`), `MUSE_AUTO_RECONNECT`,
`MUSE_LIVENESS_TIMEOUT_MS` (8000). **Set them with the launcher flag, never by editing a `.env`** —
the bridge is a C++ process calling `getenv`, so a `MUSE_ENABLE_OPTICS` line in `EEGResearch/.env`
is read by nothing. That is the version of this mistake that looks like it worked.
---

# Database

## Postgres functions are world-executable by default

**Every `CREATE FUNCTION` in `public` is EXECUTE-able by every logged-in user unless you explicitly
revoke it, and the usual boilerplate revoke does not catch it.** Two things stack up: Postgres
grants `EXECUTE` on new functions to `PUBLIC` automatically (unlike tables), and Supabase
additionally ships `ALTER DEFAULT PRIVILEGES` granting `EXECUTE` to `anon` and `authenticated` **by
name**. Explicit grants to a named role survive a revoke aimed at the `PUBLIC` pseudo-role, so
`REVOKE ALL ... FROM PUBLIC` alone leaves both roles holding `EXECUTE`. Verified against
`pg_proc.proacl`: without the named revokes the ACL comes back as
`{postgres=X/postgres,anon=X/postgres,authenticated=X/postgres,...}`.

`scripts/check_function_grants.py` enforces this inside the `Database grants` CI job (which runs
both grant scripts). It matches by function **name**, not signature, so it catches a forgotten
revoke block but not a migration that adds an overload and revokes only the old signature — review
still has to. Deliberate exceptions go in its `ALLOWLIST` with a reason.

**Don't try to fix this with `ALTER DEFAULT PRIVILEGES`.** Making `EXECUTE` deny-by-default is the
obvious move and does not work: the `pg_default_acl` row records correctly and the named grants do
disappear from new functions, but Postgres's `PUBLIC` grant (`=X`) survives and both roles can still
execute. Reproduced with three throwaway functions, grantees combined and separated, and no event
trigger re-granting. A default that silently fails to deny is worse than none.

## The same trap applies to tables — `GRANT` does not narrow, only `REVOKE` does

A new table arrives as `anon=arwdDxtm,authenticated=arwdDxtm` before your migration grants anything,
so adding `GRANT SELECT` on top is a no-op that reads like a restriction.

RLS covers most of it — with no policy for a command, that command is denied — but **RLS does not
filter `TRUNCATE`**. As `anon`, `INSERT` is blocked and `TRUNCATE` succeeds. PostgREST does not
expose `TRUNCATE`, so the anon key in the frontend bundle is not a path to it; it needs a direct
Postgres connection. "Not reachable from the client we ship" is a weaker property than the one a
narrow grant appears to claim. So revoke before granting:

```sql
REVOKE ALL ON TABLE "public"."my_table" FROM "anon";
REVOKE ALL ON TABLE "public"."my_table" FROM "authenticated";
GRANT SELECT ON TABLE "public"."my_table" TO "authenticated";
GRANT ALL ON TABLE "public"."my_table" TO "service_role";
```

Sequences need the same treatment. `scripts/check_table_grants.py` enforces it in the same CI job.

**What to grant back is per-table judgement, and the lint deliberately does not check it — so a
judgement call can go stale as the write path moves, and nothing catches that.** `math_topics` and
`questions` have `USING (true)` public-read policies, so `anon` keeps `SELECT` on those two and
nothing else anywhere. Every other backend-written table gets `SELECT` for `authenticated` and
nothing more.

`sessions` was one that had been missed: it kept `authenticated=arwd` next to a `FOR ALL` own
policy, so a student could rewrite any column of their own sessions through PostgREST —
`started_at`/`ended_at` drive the rollup's day bucketing and the expiry cutoff, and a DELETE there
cascades all three signal tables. **RLS narrows which rows a command touches, never which commands
exist**, so an own-row policy is not a substitute for withholding the grant.

`class_memberships`, `classes`, `profiles`, `parent_child_links`, `user_math_performance`,
`user_stats` and `session_answers` were the next seven. This file used to say `Adaptive.jsx` upserts
`user_math_performance` directly through PostgREST; that was true once, the write moved server-side,
and this file was not updated. A repo-wide grep of `frontend/src` for
`.insert(`/`.update(`/`.upsert(`/`.delete(` against the Supabase client returns **zero** matches
today: every write in this app, `profiles` included, goes through the backend. **A stale "the
frontend needs this" comment is exactly as dangerous as the missing revoke it excuses — re-verify
the claim against the current write path before trusting an old grant rationale, this file's own
included.**

## When adding a function

```sql
REVOKE ALL ON FUNCTION "public"."my_function"("uuid", integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."my_function"("uuid", integer) FROM "anon";
REVOKE ALL ON FUNCTION "public"."my_function"("uuid", integer) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."my_function"("uuid", integer) TO "service_role";
```

- **Prefer `SECURITY INVOKER`** (the default). A `SECURITY DEFINER` function returning rows or
  aggregates over student data is a ready-made way to read anyone's data; as invoker, RLS still
  applies if it is ever reached by a lower-privileged role.
- **If you need `SECURITY DEFINER`, pin `SET search_path`.** An unpinned definer function is the
  classic privilege-escalation vector.
- **End the migration with `NOTIFY pgrst, 'reload schema';`** so PostgREST picks up the new RPC.
- **`CREATE INDEX CONCURRENTLY` is not available in migrations** — Supabase wraps each in a
  transaction, and plain `CREATE INDEX` takes an `ACCESS EXCLUSIVE` lock while building. On a large
  table, build it manually with `CONCURRENTLY` first; the `IF NOT EXISTS` then no-ops.

## When changing an existing function's signature

Adding a parameter creates a **new** function rather than replacing the old one, so the migration
must `DROP FUNCTION` the previous signature explicitly — `CREATE OR REPLACE` alone leaves it behind
as an overload that is still granted, still callable, and unaware of whatever the new parameter
controls. Keeping both is not an option either: with named-argument RPC calls matching more than one
signature, Postgres rejects the call as ambiguous. The new signature carries a fresh ACL, so repeat
the revokes and the `service_role` grant against it.

That leaves a window. Backend code calling the new signature against a database that has not run the
migration gets PostgREST's `PGRST202`, which the callers here catch — so the failure is silent and
the symptom is empty data rather than an error. **Apply the migration before rolling out the code
that depends on it.**

Where an in-between state would be visible to a user, a temporary retry against the old signature is
a reasonable bridge — but only where doing so cannot violate what the caller asked for, and only if
it is removed once the migration is applied everywhere. Left in, it is dead code that looks live,
and it makes any *later* schema mismatch degrade to a quietly wrong answer instead of an error.

## Do not "fix" the RLS helper functions

`is_member_of_class` and `is_teacher_of_class`
(`supabase/migrations/20260709154104_teacher_read_policies_and_recursion_fix.sql`) are
`SECURITY DEFINER` **and deliberately granted to `anon` and `authenticated`**. RLS policies evaluate
them as the calling user, so revoking the grants breaks the policies they exist to serve. They are
safe by construction — both are `auth.uid()`-scoped booleans with no parameter to pivot on (they
answer "am *I* in this class", not "is user X"), and both pin `SET search_path TO 'public'`.

Audited against `pg_proc.proacl` on production and a local stack: five functions in `public`, and
these two are the only ones granted to an application role. Re-audit with:

```sql
SELECT p.proname, p.proacl
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public';
```

## The Supabase CLI is a repo-local npm install

It is **not on `PATH`** — `which supabase` and `Get-Command supabase` both report it missing, which
looks like "not installed" and isn't. It lives at
`node_modules/@supabase/cli-windows-x64/bin/supabase.exe` (platform-suffixed, so the directory name
differs on mac). Run it through npx from the repo root:

```bash
npx supabase migration list --linked
```

`supabase/.temp/project-ref` holds the linked project ref, which `--linked` resolves against.

## How a migration reaches production

**Merging to `main` applies the migration to production, a few minutes later.** The Supabase GitHub
integration does it — configured in the Supabase dashboard, which is why nothing in
`.github/workflows/` describes it. There is nothing to run by hand; `npx supabase db push` answers
"Remote database is up to date". The **"Supabase Preview"** check on PRs comes from that same
integration and verifies nothing: per-PR preview branches are switched off, so it reports `skipped`
every time. Never read it as the migration having been exercised.

**CI applies the migrations; it does not gate the merge.** The `Database migrations` job applies
every migration to an empty local stack, so one that cannot apply goes red on the PR — but branch
protection needs a paid plan on this private repo, so every job in `ci.yml` is advisory and a red PR
still merges. Read the check before merging; it is the only thing between a laptop-only migration
and production. It proves the SQL *applies*, not that the grants are right.

**The delay is the trap.** It is minutes, not seconds, so a check run straight after the merge
reports the migration as *not applied* — Local populated, Remote blank — and that is
indistinguishable from an integration that never fired. Don't conclude anything from one look; re-run
`npx supabase migration list --linked` before acting on a negative. It confirms only that the
migration *ran*: the CLI has no arbitrary-SQL command, so verifying the resulting policies and ACLs
means the dashboard SQL editor. The local `.env` files point at a local stack, so nothing in the
working tree reaches production.

## Run `assert_signal_rls.sql` locally before merging a change to it

The local stack's Postgres is a container and `psql` is inside it, so there is no need to wait for CI:

```bash
docker exec -i supabase_db_AdaptiveLearning psql -U postgres -d postgres -v ON_ERROR_STOP=1 -f - < scripts/assert_signal_rls.sql
```

Safe against a working database: the file is `BEGIN … ROLLBACK`. Exit 0 and a final `ROLLBACK` is a
pass.

**Do this for any change to that file, and for any migration that constrains a table it writes to.**
CI is downstream of the merge, so a broken fixture is otherwise found after the decision to ship.
Two things were caught the first time it was run by hand, neither visible in a diff:

- **A new unique index made the file's own fixtures illegal.** A `cog_session_ts_key` migration met
  a batching-loop fixture inserting five `cognitive_signals` rows sharing one `(session_id, ts)` — a
  `unique_violation` under `ON_ERROR_STOP=1`, failing the whole job and every assertion below it.
  The migration's own comment warns that a unique index "passes CI against an empty stack and fails
  against real data"; **the repo's own fixtures are also that data.** Check both.
- **A comment naming `$` `$` inside an anonymous code block closes the block**, and the syntax error
  surfaces hundreds of lines later. Nothing but execution finds that.

It is also the only place several pieces of arithmetic actually run — the rollup's per-measurement
counts, `record_topic_attempt`, `score_scale_of` — because the backend suite drives `main.py` with a
fake client, and CI applying the migration proves the SQL parses, not that it counts.

## `supabase/seed.sql` is gitignored, so a broken one is a local problem

Each machine generates its own with `supabase db dump --local --data-only`; nothing ships it, and CI
never runs it.

**A regenerated seed collides with the migrations that seed `math_topics`.** `db reset` applies every
migration *first*, and several now insert topics, taking ids from the sequence. A dump written with
explicit ids — which is what `--data-only` produces — then hits `duplicate key value violates unique
constraint "math_topics_pkey"` and dies part way, leaving a database with four topics and no users.
That reads as a corrupt checkout rather than a seed that needs regenerating.

Fix a local copy by inserting topics **by name** with `ON CONFLICT ("topic_name") DO NOTHING`, and by
deriving the `setval` from `MAX(id)` rather than hardcoding it — a literal was right only while the
seed was the sole writer, and winding the sequence back makes the *next* insert collide, which is the
same failure one step later. Nothing references `math_topics.id`: `record_topic_attempt` joins on
`topic_name`, and the one foreign key to it, `user_math_performance`, is not seeded.

`backend/tests/test_seed_sql.py` checks all three, and **skips when the file is absent** rather than
failing on something the repo does not contain. It is a guard for whoever regenerates the file, not
a gate.
---

# Signals

## Ingestion is push or pull, and which one is a setting rather than a guess

`eeg_poller` runs **inside the backend** and polls the sidecar over HTTP. That works only because
`start.ps1` puts both on one machine. The camera breaks it: the sidecar is a per-student local
process, and a hosted backend has no route to a student's laptop. So the sidecar POSTs to
`/api/signals/*` with the student's own token instead.

`INGEST_MODE` (`pull`, the default, or `push`) says which is live. **Explicit because the failure is
silent otherwise** — a poller that cannot reach a sidecar produces no rows, raises nothing, and
leaves a session looking live: indistinguishable from a headband nobody put on. Deploy the backend
anywhere but the student's machine and every session degrades that way with nothing to read.

Under `push`, `eeg_poller.start` raises `PushModeError`. `INGEST_MODE` binds **the poller only** —
the ingest endpoints stay open in both modes, so a developer can hand-post a batch under `pull`.
That is why the double-write warning asks `eeg_poller.claim_double_write_warning(session_id)`, the
real condition, rather than reading the mode as a proxy for it.

**Every endpoint that probes the sidecar checks the mode first, and there are eight.** "EEG service
is not running on port 8001" is true under `push` and entirely misleading. Two shapes:

- **Returns a payload** (`/api/eeg/{health,status,debug,devices}`) — the liveness field is `None`,
  never `False`, with `ingest_mode` alongside so the caller can say *why*. "Not probed in this
  deployment" is a different claim from "probed and down", and **a consumer that branches on
  falsiness renders both identically** (rule 1). The debug panel no longer reads this endpoint under
  push at all — `sidecarDebug` assembles the same shape from the sidecar the page can reach — so its
  `available` there is a **real boolean, observed rather than proxied**, and the panel tests it with
  `=== false`. Two-valued and three-valued sources under one field name is a trap of its own.
  Deriving it from the payloads is the other wrong answer: an idle sidecar answers `data: null` and
  a headband-less one an empty muse block, both ordinary, so **a payload that is empty in normal
  operation cannot stand in for reachability**. Hardcoding it `true` made the "not answering" line
  unreachable and drew a panel of blanks for a sidecar that was not running.
- **Raises** (`/api/eeg/{start,muse/refresh,muse/connect,muse/disconnect}`) — call
  `_refuse_under_push(what)` in `main.py`, *before* `eeg_client.is_alive()`, or the misleading 503
  wins the race. Don't write the 409 out by hand; one inline copy already drifted from the helper
  that replaced it.

**"Before" means before every sidecar call, not just before the liveness probe.** `/api/eeg/status`
had the check and still 500'd under push, because `get_muse_status()` ran a few lines above it:
`eeg_client._learner_headers()` raises when `EEG_API_TOKEN` is unset — the normal state of a hosted
push deployment — *outside* the request try. Test stubs for `eeg_client` must therefore **raise**
from `get_muse_status`, as `_StubClient` in `test_ingest_mode.py` does; a stub returning `{}`
modelled a deployment that does not exist and hid this for two rounds.

A ninth endpoint needs the same treatment and an entry in `_MODE_AWARE` or `_MODE_AWARE_RAISING` in
`backend/tests/test_ingest_mode.py`, which parametrises both modes over every member. This was found
one endpoint at a time across five review rounds because each site was written by hand and the test
listed only the endpoints someone had already remembered.

### The shared mapper decides what may be recorded

Both paths share `signal_mapping.py`. The mapping used to live in `eeg_client`, the pull *transport*;
the push path would have had to import an HTTP client it never calls to reach a pure function, or
keep a second copy — and a second copy of a unit conversion is how one path ends up storing
percentages while the other stores ratios.

`eeg_quality()` answers `no_signal` / `contact_poor` / `ok`, and all three mappers return `None` for
a channel that produced nothing:

- **`no_signal`** — a disconnected headband reports *zeroed* scores, and rule 2 arriving through the
  *write* side, where none of the reporting rules can see it: an unworn headband read as sustained
  zero focus rather than as no data.
- **`contact_poor`** — keep the row, null the eight measurement columns. "Recording but unable to
  measure" is not "no session", and `class_live` derives staleness from the newest row's `ts`. Only
  `signal_quality == "poor"` **with `quality_basis == "contact"`** counts; the legacy heuristic says
  "poor" for any focused student.

These rules lived inline in `eeg_poller` and were absent from the push path, so the same unworn
headband wrote nothing under pull and a zeroed row per tick under push. **Anything of this kind
belongs in the mapper:** it is the only place both deployments are guaranteed to read.

### The sidecar's push client does no arithmetic

`EEGResearch/src/app/services/push_client.py`, enabled by `PUSH_ENABLED` with `BACKEND_URL`. It
cannot import `signal_mapping` — different package — so instead of converting it sends the payload
**whole**: `/api/signals/cognitive` accepts a sensor-shaped sample (`features`/`bands`, 0..100) as
well as the flat already-mapped one, and maps the first itself. Don't add a divide to the sidecar.

- **The student's bearer token arrives from the browser and lives in memory for one session.** Never
  logged or written to disk — this runs on a student's laptop. `stop()` clears it, and changing
  session drops the old queue, since those samples belong to a session the new token may not own.
- **The queue is bounded and drops oldest, counted.** `deque(maxlen=…)` evicts silently, and an
  uncounted eviction is a signal path losing data with nothing to say so. That applies to *returning*
  a failed batch too — `extendleft` evicts the newest — which is why restoring goes through
  `_restore`.
- **A failure in one channel must not cost the others.** Each channel is drained immediately before
  its own POST, not all three up front; the first version re-raised on the first failure and threw
  away two already-popped batches.
- **The sampling hook emits `snapshot()`, not `latest_payload`** — `bands` and `ingestion` are
  assembled in `snapshot()` — and via `to_thread`, because `snapshot()` reaches
  `get_ingestion_meta()`, the one call the sampling loop already offloads for blocking.
- **A rejected window is not a reading.** `build_face_record` and `build_heart_record` always return
  a dict, with `emotion: None` / `bpm: None` and a `rejected_by`. Enqueue on *the reading*, not on
  the block's presence, or a 4 Hz session writes ~14k all-null rows an hour, every one counted as a
  sample. `source` alone does not test it: the heart block sets `rppg` unconditionally.
- **Nothing after `raise_for_status()` may raise, and no POST is cancelled mid-flight.** The rows are
  committed by then; a throw — or a `task.cancel()` during the request — restores the batch and the
  re-post duplicates them. All three signal tables now carry a dedupe key, so a re-post is a no-op —
  but this rule stands on its own: the key makes the *rows* idempotent, and nothing makes the local
  accounting so. `stop()` therefore *asks* the loop to finish and awaits it, cancelling only once
  `SHUTDOWN_BUDGET` is spent, and is bounded by the clock rather than by an attempt cap (12 attempts
  × 3 channels × a 4 s timeout is ~144 s on a Ctrl-C). A batch whose fate is unknown is
  `unaccounted`, which is neither `recorded` nor `dropped_locally`.
- **Delivery is counted from the backend's `inserted`, not from what was sent.** The endpoint drops
  samples for a sensor the student declined; counting sent would report a healthy session that
  recorded nothing.

The **browser** side has the matching rule: effect cleanup does not run on a tab close or hard
refresh, so `Adaptive.jsx` also stops the sidecar from a `pagehide` listener via `stopPushOnUnload`,
using `fetch(..., {keepalive: true})`. Without it the sidecar keeps the student's token and keeps
recording for up to an hour after they walked away — a consent problem, not untidiness. `sendBeacon`
cannot be used: it cannot set an `Authorization` header.

`/api/v1/push/start` refuses with 409 when `PUSH_ENABLED` is false rather than becoming a second
writer alongside a poller. The original reason was that `cognitive_signals` had no dedupe key;
`cog_session_ts_key` closes that and every writer upserts against it. **The refusal stays**, because
two writers on one channel is still a deployment nobody chose.

### The browser calls the sidecar directly, and two tokens are in play

`frontend/src/lib/sidecar.js`. Under push the hosted backend cannot reach a student's laptop, so
lifecycle control comes from the page: it calls `http://127.0.0.1:8001` itself. An HTTPS page may do
that — loopback is exempt from the mixed-content block; evidence and limits in
`EEGResearch/docs/LOOPBACK_FROM_HTTPS.md`.

**Don't conflate the two credentials.** `VITE_EEG_LOCAL_TOKEN` is the sidecar's own `API_TOKEN`, is
in the client bundle, and is *not a secret* — the sidecar binds to loopback, so it separates this
page from other pages in this browser, not one user from another. The student's Supabase access
token is a real secret, fetched per call, and handed to the sidecar once so it can post as them.

**Those four push refusals left pairing with no path, which is why the browser has one.** The
sidecar's own start/scan/connect routes were admin-only while the browser holds the *learner* token,
so every push deployment answered 401 to the one channel push exists for. `sidecar.js` now calls them
directly (`deviceStart`, `museRefresh`, `museConnect`, …) and `toggleHeadband` picks the transport
from `headband.pushMode` — one adapter, the same seven steps, because a second copy of the pairing
sequence would drift and that sequence is where the ordering matters.

**`require_local_controller` is what admits it, and it is scoped to the mode on purpose.** Admin in
both modes; the learner token *only* when `PUSH_ENABLED`. Under pull the browser gains nothing,
because the backend is the legitimate controller there. What it grants is bounded by what the learner
token already was: any page that could call `/api/v1/push/start` could already make the sidecar stream
a student's signals. Pinned by
`test_under_pull_the_learner_token_may_not_drive_the_hardware`.

**Re-hand the token on refresh.** Supabase access tokens expire roughly hourly and a lesson can run
longer; the sidecar holds one token per session. `Adaptive.jsx` re-calls `startPush` on
`TOKEN_REFRESHED`, replacing the token in place — same session id, queue untouched. Without it the
pushes 401 partway through and the samples sit in a bounded queue until dropped.

**Never call `supabase.auth.getSession()` inside an `onAuthStateChange` callback.** supabase-js v2
holds an internal auth lock while dispatching and `getSession()` waits on it, so awaiting it there
deadlocks. Use the `session` the callback is handed; that is why `startPush` takes an optional token.
The symptom is the worst kind: the refresh handler hangs, the sidecar keeps the expired token, and
every push 401s for the rest of the lesson with nothing raised.

**The camera is stopped when the Adaptive page goes away; the headband is not.** The headband stays
paired across navigation deliberately (the bridge holds the link, re-pairing costs a 12 s scan); a
webcam has no such cost and the consent copy scopes it to the questions. Two exits, because effect
cleanup does not run on a tab close: the route change sends `deviceStop`, `pagehide` sends
`deviceStopOnUnload` with `keepalive`, both reading the camera through a ref synced after every
render. `AdaptiveCameraLifecycle.test.jsx` pins both, and that a camera already off sends nothing.

All three ingest endpoints are rate-limited and length-bounded. `/api/signals/cognitive` was neither
until the push client existed, survivable only while its sole writer was the in-process poller.

## Samples are stored during a session, not while a headband merely sits paired

Under pull, Connect has to start the poller — it is what starts the sidecar's device stream, and it
feeds contact and battery to the page — and the poller used to write from its first tick. So a
student who paired and never started a question had rows on the teacher's Live view, and a "session"
in History, for a lesson that never happened.

The poller now has two states. `POST /api/eeg/start` takes `record` (default `true`, so callers
predating the flag are unchanged): Connect sends `record: false` — stream up, nothing written — and
`Adaptive.jsx`'s `armRecording` sends `record: true` on the first question, flipping the *running*
poller in place rather than restarting it. Ending the session stops the poller, so the recording
window is first question → Finish. After a Finish the next question is a new session; the headband
stays paired at the bridge throughout. `status()` reports `recording` beside `running` because they
are now different facts. Push needed none of this: `startPush` was already keyed on `sessionId`.

**A poller that is up but not recording still moves `last_ts`**, so arming starts from the live tick
rather than replaying a backlog — and so a paired, idle headband does not read as a sidecar that has
stopped answering.

## The bridge owns BLE recovery, and reports it

Reconnection used to be manual at all four layers at once, so a headband that fell off mid-question
cost the rest of the lesson's recording with nothing on screen saying so.

`service_auto_reconnect()` runs once per main-loop tick. A CONNECTED → not-CONNECTED edge arms a
bounded sequence of `connect_named()` calls against `last_connected_name_` (2/4/8/16/30 s backoff,
`MAX_RECONNECT_ATTEMPTS = 5`), launched on its own thread because `connect_named` blocks for up to
3 s. **The preset is not carried across and must not be**: `apply_model_preset()` re-derives it from
`getenv` on every CONNECTED, so a reconnect lands on exactly the configuration the process was
launched with — don't add reconnect-time preset logic. A liveness watchdog
(`MUSE_LIVENESS_TIMEOUT_MS`, 8000; 0 disables) catches the failure `NOTCH_STALE_MS` cannot: EEG stops
while libMuse still says CONNECTED. It measures from the later of the last packet and the connect
itself, since a preset switch interrupts streaming after every connection.

Two things about the edge detection are load-bearing. **`disconnect_muse()` sets `connected_ = false`
*before* asking the SDK to disconnect**, so the callback for a deliberate disconnect sees
`was_connected == false` and arms nothing; that ordering is the whole mechanism, and there is no
separate flag. And **every command a person sends cancels the sequence**
(`cancel_auto_reconnect()`), with a generation counter so an attempt already mid-`connect_named`
undoes its own connect when it returns — otherwise a reconnect landing after a deliberate disconnect
re-pairs a headband the student just released.

Status lines carry `auto_reconnect`, `reconnecting`, `reconnect_attempt`, `reconnect_max_attempts`,
`reconnect_exhausted` and `eeg_age_ms`, **additively** — `muse_connected` still means "up right now".

**A link held for `LINK_STABLE_MS` (30 s) resets the attempt count; a CONNECTED does not.** At the
*edge* of range every attempt reaches CONNECTED within seconds with no EEG following, and the
watchdog drops it 8 s later — so resetting on CONNECTED flapped a toast pair every ten seconds for as
long as the headband stayed there. Five short-lived reconnects now exhaust the budget like five
failures. The page says "disconnected" once per `DROP_TOAST_MIN_MS` (60 s) and "reconnected" only for
a drop it announced; the panel still tracks every one.

**The sidecar and the poller back off; the page shows the bridge's progress and only then drives its
own.** `TcpMuseBridgeAdapter` waits 0.5 → 5 s between TCP attempts (`connect_wait_remaining()`, reset
on success). `DeviceSession.health_fields()` — `last_good_ts`, `last_good_age_s`,
`consecutive_errors`, `preset_mismatch` (only after `PRESET_SETTLE_SECONDS`, since the two presets
legitimately disagree for a moment after every connect) — rides **inside `ingestion`** on both
`/api/v1/state` and `/api/v1/muse/status`, deliberately not as top-level keys the envelope would
drop. `eeg_poller._poll_wait` doubles the wait per empty read up to `POLL_BACKOFF_MAX_S` (5 s); the
first miss costs nothing, since an idle stream at session start is ordinary, and the cap is small
because the consent re-check shares the loop.

`Adaptive.jsx` polls the bridge in **both** modes (under pull `poller.running` never says the
headband went away), claims a drop only from `phase: 'connected'` (under pull `connected` is the
poller, true from `/api/eeg/start` and so before the scan has begun — keyed on it alone, every
pairing read as a drop and the page sent a second connect over the first, three clicks to pair on
hardware), keeps polling through the `reconnecting` phase, shows the bridge's attempt count, and
starts `startFrontendReconnect()` only on `reconnect_exhausted` or a bridge too old to report
`reconnecting`. **"Stop trying" sends a bridge disconnect before the usual teardown**, because
Disconnect's teardown stops the sidecar's stream without sending the bridge a command, and only a
command cancels its attempts. `pairOnce` takes the cancel token and checks it before the connect, so
a cancel during the 12 s scan cannot be followed by a pairing.

**The page's give-up path must tear down like Disconnect**, not reset state: under pull `connected`
is the poller, which the loop never stopped, so three seconds after "could not be reconnected" the
panel read STREAMING with a Disconnect button over a headband four minutes gone.
`AdaptiveReconnectPull.test.jsx`'s recorder mock drives `poller.running` for that reason — a mock
that always says running cannot see it.

### Adoption: `linkAlive` is the one answer to "is this link alive"

**Connect adopts a link the bridge already has — when EEG is flowing on it.** `pairOnce` reads the
bridge first and, on `muse_connected: true` **with `eeg_age_ms` under `ADOPT_MAX_EEG_AGE_MS`** (3 s),
goes straight to connected without the disconnect-then-scan. "Connected" alone is not evidence:
libMuse keeps saying CONNECTED after EEG stops, which is why the bridge has a watchdog, and that
disconnect is the page's only reachable bridge disconnect outside "Stop trying" — adopting a dead
link would leave nothing able to clear it. An older bridge reports no age and falls through to the
scan.

All three readers use `linkAlive(ing)` — Connect's adoption, the reconnect loop's "came back on its
own" check, and the telemetry poll's recovery — because the second and third had the same gap: after
the bridge had exhausted its attempts the loop declared success on the word "connected" and reached
the same unclearable state by another door. **Test fixtures that mean "connected" must carry an
`eeg_age_ms`.**

**But "not alive" is not "dead" on the recovery paths.** The bridge zeroes its packet clock on every
CONNECTED and reports `eeg_age_ms: null` until the first packet, and a preset switch keeps that null
for seconds — so every successful bridge reconnect briefly reads as connected-with-no-age, and a
reader that called that dead started a page-driven reconnect whose first act is a bridge disconnect.
`linkSettling` names that state, and the two recovery readers give it `SETTLE_GRACE_MS` (10 s, above
`PRESET_SETTLE_SECONDS` and the 8 s watchdog, so with the watchdog on the bridge decides first) —
one grace shared through `settlingSince`, not one per reader. **Adoption keeps refusing it**: it needs
positive evidence, and "no packet yet" is not that. The disconnect exists for a headband left
streaming from a *previous* session; one streaming to us now is not that. Consequence for tests: a
harness whose bridge starts connected is adopted without a scan, so both reconnect harnesses start
`muse_connected: false` and flip it from their connect mock.

### The bridge runs under a supervisor, in the launcher's window

`muse_native_bridge.exe` runs under `EEGResearch/scripts/run_bridge_supervised.ps1`, which
`start.ps1 -Muse` launches in the bridge's window. It restarts the exe on a non-zero exit, prints
every exit with its time and code, and gives up once more than five exits land inside ten minutes —
so a persistent failure (a missing `libmuse.dll`, port 8765 taken) stops with its cause on screen
rather than looping. A clean exit (Ctrl+C) is not restarted. It inherits `MUSE_ENABLE_OPTICS` and the
rest from the window `start.ps1` set them in and reads none of them itself, so a restart lands on the
configuration the session was launched with; nothing else has to change, because the sidecar's TCP
adapter reconnects on its own and the page treats the restarted bridge's "not connected" as a drop.
`EEGResearch/tests/test_bridge_supervisor.py` drives the loop against a stub `.cmd` (Windows only).

It is deliberately **not** a Windows service or a scheduled task: moving the exe out of the launcher
window is how those variables get lost. The debug panel's *Link* row tells a dead bridge from a
dropped headband — `consecutive_errors` climbing with `eeg_age_ms` absent is the bridge gone,
`eeg_age_ms` climbing with the bridge answering is the headband gone.

Electrode contact reaches a student through `lib/contactQuality.js`, which is
`signal_processing._signal_quality`'s contact half without the smoothing (the page debounces two poor
polls instead). The teacher's Live badge carries the age of the newest row and the same "weak signal"
the heart badge had, from `lib/signalAge.js` — `STALE_AFTER_S` there mirrors the backend's
`_LIVE_WINDOW_SEC` so the two surfaces agree on what counts as live.

## The simulator pairs like a headband, and streams whether or not it is paired

`SimulatedMuseIngestionAdapter` answers the bridge's three commands: `refresh` lists one device (`MuseS-SIM0`, named so
no status line or bug report can mistake it for hardware), `connect` pairs it, `disconnect` clears both, and the
pairing fields follow that state. Before this, a sim run could never exercise the pairing sequence, the adopt path or a
drop.

**`eeg_age_ms` is a packet clock, and the sidecar's sample stream stands in for the packets, deliberately**: null for
`PAIR_SETTLE_SECONDS` (5 s) after every connect, the way the bridge zeroes its clock on CONNECTED; then the time since
the last delivered sample, stamped on every `read_sample` *and on stream start*, so a running stream keeps it under one
4 Hz tick and a *stopped* stream lets it climb. That climb is the drop — CONNECTED-but-silent, the state `linkAlive`
and the watchdog exist for — and the only way to reach it with no BLE. The stream-start stamp keeps adoption reachable:
under pull, Connect starts the stream and reads the status before the first tick.

Two things are deliberately unlike hardware: the sample stream runs whether or not anything is paired, and the pairing
survives a stream stop, as the bridge holds a link across a session end. A device whose adapter has no
`send_bridge_command` — the camera — answers `ok: false, commands require EEG_SOURCE=muse`.

**Its electrode contact varies, on the clock, in two layers.** It was `hsi [1,1,1,1]` for ever, so a sim run never
reached the contact gate, the confidence step at the degraded line, or a `contact_poor` row — while on hardware
degraded is the ordinary state and poor the fault. The strap alternates seated and loose episodes, and inside an
episode each electrode holds an HSI state for a drawn streak, redrawn with the episode's weights (`CONTACT_WEIGHTS`,
`CONTACT_STREAK_SECONDS`, `STRAP_PHASE_SECONDS`). **The strap layer is what makes `poor` reachable**: with independent
per-electrode draws, three-of-four poor was ~1% of ticks at any weights, since electrodes going poor *together* is what
a loose strap does. Measured through `SignalProcessor._contact_ratio` over two simulated hours: good ~30%, degraded
~55%, poor ~14%, pinned by a test with loose bounds. `is_good` follows hsi so the processor's min of the two never
reads a contradiction, and `band_channels_used` counts the seated ones. Streaks are long against the 5 s smoothing, so
one is a verdict rather than a blip. **The raw channels are untouched**: contact changes what the bridge reports about
the electrodes, not the samples, so the artifact gate sees the same signal.

**The adapter takes a `seed`, and every draw comes from its own generators** — contact, battery, resting heart rate,
state drift and channel noise from one `random.Random`, optical noise from its own numpy generator — so building an
optics window cannot shift the contact sequence. A draw from the module-level `random` anywhere in the class breaks the
replay, and a test replays a run to catch it.

**Its cognitive state answers the lesson.** `record_answer` ends with a best-effort `eeg_poller.notify_answer`, which
under pull only, and only for a session with a live poller, POSTs `/api/v1/session/answer` — **on a one-worker notify
pool, never the request thread**: `record_answer` is a sync endpoint on anyio's ~40-slot pool and `requests` applies its
timeout to connect and read separately, so a sidecar that accepts and then stalls would hold a slot ~6 s per answer on
the hottest path. Pending deliveries are capped (`NOTIFY_MAX_PENDING`); past the cap a notification is dropped with a
log line, so a stalled sidecar costs notifications, never threads. `stop_all` shuts the pool down and joins it, **and
never resets the pending counter**: every submit is balanced by its delivery's `finally`, so after the join it is 0 on
its own, and zeroing it *before* the join left it at −1.

Nothing on the request path waits on the returned future. `stream_manager.report_answer` hands it to the adapter if it
has one and answers `applied: false` otherwise, so **a real headband ignores it and nothing feeds back into scoring on
hardware.** The simulator nudges its hidden focus and calm per answer into a bounded offset (`TASK_BIAS_BOUND`) that
decays on the clock, applied to the state *before* the bands and raw channels are solved from it, so the processor
meets it through its own smoothing and artifact gate. It cannot trip that gate, pinned by a test. `focused` still
cannot fire on a sim run — that is the pipeline's property, not the bias's. The backend sends `correct` only, and the
sidecar route is admin-only under pull.

**It carries a synthesised pulse, fed through the unmodified heart path — opt-in, and marked.** Off by default
(`EEG_SIM_OPTICS=false`, read only under `sim`): a plain `./start.ps1` must not store a made-up heart rate, and off,
every window is refused as `no_samples` exactly as a headband without optics is. On, the window is `synthetic`,
`build_heart_record` puts that on the record (only when true, so hardware records keep their shape) and
`signal_mapping` writes it into the row's `raw` — the source stays `muse_optics`, because consent is enforced per
sensor and the pulse stands in for that sensor, so `raw.synthetic` is what separates a stored rate nothing measured
from one a headband did. **Both ingestion paths carry it the same way**: `push_client` sends it as a top-level field
(never inside the `raw` it hand-builds), `HeartSample.synthetic` receives it, and `/api/signals/heart` puts it on the
block the shared mapper derives from — so a client cannot mark or unmark a row by posting the key in `raw`, and only a
derived `True` survives. The first cut marked the poller path only and a camera run stored the unmarked row the mark
exists to prevent: the mapper rule again.

`optics_window` builds the last 25 s on demand from the clock — a pulse at a resting rate drawn per simulator
(`HEART_REST_BPM_RANGE`, 62–84) with a slow drift, raised by misses through the same decaying task bias, a second
harmonic so a spectral argmax cannot read double, and independent noise per channel so the beat consensus has four
opinions of one heart — at 64 Hz on the bottom rung's four channels, complete and gap-free. History exists while the
stream is up *and* a device is paired, from whichever began later, and is cleared with the stream, the link or
`clear_optics`. So a sim run sees `warming_up`, then `unconfirmed_anchor`, then a trusted rate within a few bpm of the
simulator's own. Nothing downstream is told it is synthetic beyond `bridge_mode: python_sim`.

## Battery is device telemetry, and null for the first stretch of every session

`battery_percent` rides on the bridge's ingestion block through to the badge beside Disconnect.
Registered on **every** preset, not just the optics ones — libMuse fires BATTERY on its own schedule
rather than as part of a preset's stream, so it costs nothing on `PRESET_21`.

**Null until the first packet, which is most of the first minute.** That is normal, and it is why the
badge renders nothing rather than `--%`: a permanent empty slot reads as a broken sensor. Rule 2
applies with unusual force because **0% is a real and alarming reading** — `pct || null` anywhere on
this path erases exactly the value the badge exists for, so the checks are `typeof pct === 'number'`
and `!= null`. The bridge stores −1 for "not reported" and `main.cpp` turns that into JSON null.

Under `sim` it reports a simulated charge (it was null, on the grounds that a made-up percentage is a
number a student acts on; the classroom simulation needs the badge exercised): null for
`BATTERY_FIRST_REPORT_SECONDS` (50 s) after every connect, then a level drawn from
`BATTERY_START_RANGE` (55–100) draining at `BATTERY_DRAIN_PCT_PER_HOUR` (10) **on the clock, not the
stream** — a BLE event, like the real one — floored at a reported `0.0`, never `None`. The charge
survives a disconnect (one headband; the *report* goes null with the link).

**Cleared on disconnect in both places** — `reset_device_fields_locked` and the page's own state. A
charge percentage left standing describes the headband that just went away, and it is the one number
here a student is asked to act on.

## Headband heart rate is a held window, not a per-tick reading

The headband is the primary heart source (the camera is emotion-only), reaching `heart_signals` through
`optics_processing.build_heart_record`.

- **Nothing arrives unless `MUSE_ENABLE_OPTICS` is on**, and it stays off by default. The flag is narrower than its
  name: the OPTICS/PPG listeners are registered unconditionally, so "emits no optics" stays distinguishable from
  "never asked". What it gates is moving a capable headband off `PRESET_21`, and two things argue for leaving that
  alone. The **bandwidth cliff**: 16 CH optics at 64 Hz alongside 4 CH EEG drops the BLE link within ~20 s *and*
  collapses electrode contact to `[4,4,4,4]`, while 8 CH and 4 CH hold for minutes — which is why the default `1035`
  rung is the safe side of it. And changing preset at all is an *EEG* risk: it moves bit depth 12 → 14 and on some
  rungs the channel count, so a silent EEG regression would be blamed on whatever shipped beside it. With the flag off
  a session records no heart rate and every window is refused as `no_samples`: the honest answer, not a fault.
  (`connect_named` setting `PRESET_21` unconditionally is not an override — `get_model()` returns `MU_02` until
  CONNECTED, so the real choice happens in `apply_model_preset`.)
- **The window is placed on `seq`, never on `mono_ts_ms`.** The bridge's stamp records BLE *delivery* — ~9% of samples
  share one with their predecessor and the rest arrive in bursts — so `seq` is the only real sample index, and the
  stamps measure only an average rate across the whole window. That rate is `seq`-span over elapsed seconds, not
  `len(rows)`: with samples dropped, counting rows reports a rate low by exactly the loss and scales every bpm down
  with it. The opposite call to `rgb_window`'s median-of-intervals, and the reason is the clock, not preference.
- **Sample *loss* is gated separately from sample *rate*, and only the second is obvious.** `fs` comes from `seq`,
  which counts what the headband **sent**, so it reads a healthy 64 Hz no matter how few samples arrived;
  `window_coverage` is elapsed span, which the survivors still bracket. A window can pass both while being almost
  entirely `np.interp` output — and interpolation manufactures the smooth periodicity autocorrelation rewards, so the
  result is a *confident* wrong rate (one sample in 32 gave 55.8 bpm at confidence 1.00 against a true ~68; one in 64
  gave 44.0). So `received_rate_hz` carries the same `MIN_SAMPLE_RATE` Nyquist bar, `completeness` rides on the row,
  and anything below 10 Hz effective is refused as `effective_rate_too_low` — **including windows that happen to still
  be right**: nothing available separates "sparse but above Nyquist" from "aliased", and a refusal costs one window
  where an acceptance costs a number on a parent's chart.
- **25 s window, recomputed every 10 s, then *held* on the payload.** The 10 s step is what `MAX_BPM_CHANGE_PER_S` was
  validated against. Holding is what lets a 1 Hz poller see every reading; emitting for one tick would have push
  record everything and pull almost nothing. **An EEG no-data tick drops the held block but must not restart the
  cadence** (`_drop_held_heart_block`, not `_reset_heart`): `drain_samples` raises whenever no EEG sample arrives in
  its timeout, so flapping contact takes that path repeatedly, and restarting the clock there re-stamps the same 25 s
  of optical signal every tick — up to 4 near-identical rows a second, which dedupe on `ts` cannot collapse. It leaves
  the tracker alone too: EEG dropping out says nothing about the optical emitters.
- **A session's first heart reading is withheld until a second window agrees.** The window right after motion produces
  a *confident, unanimous, wrong* rate, and no in-window test separates it from a real one — agreement, out-of-band
  power and peak margin were all tried, and one candidate discriminator rejected every genuinely fast rate along with
  it. So the tracker asks a different question: is the periodicity still there a step later. Motion settling is not; a
  heartbeat is. An unusable window in between discards the candidate rather than bridging it. **Re-acquisition after a
  dropped lock goes through the same rule**, and needs it most: a lock is dropped because two windows disagreed, so
  whatever re-acquires comes from exactly the population this distrusts. Costs one usable window of latency and
  refuses nothing. `rejected_by="unconfirmed_anchor"` says a rate was *withheld*, which is not `no_signal`.
- **The block carries its own `ts`, and both writers key on it.** Held, one measurement arrives on ~40 consecutive
  ticks. `map_heart_to_heart_signal` prefers `heart["ts"]`, the push client dedupes per `(device, source)`, and the
  poller upserts on `heart_session_source_ts_key`. The camera's block has no `ts` and takes the tick's.

**A payload key needs a field on `InterpretedEegData` or `/api/v1/state` deletes it.** `Envelope.data` is a declared
model and **pydantic drops undeclared keys silently** — the same trap as `main.FaceSample`, one layer further out.
`heart` was undeclared, so under `pull` (the default) a headband on an optics preset could never record a heart rate:
window built, anchor confirmed, block held and stamped, then deleted at the boundary with nothing raised. It hid
because **push bypasses the envelope** and every heart test asserts on `session.latest_payload`, the dict *before* the
model. `tests/test_state_envelope.py` derives the check from `stream_manager`'s source.

**`features` is its own nested model, and the same trap one level down.** That check sees top-level keys only; a
diagnostic added to `SignalProcessor.update`'s return dict has to be declared on `schemas.FeatureData`, which
`test_every_feature_key_the_processor_returns_is_declared_on_the_model` derives by calling the processor.

**The bridge accepts one TCP client** (`listen(…, 1)`), so nothing can tap the raw 256 Hz stream while the sidecar
holds it. Every frame does reach the sidecar — the queue is drained in full each tick, then only `samples[-1]` is
scored — so a consumer of the raw stream belongs inside the drain, not on a second socket.

### RMSSD is an enrichment, and a null one is normal

`build_heart_record` derives it through `hrv_processing.estimate_hrv` over the same 25 s window and the same rate —
sharing them is required, not incidental, so the two cannot disagree about whether a window is usable. Roughly one
window in five is gated out even seated and at rest, so **nothing may make a heart rate conditional on RMSSD being
present**: `stress_score` is defined on heart rate alone, and a score whose definition shifted when an input dropped
out would be unreadable across a session. The refusal has its own field (`rmssd_rejected_by`, carried into `raw`) so it
is never confused with `rejected_by`, which says whether there is a reading at all.

Validated against simultaneous watch ECG, seated, at the shipped 25 s window: **r = 0.78, bias −3.1 ms, RMS 5.2 ms**,
measured in `test_optics_rmssd.py` rather than carried across from the 30 s capture — a shorter window has fewer beats
to average, so don't quote the 30 s figures.

**All of that depends on more than one optical channel being alive, and a count-based quorum is how it silently
stops.** RMSSD is usable only because beats are agreed across channels and timed by averaging the channels that saw
each one; with one live channel both steps become the identity and what is recorded is the raw per-channel detector,
which ranged 29–246 ms across four channels watching the same heart. Run single-channel it reports every window, never
refusing, at up to +75% error — and nothing downstream catches it, since `agreement` is 1.00 by construction against
one waveform. So `consensus_beats` refuses below `MIN_POPULATED_CHANNELS` and scales its quorum as
`CONSENSUS_FRACTION` of the channels that produced detections. **A fixed count is what to avoid**: 3 was tuned on 4
channels and would be 3-of-16 on the wide optics presets.

Beat coverage is bounded **both ways** for the same reason. The lower bound catches missed beats; without an upper one
a double-detected notch or an octave-low rate is indistinguishable from clean, since every count beneath it looks
healthy. Genuine 4-channel windows reach 1.054, so the bound sits at 1.15 — above real data and well below the
1.20–1.26 single-channel runs produce.

`sqi` and `stress_score` are **not derived** on either path; those columns stay null, so `heart_signals.stress_score`
has no producer — don't read an empty tile as a broken query.

### The poller's heart write is consent-gated, and that gate is the only one there is

It writes with the service-role client, so neither RLS nor `/api/signals/heart`'s per-sample check reaches it.
`eeg_poller.set_heart_consent_check(fn)` is wired from `main` at import; `fn(user_id, source)` is built from the same
`_may_record` + `_permitted_heart_sources` pair the endpoint uses, so the two paths cannot disagree about one student —
and because `_permitted_heart_sources` reads the composed `record_*` flags, the school year applies without either site
mentioning it. Unwired it denies, a failed read denies, and it is re-read on `CONSENT_RECHECK_SECONDS`. Per *source*,
not per channel: a student who allowed the headband and refused the camera has consented to `muse_optics` and not to
`rppg`.

**`set_consent_check` returns a bool, so it cannot say *why*.** A withdrawal, a closed school year and a failed read
all arrive as `False`, and the poller's log used to assert the first. `set_consent_reason_check(fn)` supplies the
sentence for `start()`'s refusal, wired to `_poller_may_record_eeg_reason`, which **reuses what the bool check just
computed** rather than re-reading `_may_record` — a second read would cost another round trip on every refused start
and could return a different verdict from the one it is explaining. Unwired, `start()` falls back to a consent-only
message, so a test that stubs `_consent_check` must stub this too or it reaches a real database.

**On the pull path, EEG consent gates the heart channel as well — deliberately, and only there.** `_record_heart` runs
inside the poller loop, and withdrawing `eeg` stops the poller outright, so a student who allows `headband_optical` and
declines `eeg` records no heart rate under `pull` at all. Accepted rather than overlooked: it errs the safe way — the
path records *less* than consent allows, never more — and undoing it means a poller that keeps running with only its
cognitive write switched off, which is a session reporting EEG stopped while still holding the device. **Push is
unaffected**, so this is a real difference between the deployments and the one place they are knowingly allowed to
differ. Pinned by `test_withdrawing_eeg_consent_stops_the_heart_channel_too`. A feature, not a fix; raise it as one.

### Optics and EEG coexist at the 4 CH rung

On a MuseS at the default `PRESET_1035`: good EEG channels **63.8% on `PRESET_21` against 60.7% with 4 CH optics**,
zero link drops in three minutes, optics at 64.3 packets/s. So **optics is not what degrades EEG contact** — the
earlier working hypothesis, formed across several failed attempts, was wrong. The 16 CH cliff is real and separate.
Residual `is_good` failures with `hsi [1,1,1,1]` are dry electrodes, not bandwidth.

**Verify the flag reached the bridge by reading the process, not the launcher.** Every earlier attempt measured a
bridge that never had the variable — set in a string the outer shell expanded first — and the run looked exactly like
optics being harmless. The bridge reads `getenv`, so the check is its own environment block, or
`requested_preset`/`active_preset` on `/api/v1/muse/status`, which must both read `PRESET_1035`. `active_preset: ""`
means the device never applied one.

### Seated BPM is cleared; gait is not

Two regimes with different mechanisms, and the rule is scoped to the right one. **Seated** — 14 of 16 windows accepted
against a simultaneous watch ECG, max error 2.1 bpm: a student at a desk, good enough to record and act on. **Desk
fidgeting** — degrades into *refusal*, not error: 12 of 16 rejected at confidence 0.00–0.50, the 4 accepted within
7.5 bpm, while the watch's own ECG failed one of three attempts outright. **Gait** — *confident error*: 162–167 bpm at
confidence 1.00 for six consecutive windows against a watch-verified 104, which is step cadence at no harmonic
relation, so no periodicity test sees it and four have been tried.

Confidence discriminates in the second case and not the third because running supplies a *sustained clean rival
oscillator* for the autocorrelation to lock onto, while fidgeting merely destroys the pulse. The accelerometer is the
only signal independent of the periodicity being confused, and is what a walking-around deployment would need — not a
prerequisite for a maths lesson at a desk.

Two limits worth keeping in view: the seated validation is one adult over three minutes, not a child over a lesson; and
7.5 bpm at high confidence is harmless for fusion (which can only ease difficulty) while being a real if modest error
on a parent-facing chart. Evidence and the failed discriminators: `EEGResearch/tests/fixtures/README.md`.

## Camera rPPG is validated-and-rejected. `FACE_HEART_ENABLED` stays off

Against a simultaneous watch ECG, POS over the mean RGB of our three ROI boxes reported **47.7 bpm at
confidence 0.74 against a true 88**, over five minutes with the face found in 8988 of 8988 frames.
That scope was right, and it is now closed — **including for the RLAP weights, so don't chase the
licence.** Four learned front ends (`RhythmMamba.pure`, `RhythmMamba.rlap`, `FacePhys.rlap`, plus
POS) were run against ECG on a paced-breathing capture where the true rate rose 16 bpm: **none
tracks the rate, and the raw green channel scores as well as any of them.** `FacePhys.rlap`, the best
model on the largest dataset, reported 128.8 bpm for a true 89. Full tables:
`EEGResearch/tests/fixtures/FACE_RPPG_ECG.md`.

Five methodological rules came out of it, and they outlive this camera:

- **Score against a *moving* truth.** Over a half where the truth held near 68, RhythmMamba accepted
  70/83 windows at a median error of −5.9 bpm — a shippable-looking number from a model that emits
  ~62 whatever the heart does. Paced breathing (4 s in, 6 s out) swings the rate 10–20 bpm while the
  subject stays seated and still, which is what caught this.
- **But don't compare across the breathing switch.** Deep breathing moves the chest and head as well
  as the heart rate, so a model responding to breathing motion produces the same signature as one
  tracking a pulse. Correlate inside one breathing regime.
- **Always compare against the best constant, never against zero.** `.rlap` has MAE 8.5 against a
  best-constant 5.8 and r = −0.14 — *a model that always answered "68" beats all four front ends*.
  Single-digit absolute error is not evidence of measurement when the truth barely leaves the
  predictor's output.
- **Check chromaticity stability before blaming a result on the method.** A television in the room
  put chromaticity CV at 5.00% against 0.20% with it off; POS projects onto a plane chosen for a
  *fixed* illuminant. It is not a lighting-*level* problem either: in-band fluctuation on the clean
  capture is 0.533% of mean against a photon-noise floor of 0.03–0.12%.
- **`ppg_processing`'s confidence does not apply to a single-channel source**, and better hardware
  would not fix that. Its three terms were built for four contact channels: `agreement` is 1.00 by
  construction, `margin` is highest exactly when there is no rival structure to beat, and noise
  scored inside the range the code documents as a clear pulse. The gate is not weak, it is
  **inapplicable** — the same trap as single-channel RMSSD above.

The remaining suspect is the camera's own temporal denoising: **31.4% of consecutive frames carry
bit-identical ROI means**, ~20 distinct frames per second inside a 30 fps stream. Testing that needs
a camera exposing raw frames, not another model.

**The cost objection is gone and the licence one never applied.** Both upstream packages are MIT and
ship pretrained weights; the RLAP Data Usage Agreement is on the *dataset*, not the weights, and
`.pure` weights avoid the question entirely — so **name the model explicitly**, since `FacePhys` is
`.rlap`-only and is the package default. Every live selection in `FacialRecg/` pins `.pure`;
`ubfc_rppg_exp_dataproc.py` is the deliberate exception, since it sweeps the whole grid and its committed report
would otherwise be unreproducible. `EEGResearch/scripts/export_rhythmmamba_onnx.py` patches a
vendored `open-rppg` (~20 lines that tf2onnx cannot convert) and emits a 22 MB model running under
**onnxruntime alone**, already a dependency: 1.5 s to load against ~34 s, matching **the unpatched
package** at correlation 0.99985 — measured against a baseline captured *before* patching, because
comparing the export to the patched model only proves it reproduced what it was exported from. The
`.onnx` is not committed; the script regenerates it. Numbers:
`EEGResearch/docs/RPPG_DEPENDENCY_COST.md`.

**This settles the cost, not the accuracy** — that still needs a video + ECG capture, and the POS
rejection stands. `EEGResearch/scripts/capture_face_video_ecg.py` is that capture: 128×128 face crops,
lossless because every lossy codec discards exactly the variation rPPG reads, and it **refuses to
write inside the repo** — this is the one artefact that must never be committable, and `git add -A`
does not ask. `--delete` clears the frames and stamps the header, since a cleaned-up capture with no
trace is indistinguishable from one nobody cleaned up. The `.npy` is **trimmed on close to the frames
actually captured**: `open_memmap` zero-fills, and an untrimmed tail reads back as black frames rather
than absent data, which a windowing script would feed to the model as a sharp non-physiological edge.

The camera ships **emotion-only**. POS is kept because it is correct and is the front half of any
future attempt; do not read its passing tests as evidence it measures a heart rate.

## `attention` has no producer; `gaze_x`/`gaze_y` do

`FaceCaptureAdapter` runs the face-mesh landmarker on its own `GAZE_INTERVAL_S` cadence — 5 Hz, not the frame rate,
because it is a *second* detector doing its own face detection rather than reusing the Haar box. `FACE_GAZE_ENABLED`
is **off by default**: it needs `models/face_landmarker.task`, which is not in the MediaPipe wheel.
`./start.ps1 -Gaze` fetches and checksums it at setup; the sidecar deliberately **never** fetches it itself, and
`FaceMeshLandmarker` only ever refuses.

**The URL is pinned to `/1/`, not `/latest/`.** Google serves both and they are the same bytes today, but a checksum
pinned against a moving URL fails on the next release *as a checksum mismatch* — which reads as a compromised download
rather than an upstream version bump. The digest is re-checked when the landmarker loads, not only at setup:
`ensure_model` protects the moment of install and nothing after it, and a truncated or hand-swapped `.task` would
otherwise produce landmarks that are wrong rather than absent.

**A missing model costs gaze, not the camera.** `connect()` tolerates a landmarker it cannot build, logs, and lets the
channel report `rejected_by="landmarker_unavailable"`. Deliberately unlike the emotion classifier beside it, which may
refuse the whole device: emotion is the camera's primary measurement, gaze is an opt-in extra nothing yet renders, and
taking heart and emotion down over a hand-edited `.env` is the wrong trade. The channel stays *enabled* while
unavailable — reporting it as off would be a false claim about how the deployment is configured.

Three things about that path are load-bearing:

- **It samples before the Haar early-return.** A Haar miss says nothing about whether a mesh is available, so returning
  early would make gaze silently depend on a detector it does not use — and fail exactly on the faces hardest to find.
  `_sample_gaze` (in `face_ingestion.py`) also never raises, because it runs *before* the colour sample and an
  escaping exception would cost the heart channel every frame.
- **Emotion and gaze are two measurements, so they get two refusal fields.** `rejected_by` stays the emotion refusal
  and `gaze_rejected_by` is its own, exactly like `rmssd_rejected_by`. Collapsed into one, a refused gaze on a
  well-classified face explains the wrong null.
- **A reading is an emotion *or* a gaze.** `push_client` gated on `emotion is not None`, right while emotion was the
  only measurement; unwidened, a window where FER+ refused and the landmarks did not is dropped. It still refuses when
  *both* refuse, or the all-null flood that gate was added to stop comes back.

**`gaze_x`/`gaze_y` are eye-in-head, so they need `head_yaw`/`head_pitch`/`head_roll` to mean anything about where a
student is looking.** Point-of-regard is head pose plus eye offset; with only the second term, a student turned 30°
away with centred eyes reads as `gaze_x ≈ 0`, identical to one facing the screen. `head_pose()` fills the three pose
columns on the same landmark call, and they refuse *independently* of gaze (near profile the fit refuses while the eyes
are readable; a closed eye refuses gaze while the pose is fine), so `pose_rejected_by` is its own field.

**A column here needs a field on `main.FaceSample` or it can never be stored.** `/api/signals/face` is the *only*
writer of `face_signals` in either mode, and Pydantic drops undeclared keys silently — so the sidecar posts them, the
endpoint discards them before the handler runs, and the column reads as "not measured" for ever. That happened to the
three pose columns with every hop between the landmarker and the mapper wired and tested.
`test_every_column_the_mapper_writes_can_be_supplied_by_the_endpoint` derives the check from the mapper.

**Pose is deliberately not in the rollup**: averaging an angle over a day is close to meaningless — ±40° of swinging
averages the same 0 as never moving — so the useful aggregate is time-past-a-threshold, and that threshold belongs with
whatever first renders it.

Gaze keys are **absent** when the channel is off, `None` + a reason when refused, a number when measured (rule 1).
**0.0 is a valid gaze** (dead centre), so a refusal must never be recorded as one. A landmarker that raises stores
`rejected_by="landmarker_failed"` rather than leaving the reading unset: unset reads as `no_reading`, the *warming-up*
state, so a corrupt model would otherwise claim to be starting up for a whole session.

### `face_signals` has two producers, so its counts are per *measurement*

`rollup_signal_day`'s `'emotion'` channel takes `sample_count` as `count(*) FILTER (WHERE emotion IS NOT NULL)` —
unlike the cognitive and heart channels, which count every row, because those tables have one producer each. A window
where the landmarker read a gaze and FER+ refused is a real face row with no emotion in it, and counting it would make
enabling gaze read as *emotion coverage improving*.

Two halves that have to move together: `_weekly_signal_report`'s raw-day fallback counts the same thing, or
`face_samples` means something different depending on whether the day has been rolled up yet. The row's *existence*
still gates on `count(*) > 0` over all face rows — `expire_signal_rows` refuses a day with no rollup row, so a
gaze-only day must still get one or its raw rows never expire. Asserted in `scripts/assert_signal_rls.sql`.

### The geometry half, and what it may not claim

`face_geometry.py` is the arithmetic — named landmarks in, head pose and iris offset out, pure numpy so CI can test it.
`face_landmarks.py` is the other half: MediaPipe Face Mesh mapped onto those names, and the only file that knows a mesh
index from a face part, so swapping detector rewrites it and nothing else.

**MediaPipe 1.0.0 removed `mp.solutions` — the entire legacy Solutions API.** `mp.solutions.face_mesh` raises
`AttributeError`, which reads like a broken install and is not one. The Tasks API (`vision.FaceLandmarker`,
`RunningMode.VIDEO`, `detect_for_video`) replaces it and still returns the 478-point mesh, so `MEDIAPIPE_INDICES` is
unaffected. The model is **no longer in the wheel** — `_TasksMesh` loads `models/face_landmarker.task` (gitignored;
override with `FACE_LANDMARK_MODEL_PATH`) and refuses with the fetch command when absent. The Tasks call shape is
adapted at *construction* rather than in `locate()`: `locate()` is the half with tests and its injected collaborator's
shape is the legacy one, so porting the untested half to fit the tested half keeps every existing test on real code.

**Everything left of the camera is measured in image coordinates, and the frame is not mirrored**, so a subject's own
left is the image *right*. Looking left drives `gaze.x` **positive**; turning the head left drives `yaw` **positive**;
`pitch > 0` is the face pointing *up*. `CANONICAL_FACE` must therefore put the subject's left at **positive x** — it
did the opposite once, and because the fit solves for a rotation and a rotation cannot reflect, a person sitting
perfectly square on was refused `implausible_pose` on 120 frames of 120. **Round-trip tests cannot catch this**:
rotating the model and recovering the rotation is self-consistent under either handedness, which is how 32 of them
passed over an unusable model. Tests that pin it construct a frame from the image convention instead
(`test_the_model_handedness_matches_a_real_frame`).

**`gaze` cannot detect a left/right swap and must never be described as doing so.** Both eyes are averaged in image
coordinates and `_eye_offset` divides by an absolute width, so permuting the labels returns a bit-identical number.
`head_pose` is the adjudicator — a mirrored table makes the correspondence unfittable, so it *refuses* rather than
answering wrongly.

**The index table is unverified against hardware** — MediaPipe ships no canonical mesh file and there is no camera in
CI, so the mapping comes from published topology rather than measurement. `check_topology` re-derives what any real
face satisfies (eyes above mouth, nose between the eyes, iris inside its own eye) and refuses a set that does not,
turning a wrong index into a first-frame refusal. It cannot catch a mirror, so it needs the manual check:

```bash
python EEGResearch/scripts/verify_landmarks.py --gui
```

Three prompted steps with automatic verdicts — square on, eyes left, head left — because a check that costs twenty
minutes of assembling a camera loop is a check nobody runs. **Steps 2 and 3 test different things**: step 2 is iris
tracking and the image-x sign, step 3 is the pose fit's handedness, and step 2 *cannot* detect a mirror. Records no
video; `--gui` previews it, deliberately **unmirrored**, since the whole question is which way is left.
(`capture_face_video_ecg.py` has the same flag for a different reason — it *does* write frames, so its preview is about
not wasting a five-minute capture.) Neither preview adds a way to persist a frame, and a test on each asserts that. It
deliberately scores no attention: the geometry has a right answer and can be checked against one, the inference to
"attending" is a judgement, and keeping them apart is what lets the judgement be revised without re-deriving anything.

Passed three times on one adult and a laptop webcam, across the `opencv-contrib-python` swap. Two firsts came with it:
the **detector cross-check** (61 frames, mesh and Haar both found a face on all 61 — the only time
`face_roi.FaceLocator` has been exercised against a real face), and the **emotion path end to end** (crops accepted,
FER+ confidence 0.94–0.99 — every other emotion test injects a fake ONNX session). **Plumbing only**: high confidence
means it ran, not that it read the face right. **Pitch at square on is posture, not a fixed offset** — −13.8, then
−6.7 and −7.2 in the same setup within the hour, so the subject's head angle dominates and the 20° tolerance absorbs
it. Re-run after any change to the index table or the canonical model.

It uses an orthographic fit, **not `cv2.solvePnP`**, because solvePnP needs camera intrinsics we do not have — a
guessed focal length yields a systematically wrong pose that still looks like a face turning. The trade is that
perspective is ignored, so it degrades at close range and large angles. **Yaw is measurable only within ±90°**: past
that the Euler recovery returns the other branch of a two-fold ambiguity no rotation matrix can resolve, corrupting
pitch and roll by 180° as well, so it refuses with `implausible_pose` rather than reporting a mirrored angle.

### `attention` is unproduced, and deliberately

Blocked on a labelled reference rather than on code: "attention" inferred from head direction is least valid for
exactly this product's users, and unlike a FER+ label it renders as a percentage, which reads as objective. A child
looking away while thinking is not inattentive, and one adult is not a validation set for a construct whose failure
mode is population-specific.

**Every surface that rendered it has been removed** — the teacher's Live gauge, `SessionReview`'s ribbon field, the
parent and teacher `face_attention` tiles, the weekly chart series and the LLM strategy prompt's sentence. The
three-state logic meant none of them lied, but a tile that can only ever say `Calibrating` teaches a reader to ignore
it, and it occupied space on the surfaces where trust matters most. `hasSignalSummary` on the parent dashboard dropped
`face_attention` with them: that list tracks what the tiles can render, so leaving it in would admit a child whose only
reading is attention to a card with no tile to show.

**The column, the payload field and `face_geometry` all stay.** The measurement is still the plan; only the claims
about it are gone. Fill it when there is a labelled reference, and put the UI back in the same change — not before.

**`identity_confidence` was retired instead — do not add it back without a consent decision first.** Matching a child's
face against a stored identity is a *different purpose* from what the camera consent asks about, so it needs its own
consent channel and copy before it needs a model. Its removal also closed a live footgun: `face_signals` carried two
confidences and `signal_fusion`'s face channel read the wrong one, so a clearly identified face with a garbage FER+
label withheld a difficulty increase while a well-classified expression on a poorly identified face was discarded, both
silently. `emotion_confidence` keeps its qualified name for that reason.

## EEG focus, calm and confidence: measured on a person once, and most of it failed

Until the reference captures, the three scores had never been compared against a wearer doing a known thing — the
simulator solves its bands *from* the scoring formulas, so every green test on that path was the formula agreeing with
itself. Two labelled captures (one adult, MuseS) are scored in `EEGResearch/tests/fixtures/EEG_REFERENCE.md`; the
recordings stay outside the repo.

Captures are taken with `EEGResearch/scripts/capture_eeg_reference.py`. Replay one through the shipped path with
`EEGResearch/scripts/replay_eeg_capture.py` — **and score a change as replay-against-replay (`--save`, then
`--against`)**, never recorded-against-replay: a recording carries the inputs but not the prior state the live sidecar
had. `replay_eeg_capture.is_gap` reads `signal_quality`, never the label, which the engine holds at `no_signal` on
real ticks after a gap; `--arm-at` an absent segment is refused.

**Degraded contact is the ordinary state.** Even prepared and at rest, 2–3 of 4 electrodes; every active segment drops
below 2, and extended `good` is not achievable. `degraded` (2 of 4, contact ratio ≥ 0.4) is the regime every score
must work in and `poor` is the fault. **Nothing gates on `good`.**

### What each score is now

- **The amplitude terms were the strap.** Rest spread was 147 µV on one fitting and 23 µV on another for the same
  person at the same task, and a quarter of every focus and calm score was that. Removed; the raw-level path now
  serves only a bridge that reports no band powers.
- **Confidence is a signal-quality number, and calm is not in it.** It was 32% calm, so a stressed student was the one
  most likely to be discarded as `insufficient_signal`. It is now warm-up, contact, spectral stability and band
  presence, with a contact term that is 0 below the degraded line and **steps to 0.5 on it** — no linear weighting puts
  every `poor` reading under the gate while keeping `degraded` above it, since the two meet at 0.4, and a ramp from
  zero put two-of-four electrodes at exactly 0.50 and under the gate on any jitter. With the step, degraded clears the
  gate at zero spectral stability. `contact_ratio` rides on the payload.
- **The confidence rides in `raw.confidence`** — no column carries it. `engagement` did, and once `engagement` became
  the focus index the decider was still averaging it into the `eeg_channel` gate, which is a *focus* threshold: a
  disengaged student on good contact lost the whole EEG channel, ease-off included, while a focused one on a bad strap
  passed. The decider selects `focus, stress, raw`, never `engagement`.
- **`engagement` is the focus index** (beta/(alpha+theta), Pope's engagement), not the confidence — every Engagement
  tile was showing strap fit.
- **`engagement` is never drawn beside `focus`.** They are one number, so a second line, gauge, tile, archived series
  or prompt sentence reads as two measurements agreeing. The column stays; every surface shows focus alone, and each
  of the three charts has a test asserting the absence **in its screen-reader table**, since the sentence omits an
  empty series on its own. **Every reader serves `engagement` from the focus average, never from the stored
  `avg_engagement`**: that column was the confidence before the rescoring and a copy of focus after it, with no flag
  saying which, and the rollup outlives the raw rows. The flat ingest shape stores `engagement` as the posted `focus`,
  so no path can write a row where the two differ. **Archives written before the series was dropped keep it**, since
  nothing revisits an archive after close: `backend/rearchive_session_charts.py --apply` re-renders them and skips any
  session whose raw rows have expired, because there the archive is the last copy. Its cursor advances only past a
  session the run *finished* — set before the render, a failed render was passed over by the resume exactly as a
  failed read was.

### Gates, smoothing and the baseline

**Delta doubles on a blink**, gamma exceeds beta by 0.5 Bels on a clench and never at rest, and an artifact doubles the
raw spread. A tick that trips one **holds** the previous scores and enters neither the window nor the baseline — held
is a third state beside rejected and low, with `artifact_reason` and `samples_artifact` saying so. Bounds are relative
to running medians of **every usable tick**: referenced on admitted ticks only, the gate ratcheted and held a third of
resting ticks. No bound separates artifact from rest by better than ~3:1; 3.0× delta / 3.5× spread hold 12% of rest and
39% of artifact ticks, and a false hold costs one 250 ms tick.

**The ratios are smoothed over 4 s** on the sample clock before scaling; held and rejected ticks leave the smoothed
value alone.

**The baseline is 45 s of at-least-degraded contact, fixed for the session, on one scale with a 10 s ramp at the
latch — and it is taken from the first question, not from Connect.** Gating on contact was not enough: the
strap-settling period *is* degraded contact, with beta and gamma high from muscle, and replayed on the capture the
whole session read focus 0–14. The processor lives from stream start, so `eeg_poller` calls
`POST /api/v1/session/arm` when `record` flips true — `SignalProcessor.restart_baseline()`: discard, gather afresh,
keep the old centre in use until the new one latches, ramp to it. Under push, a `push/start` with a *new* session id
does the same (a repeat with the same id is a token refresh). Best effort and logged on failure: a sidecar too old to
know the route must not cost the session its rows. A rolling reference was rejected — a sustained state would decay
to 50.

**`reset()` keeps the baseline.** The stream manager calls it on every no-sample tick, which flapping contact does
repeatedly, so clearing it there made a strap slipping at minute 20 the session's new zero point through a path
nothing arms. Only `restart_baseline()` replaces it. **`stop()` calls `clear_session()`, not `reset()`**: through
`reset()` the next student on a shared station was scored against the previous one's baseline.

Three further gap rules follow from that reset: the baseline latches on *covered* seconds (a gap counts as one);
`reset()` keeps the time-windowed contact histories, the artifact gate's running medians and the two session counters
(cleared every fifth tick, 0 of 20 blinks were held); and the label's pending run survives a reset and ages out at 5 s
instead — cleared, contact flapping every other tick never reached four readings and read `no_signal` throughout. A
stalled sample clock counts as a nominal tick for the baseline's coverage *and the ramp* (coverage alone latched a
baseline the ramp never applied), and the baseline lists are capped.

**A label needs four consecutive readings** before the 3 s cooldown protects it; 90 of 133 `focused` readings on the
captures were the cooldown holding one spurious tick.

### Population bounds and score scales

Widened against the capture: **ln(0.15) to ln(2.00) for focus, ln(0.16) to ln(2.00) for calm.** Calm widened at
**both** ends so its midpoint, the pre-latch centre, stays at −0.57; raising the ceiling alone put the strap-settling
segment under the stressed line and eased difficulty on the opening questions. Focus's midpoint deliberately moved
down 0.49 Bels; only calm's is held.

**The label lines moved with the spans**: `focused` is focus ≥ 0.624 and `stressed` calm < 0.377, in **both**
`adaptation.py` and `signal_fusion.py`, pinned equal by a test on each side, so the Bels of movement each label needs
are unchanged. Left at 0.7/0.35 the widening made `focused` 61% harder on a capture where it was reached on zero
ticks. Both remain unmeasured against a task.

That re-anchors every stored value, so **`signal_mapping` writes `raw.score_scale` on every cognitive row** (2 on the
sdk calm source, 3 on the local one, per `SCORE_SCALE_BY_CALM_SOURCE`); rows without the key predate it. **The rollup
records the range seen each day** (`score_scale_min`/`score_scale_max`) and every rollup-backed payload carries
`score_scale: {min, max}` for its window. **Never a date**: the rollout is per sidecar process, as each student's
machine restarts, and `_scale_range` keeps "no row recorded one" apart from scale 1.

`ScaleNote` renders the caption — term trend, class trend (only beside a drawn line), class roster (class range, or
any one student's, since the outlier flag is computed on those numbers) and weekly summary tiles — **when the range
straddles the change**: a series on two scales is not one series and the chart cannot show where the step is. Each
wiring has a test with a mixed fixture, since the null branch passes with the element deleted.

**Scale 3 is a different unit, not a later version**: it moves stress and not focus, and runs on one student's
headband beside a classmate's on scale 2 at the same time — so `describeScaleChange` names which figures a range moves
and whether the split is a step in time (1→2) or two sources side by side (any range reaching 3). **A scale-3 row
whose stress is NULL is left out of the day's range while any row with a scored stress is present**, and only a day
with no scored stress at all falls back to reading such rows as scale 2 (they contributed only a focus). Mapping them
to 2 unconditionally made every local session read 2..3 on its own; not mapping them at all drew the two-source
caption beside an sdk day. A row with a NULL `raw` is scale 1 like a row with no key, so the null test comes first.

**The rollup reads `raw.score_scale` through `score_scale_of(jsonb)`, never a hard cast**: `raw` is client-supplied on
the push path, and a cast raised out of the cognitive INSERT, the first of three, so one posted sample aborted a
student-day's rollup — which the close swallows and the expiry job then refuses for ever, a student exempting their
own rows from retention with one request. `scripts/assert_signal_rls.sql` exercises that against a real stack, garbage
value included. That rollup read is the one stated exception to the cohort endpoint's consent bucketing: it selects no
reading.

### Malformed bands, and what a held tick records

A NaN or infinite value in a **ratio** band, or a **partial** band dict, is a **held tick** with
`artifact_reason: malformed_bands` and confidence at the floor: as an exception it read as a dead headband, as "no
bands" it was scored on the amplitude fallback above the gate, and a missing band defaulted to 0 Bels and scored. **A
NaN *delta* is not malformed** — it feeds only the blink gate, and holding on it pinned a session with four perfect
ratio bands at the midpoint. The artifact histories are fed by every usable tick whatever the bands say, since the
spread comes from the raw channels.

The snapshot serialises a non-finite *or absent* band as `null` (`BandData` fields are optional) or `/api/v1/state`
500'd on exactly that tick, and the mapper stores the row with its measurement columns nulled and the reason in `raw` —
**a held score is the previous tick's, not a measurement.** A push batch validates each sample on its own and reports
`malformed`, since a typed list 422'd every valid sample beside one bad one.

`raw.confidence` is dropped on a `contact_poor` row with the measurement columns — kept, four poor rows beside one good
one averaged focus 0.8 against confidence 0.36 and dropped the EEG channel — and the decider validates the **value**,
not just the container, since `raw` is client-supplied: a string 500'd every question and `true` claimed 1.0. `_raw()`
removes the client's value under a key the backend derived as `None`.

`samples_no_delta` and `samples_no_spread` count the usable ticks the blink and spread gates had no reference for, so a
recording whose detector never armed does not read as flawless.

### The raw spectrum settled alpha, and did not settle focus

Measured from the bridge directly: eyes closed, **TP9 and TP10 carry a 10 Hz peak 4.6× above the 1/f fit** that is
absent eyes open; the frontal pair does not, so the SDK's four-channel average could not see it. `services/eeg_spectrum.py`
is Welch over 2 s Hann windows on a 4 s buffer, per channel, a 1/f slope fit over 2–40 Hz **with 7–13 Hz excluded**
(fit through the band and the peak becomes slope), and calm is the mean log10 residual over 8–12 Hz at the temporal
pair. Closed against open separates at **AUC 0.92 at 4 s epochs**.

`EEG_SPECTRUM_SOURCE=local` scores calm from it on its own population scale (`CALM_ALPHA_RESIDUAL_*`, midpoint 0);
**the default stays `sdk` by decision** — one adult, three runs — and the local figure rides on every payload as
`calm_alpha_residual` either way, so a session on `sdk` still records what `local` would have read. On `local`, a tick
before the buffer fills **holds** calm rather than borrowing the SDK ratio: the two are different numbers on different
scales and one baseline cannot hold both. A signal-loss reset empties the buffer, since whatever spans a gap is two
recordings. The estimator is fed from the drain in `DeviceSession._loop` — every sample, since only `samples[-1]` is
scored. **The SDK alpha band did move on this run** (+0.24 Bels closed) because contact held at 3 of 4: it is not blind
to alpha, it is unreliable at the contact the product gets.

**Focus has no marker in this data, and `focus` stays the SDK ratio, documented as unmeasured.** Silent arithmetic
raised neither beta (AUC 0.38–0.43 against eyes open, i.e. *lower*) nor gamma; the only task effect was alpha
suppression at AUC 0.56. The beta ratio has now failed aloud, silently, on the SDK bands and on the raw spectrum. The
1/f slope itself separates closed from open more than any band does, which is why a raw band ratio mostly measures the
slope — carried unscored as `spectrum_slope`. Blinking produces a spurious 8 Hz "alpha" from the blink harmonic; the
delta gate keeps those epochs out of a baseline. Re-derivable with `EEGResearch/scripts/analyze_raw_capture.py`; its
AUCs are over adjacent epochs of one block each, so they describe that recording and are not estimates.

**Its label and its surfaces are held as they are until the second wearer's capture — don't relabel it in passing.**
Three options were weighed and rejected. A *rename* has nowhere true to go: the honest names make no claim a reader can
check, which invites them to invent one, and the readable alternative — engagement — is the same claim in a word this
file strips from every surface. (The opposite case to the `Confidence` bar, correctly relabelled *Signal quality
score*, where the number was well understood and only its name was wrong.) A *caveat under the tile* is the
anti-pattern `FacialRecognitionToggle` was retired for. And *removal*, the treatment `attention` got, is the real
option — with the teacher's focus-versus-accuracy panel first, since a correlation coefficient is the strongest claim
in the product about what this number means — but it cannot be done to focus alone: the **sdk calm sits in the same
evidential position** (eyes-closed alpha moved 0.02 Bels; beta and gamma fell instead, so it tracks muscle tone
relaxing), and `stress` is that number inverted. One decision about the EEG family's family-facing surfaces, gated on
the same capture plus a marker for effort that three attempts have not found.

What keeps it defensible meanwhile: focus reaches fusion through **one** door, the `focused` label (focus ≥ 0.624
*and* calm ≥ 0.5), which can only push difficulty **up**, is vetoable by heart and face, has no ease-off role, and is
close to unreachable at the contact the product gets. Nothing on a parent's or teacher's screen calls it effort, and it
must not start.

### What the local calm may claim

The estimator is fed only by a headband and refuses a buffer whose timestamp span is not a 256 Hz stream's — the
simulator's one sample per tick filled it with 256 s analysed as four. An artifact tick **poisons** the buffer until
its samples have left: the gate holds one tick, the window kept the blink for four seconds of estimates, and one blink
moved the residual further than the whole closed-to-open effect. `malformed_bands` does **not** poison it (a fault in
the SDK band dict, not the raw samples), the rate check compares the span between two stamps against the sample
*positions* between them (push admits an unstamped sample, so counting stamps refused a real buffer), and a poison
while still filling reports `artifact`, not `filling`.

**The stressed line is per calm source** — `STRESSED_CALM_MAX` in `adaptation.py` and
`EEG_STRESSED_CALM_MAX_BY_SOURCE` in `signal_fusion.py`, pinned equal by a test on each side. 0.377 was 0.311 Bels
below centre on the SDK span and 0.148 on the local one, where silent arithmetic then read stressed; the local line is
**0.25**. It stands only until the poison length and the pre-latch calm centre are decided against the second wearer's
capture — the table it came from was derived before the artifact poison and does not reproduce under it.

**Both alternatives exist as settings with the shipped behaviour as default** — `EEG_SPECTRUM_POISON_SECONDS` (4.0,
the buffer; 2.0, the Welch window) and `EEG_CALM_CENTRE_ON_ARM` (`keep`; `midpoint`, the local calm only — focus keeps
the no-step arm, and so does the sdk calm, which latches beside focus). **Both are read at import, inside
`StreamManager()`, so neither may refuse the boot**: `config.py` validators warn and fall back, and the estimator
floors a numeric poison at one sample, since 0 made it a silent no-op. `SignalProcessor` itself still raises on an
unknown centre, because a direct caller is code. `EEGResearch/scripts/replay_raw_capture.py --matrix` scores all four
on a capture in one run, so the decision is made against numbers rather than by editing code twice; it applies the same
artifact poison `DeviceSession._loop` does, under which the local calm is fresh on 18–21% of eyes-open and task ticks
and held past the 10 s cap on 40% of resting ones.

**Calm latches on its own coverage** over the ticks that had a value, with its own ramp, and keeps collecting after
focus has latched: latched with focus, one calm sample was the session's calm centre for good. `calm_measured` is false
on a placeholder — the opening fill, and after every gap, both write the same 50 a genuine residual of zero produces —
and the engine labels neither stressed nor focused on one. `calm_held_seconds` says how long a local calm has been
carried, and past `CALM_HOLD_MAX_SECONDS` the mapper nulls `stress` **and the engine labels neutral** — the constant
lives in both `adaptation.py` and `signal_mapping.py`, pinned equal by a test on each side, or the sidecar asserts a
learner state from a calm the backend has just declined to record.

**`focus_centred` and `calm_centred` say whether each score is on the session's own baseline yet or still on the
population midpoint**, and ride in `raw`: the local calm latch needs 45 covered seconds of ticks that *carried* a calm
and a poisoned tick carries none, so at the reference capture's artifact rate it takes minutes and can outlast a
session.

The decider reads `raw.calm_source` off the rows and **a window holding both sources has no calm opinion.**
`calm_source`, `calm_measured` and `calm_held_seconds` are client-supplied on the push path and validated by type in
the mapper (string; bool; finite non-negative number), the decider type-checks the source again before it is a set
element, and **a value present in the wrong type *withholds* stress rather than recording it**: `"false"` is not
`False` and `"150"` fails an isinstance check, and both read as a measured, fresh calm — the number the hold rule
exists to withhold. A posted list as the source 500'd the ingest and then every question until it aged out. A rejected
`calm_measured`/`calm_held_seconds` is named in `raw.calm_invalid`, or the nulled stress reads as an older sidecar that
never sent the key.

**A window whose calm is withdrawn — two scales, or every stress nulled by the hold rule — keeps its EEG channel**:
`eeg_channel` reads focus and confidence, applies the contact gate, and answers `neutral` with cause `no_calm`.
Withdrawing the whole channel on `calm is None` lost the read with it.

The push session end resets the heart tracker and clears the adapter's optical buffer without dropping the link, or the
next student's first window straddles the previous one's samples — and it runs only if push was actually running, since
the page fires `push/stop` from pagehide under pull too, and unconditional it wiped a live armed session's baseline.

`SignalProcessor` and `AdaptationEngine` take an injectable `clock` for the replay.

## Two columns are called stress and only one measures it

`cognitive_signals.stress` is `1.0 - calm`, written in `signal_mapping.map_eeg_to_cognitive`. There
is no `calm` column, so this *is* the EEG calm score, stored inverted. No independent quantity exists
behind it, and `infer_state` never reads it — it uses `calm_score` directly, the same number the other
way up.

`heart_signals.stress_score` is a measurement: autonomic arousal on a 0–100 scale, derived against the
session's own baseline, with its own quality gate and its own `calibrating` state. It is defined **on
heart rate alone** — RMSSD is an enrichment term, added when available and absent without changing
what the score means, because a score whose definition shifts when an input drops out is unreadable
across a session.

So: **never average them, never sum them, and never render both under one "Stress" label.** One is a
cognitive score with a sign flip; the other is a physiological measurement with a baseline. A tile fed
by whichever happens to be present would change meaning when a headband disconnects — rule 1 in its
worst form.

## Fusion is asymmetric on purpose — easing off wins, pushing harder defers

`Website/AdaptiveLearning/backend/signal_fusion.py` decides how hard the next question is, from
whichever of EEG, heart and facial are consented and present. **To raise difficulty every channel with
an opinion must agree; to lower it, any one trusted channel suffices.**

Keep it that way. A wrong ease-off costs one easy question; a wrong push costs a struggling student a
harder one, and the signals are least trustworthy exactly when a student is agitated. A brute-force
test asserts the property directly: adding a channel can make sessions gentler and can never make them
harder. **If that test fails, the change is wrong, not the test.**

Facial is the weakest input by design — it can withhold an increase, and can neither cause one nor
trigger an ease-off alone. FER+ is trained predominantly on adult faces and is least reliable on this
product's users: children, and children with learning disabilities. Its labels deliberately use a
different vocabulary (`negative`, never `stressed`) so no later edit can wire it into the ease-off
branch by matching on a label name. `EMOTION_MIN_CONFIDENCE` is a guess, not a measurement.

**Consent gates the read, not the result.** A revoked channel is never queried, and the tests assert on
which tables were reached — rule 4: an empty result cannot distinguish "asked and got nothing" from
"never asked". `_consent_flags` fails closed, like `_consent()` and unlike the reporting helpers.

**Difficulty is chosen in the backend, not the sidecar.** `question_policy` was removed: the sidecar
computed it every tick, it was persisted and displayed, and nothing read it to pick a question. Don't
add it back — the sidecar cannot see correctness, topic history or grade level.
---

# Privacy

## The network edge: who may read a response, what rides on it, how much may be sent

All of it is one block above the helpers in `main.py`, mirroring `EEGResearch/src/app/main.py`, which has had an origin
allowlist and a headers middleware since it was written; this backend had neither.
`backend/tests/test_network_edge.py` is the **first backend test to use `TestClient`** — every middleware here was
unreachable from the suite by construction before it, so anything added to this block needs a test there or it is
covered by nothing.

**CORS is an allowlist, and `allow_credentials` has to be false for it to mean anything.** It was `allow_origins=["*"]`
with `allow_credentials=True`, which Starlette serves by *reflecting* the asking Origin — a wildcard wearing an
allowlist's clothes. Credentials here would mean cookies and there are none: the bearer token goes in a header, which a
browser never attaches on its own. `ALLOWED_ORIGINS` defaults to the local frontend; methods are the four the API
serves plus OPTIONS, and headers the two `lib/api.js` and the push client send. A production deploy that forgets the
variable is refused at the edge on the first page load — loud, and the safe direction.

**Read a blank env list as unset, not as a list of one empty string.** `_env_list` is `_env_number`'s shape for text.
An empty allowed origin matches nothing, so the symptom is the whole frontend refused by a setting that looks
configured.

**The CSP says this server is not a document, and that is the honest policy rather than a weak one.** `main.py` serves
no HTML — no `StaticFiles`, no template, no `HTMLResponse` — so
`default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'` is exactly right for it. **The
`default-src 'self'; connect-src <supabase> <sidecar>` form is a *frontend* policy**: it describes what a page may
fetch, and this server has no page. That one belongs with the Vite build's hosting config and is still an open
decision. A test asserts the three HTML sinks stay absent, since the policy stops being honest the moment one appears —
**and it walks the AST, because a source scan cannot tell a use from a mention**: read as text it failed on the comment
beside the CSP, which names all three to explain why none is there. Same trap as `AccessibleChart.test.jsx`'s stated
blind spot, from the other side.

**`Permissions-Policy` denies the camera here, and that is not a mistake.** The product does open a webcam — on the
*frontend* origin, through the sidecar. A `camera=(self)` carve-out on an origin with no document permits a capability
nothing can use.

**FastAPI's own docs are exempt from the CSP and off in production.** They are real HTML pulling Swagger from a CDN, so
under `default-src 'none'` they render blank locally, which reads as broken tooling rather than as a policy working.
`_DOCS_PATHS` names them once, so the paths exempted are the paths switched off.

**The body cap is two caps, and the second is derived.** A single number would have refused real sensor data: a
cognitive batch at the full `INGEST_MAX_BATCH` of 500 with the fattest `raw.ingestion` block the sidecar sends is
**~637 KiB**, two and a half times the 256 KiB an ordinary endpoint gets. So `/api/signals/*` is bounded by
`INGEST_MAX_BATCH × INGEST_MAX_SAMPLE_BYTES` rather than its own constant — raising the batch bound must not start
refusing batches at the edge for a reason nothing in the ingest code mentions. A test rebuilds that full batch and
asserts it fits, so trimming the per-sample allowance fails against the measurement rather than against a comment.

**Both halves of the size check are needed.** `Content-Length` is what every client in this product sends, and checking
it refuses the body before a byte is accepted. A client that chunks its upload sends none — precisely the client this
exists for — so the middleware also counts what arrives. Pure ASGI rather than `BaseHTTPMiddleware`, because that one
reads the body to hand it on and the point is not to read it.

**The refusal has to be readable by the page that caused it**, so the size middleware is added first (innermost) and
CORS last (outermost). A 413 carrying no CORS header reaches the browser as a generic network error, making a size
limit indistinguishable from the backend being down. **`add_middleware` prepends, so *added last* means *outermost*** —
getting that backwards is silent.

Not here, deliberately: **no `TrustedHostMiddleware`** (the production host is an open decision, and an allowlist with
no known host either breaks everything or is a no-op), and **no HSTS** — one line in `security_headers` when the
hosting question is settled.

## Access control — check the relationship, not the role name

Endpoints serving student data read through the **service-role Supabase client, which bypasses RLS**, so the checks in
`main.py` are the only thing between a caller and another student's data. Use the existing helpers rather than writing
a new check inline — re-deriving the rule per endpoint is how the original `class_live` guard drifted into
`owner != user AND role != "teacher"`, which let any teacher read any class:

- `_verify_class_owner(class_id, user_id)` — only the owning teacher.
- `_verify_can_view_student(viewer, student_id)` — the student themselves, a teacher of a class they are enrolled in,
  a linked parent, or an admin (a **fourth relationship** rather than letting each admin path grow its own copy of a
  report query).
- `_session_or_403(session_id, user_id, columns)` — a session is **one student's**, so this is ownership and nothing
  weaker; no teacher or parent is admitted. It returns the row, which is the point: `record_answer` and `end_session`
  need the session anyway, and paying for a second query is why they were written with no check at all.
  `_verify_session_owner` is this with the row thrown away. **Check before you write** — `record_answer` inserted the
  answer row *first*, and `/end` stopped the poller before looking, so any student could forge answers into another
  child's session or end one mid-lesson.

Access is a **relationship**, not a path segment or a role claim. Don't namespace an endpoint under `/api/teacher/`
when parents legitimately read it too, and don't gate on `user_metadata.role`.

### Where a role gate must read it from, and why one column is not enough

**`user_metadata.role` is attacker-controlled.** The client sets it at sign-up and can rewrite it with
`supabase.auth.updateUser({data: {role: 'teacher'}})`, which talks to GoTrue and never passes through this backend.
`create_class`, `my_classes` and `link_child` gated on it, so any student could self-elevate. They call **`_role(uid)`**,
which reads `profiles.role`; `test_no_endpoint_gates_on_user_metadata` greps the module so a fourth site cannot appear.
It fails closed to `student`, since `_profile` degrades to a student-shaped dict on a failed read.

**Switching to `profiles.role` is only half of it, and it is the half that looks like the whole fix.** `profiles`
carries a `FOR ALL` own-row policy and `authenticated` held UPDATE, so that column was equally client-writable. **RLS
narrows *which rows*, never *which columns*, and a CHECK cannot express "not by you".** Only the grant can, and grants
are per-column for UPDATE and INSERT — INSERT matters as much as UPDATE, since with it alone a student could delete
their profile and re-insert it as a teacher. Self-service teacher sign-up is unaffected: `handle_new_user` is
`SECURITY DEFINER` owned by `postgres`, so it bypasses column grants. What changed is that the value cannot be edited
afterwards by the account it describes. `backend/tests/test_role_gates.py` asserts both halves — the code reads the
right column, and a migration takes the write away.

### The frontend reads the same column, through `GET /api/profile/me`

`AuthContext` used to derive its role from the claim, true while every role was chosen at sign-up and wrong the moment
one was not: an account promoted to `admin` in the SQL editor has no `role` in its metadata, so it rendered as a
student — while `/admin` itself worked, because `AdminGuard` asks the backend. That is the shape of the bug: the
authoritative check was right and every surface around it read a different source.

- **It is not in the `onAuthStateChange` callback.** `apiFetch` calls `getSession()` for the token, supabase-js holds
  an auth lock while dispatching, and awaiting it there deadlocks — the app hangs on a loader for ever. It lives in an
  effect the callback merely schedules.
- **Keyed on the user *id*, not the user object**, so a token refresh mid-lesson does not put the whole app back
  through a loading state.
- **A failed read falls back to the claim, not to `student`.** A blip is not a demotion; defaulting to the
  least-privileged role would drop every teacher into the wrong application whenever the API was down. The opposite
  direction to `_role` on the backend, deliberately: that one decides *access*, this one decides which nav to draw.

`loading` stays true until the role resolves, or the guards see `role === null` for a frame and render "this account
isn't set up" on every page load. **That makes this the one request in the app that may not hang, so it is the one that
passes `timeoutMs`.** A request that *fails* is caught; one that never settles leaves `loading` true for ever — an
infinite loader for every signed-in user. **A `.catch` is not a bound.** `timeoutMs` is **opt-in with no default**,
because a blanket one would abort `/api/students/{id}/learning-strategies`, which is bounded server-side and can queue
behind other waiters first. The bound covers the **whole call**, not the `fetch`: `getAccessToken` awaits
`supabase.auth.getSession()`, which goes to the network when the token needs refreshing, so wrapping `fetch` alone
leaves exactly the hang it was added to stop.

Login and Register navigate to `/` and let `HomeRedirect` choose rather than computing a home from the claim — they
each carried a second copy of the role-to-home map that `homeRoute.js` exists to be the only one of, keyed on the value
that does not know about `admin`. The claim survives only as the fallback, and nothing that matters may be gated on it.

### The *name* was the same bug, and it outlived the role fix

Nine surfaces derived a name from `user.email.split('@')[0]` while `profiles.display_name` was read on one page. The two
agree until the first edit and then never again, because sign-up seeds the stored name *from* the email prefix.
`AuthContext` was already fetching the row for `role` and discarding the name; it now exposes `displayName` (stored
name → claim → email prefix → null), so no surface derives a name of its own. **A save has to call `refreshProfile()`**
from both Profile and teacher Settings: the write is what makes every other surface stale. A blank stored name is
`null`, not a name, or the greeting addresses nobody.

Two traps met doing it. **`teacher/Settings.jsx` already had a `displayName`** — its edit field — so destructuring the
context's under that name is a *parse error*, and **a parse error deletes that file's tests from the run rather than
failing them**: the suite went quietly from 727 to 720 with zero failures. A totals check that counts assertions cannot
see it; count the **files** too. And `Settings.test.jsx`'s `useAuth` double had no `refreshProfile`, so the page threw
where the double was thin rather than where a bug was — make the double carry what the real thing carries.

**The teacher's roster had the same defect on its own read**, and is the worked example of rule 4. `Students.jsx` is
the one page that reads `profiles` straight through Supabase, and it named each row
`s.username || s.email.split('@')[0]` — **`profiles` has no `username` column**, in any migration, so the first branch
never fired and every row showed an email prefix. The search box had the same gap pointing the other way: it matched
email and id only, so a teacher typing the name on screen found nothing. It survived because `Students.test.jsx`'s own
fixture invented `username: 'ada'`.

Access-control tests live in `backend/tests/test_access_control.py` and run in CI.

## Consent — `signal_consent` decides what may be recorded

Three channels, named for the **sensor** rather than the signal derived from it: `eeg`,
`headband_optical` (heart rate today; the Athena's `OPTICS` packet also carries fNIRS, so a `_ppg_`
name would go stale), and `camera` — which covers expression **and** the rPPG heart-rate fallback. One
device, one decision: a heart-rate failover must never open a webcam the student declined.

**Everything defaults to false.** An absent row means the same as a row of falses, so there is no
backfill and an unconfigured student records nothing. `_consent()` fails **closed** on a read error and
carries `retrieved` so callers can tell "nobody consented" from "we couldn't find out".

**Withdrawal stops future recording and keeps what is already stored.** A revoked channel records
nothing further until consent is given again, and no past row is deleted or hidden. Withdrawal is not
erasure.

**Writes only through the backend.** The table has no insert/update/delete policy for anyone, so with
RLS on, PostgREST cannot write it whatever JWT it carries — including the anon key in the frontend
bundle. `main.py` is the enforcement:

- a student may only move a flag **true → false**; only a linked parent may move it back
- a **teacher may read but not write** — they need to see a channel is off, or a blank tile reads as a
  broken query, but consent is not theirs to change. Use `_consent_actor`, not
  `_verify_can_view_student`, which admits teachers
- `revoked_by` is surfaced as a **role, never an identity**, and is stored **per channel**. The row has
  one `updated_by` and the channels are revoked independently, so deriving the role from it would
  report a parent's later unrelated write as having made the student's earlier revocation

**RLS `WITH CHECK` cannot see the previous row**, so "off-direction only" is not expressible as a
policy — which is why the student gets no update policy at all rather than a narrowed one.

Writes are **conditional on the state they were decided against** (`.eq()` on each flag being changed)
and answer 409 if it moved. Read-then-write is not atomic, and the pair that races here is a student's
withdrawal against a parent's re-enable on the same channel — losing that silently means recording
against a refusal.

A parent turning a channel **back on** sets `parent_enabled_at` and raises `needs_student_ack`, cleared
by `POST /api/consent/ack`. A parent turning one *off* raises nothing. Discovering a resumed sensor by
noticing data reappear is not consent.

**That rule has to hold on both ingestion paths, and for a while it did not.** `/api/signals/*` has
called `_consent()` per request since it existed; the poller writes `cognitive_signals` directly with
the **service-role** client, so under `pull` a withdrawal stopped nothing. Now: `/api/eeg/start` refuses
**403** (not the 409 push uses — one says this student said no, the other says this deployment does not
work that way), and a running poller re-reads consent every `CONSENT_RECHECK_SECONDS`.
`eeg_poller.set_consent_check()` is wired from `main` at import and has **no default**: unwired,
`start()` raises rather than assuming yes, because an unwired deployment that assumes yes is
indistinguishable from a wired one.

Tests: `backend/tests/test_consent.py`.

## Recording needs consent **and** an open school year

`retention_window` is a single-row table (`enforced`, `starts_on`, `ends_on`, `timezone`) holding the
school year. Outside it nothing is recorded whatever consent says, and on `ends_on` the per-sample rows
are deleted.

**`enforced = false` turns the year off without turning the gate off.** It is for prototyping and for
deployments that do not run on a term, and it exists because the alternative was inventing a pair of
term dates — which produces a row indistinguishable from a real school year on the one table whose job
is to say when recording is permitted. The dates are nullable so an unenforced row need not carry fake
ones. Three states: **no row** — nobody decided, records nothing; **`enforced = true`** — a real year,
needs both dates; **`enforced = false`** — deliberately not gating on a term, records. Consent is
unaffected and still required.

Two fail-closed edges hold that apart from an accident. It is read as `is False`, never falsiness, so a
row predating the column — or one PostgREST returns without it, or with an explicit null — keeps the
gate on rather than being opened by the migration that added it. And `enforced = true` with no dates is
`unconfigured`, not unbounded: a half-finished edit must not be the most permissive state in the system.

**It fails closed in five different ways, and they are named separately.** `_retention_window()` answers
`open`, `not_enforced`, `before_year`, `after_year`, `unconfigured` or `unreadable`, and only the first
two record. "Inside the configured year" and "not gating on a year at all" look identical from the
recording side and are very different facts about a deployment. A typo'd timezone **denies** rather than
falling back to UTC, because a fallback moves every boundary by hours while looking like it worked, on a
value edited by hand twice a year. "The year hasn't started" and "the year is over" reach a parent as
different sentences; `_not_recording_reason` puts the window reason ahead of the consent one, or a
closed year sends someone to the consent screen to fix a setting that is fine. `_poller_status` follows
the same order, with its own machine-readable `stopped_reason` vocabulary (`school_year_ended`, …) — a
poller that is not running with consent intact and nothing saying why is the silent quiet week arriving
through the status endpoint.

**Never read the raw `*_enabled` flags to decide whether to record.** `_permitted_heart_sources` takes a
`_may_record` result and reads its composed `record_*` flags; hand it a bare `_consent()` dict and it
returns no sources at all, which is the safe direction for that mistake.
`test_every_recording_site_gates_on_the_window` derives the seven sites (three ingest endpoints, the
poller's three consent callbacks, `eeg_start`) and fails if one calls `_consent(` directly.

**The window gates recording only. Don't put it in `_consent()`.** That helper is read by the reporting
surfaces, the consent screen and the poller status, none of which should change answer because term
ended: gating there would report every channel off on the last day of school, so a parent could not read
the history that survives until the delete job runs — and it would read as a withdrawal, a claim about a
decision nobody made. `_may_record()` composes the two; `_consent()` stays pure and its raw flags ride
along beside the `record_*` ones.

**The timezone is the school's, not UTC**, for both the window boundaries and the weekly report's day
buckets. The last day of school ends at local midnight; against a UTC clock it ends mid-afternoon or
runs into the next day. Bucketing goes through `_school_day(ts, tz)`, never `str(ts)[:10]` — PostgREST
returns UTC, so slicing put a 4pm Californian lesson on the next day of a parent's chart.
`_weekly_signal_report` resolves `since` to midnight of the earliest *school* day too: `now - 7 days` in
UTC starts after that day begins wherever the school is behind UTC, so the oldest column silently
averaged only part of itself.

**`_school_timezone()` defaults to UTC where `_retention_window()` denies, and that asymmetry is
deliberate.** A wrong boundary while recording collects data nobody agreed to; a wrong boundary while
reporting moves a chart column by a few hours. So the gate fails closed and the report degrades.

The row is edited through the dashboard SQL editor. RLS is on with **no policies** and
`anon`/`authenticated` are revoked outright, so only `service_role` and the dashboard reach it. Both are
needed: RLS never filters `TRUNCATE`.

Tests: `backend/tests/test_retention_window.py`. Every other test file gets an open year from the autouse
`_school_year_is_open` fixture in `conftest.py` — without it they would pass by recording nothing, for a
reason unrelated to what they assert.

### The end-of-year delete refuses days nothing summarised

`expire_signal_rows()` removes per-sample rows from `cognitive_signals`, `face_signals` and
`heart_signals`; `sessions`, `session_answers`, `user_stats` and `user_math_performance` stay, because
academic history is not signal data.

**It skips any student-day with no `signal_daily_rollup` row**, per channel, and reports the count it
skipped. That check is the whole safety property: without it a bug in the rollup writer becomes silent
permanent loss on a fixed date, since the rows it takes are the only copy. With it, a broken writer
degrades to data that does not expire — visible and fixable. Asserted in `scripts/assert_signal_rls.sql`
**on all three tables**, because "the loop body is generated identically" is an argument about the code
and the channel mapping (`face_signals` → `emotion`) is the one pair whose names do not match.

The return value carries `hit_batch_cap` beside the two counts. Rows that were eligible but not reached
before `p_max_batches` appear in neither `deleted` nor `skipped_days_without_rollup`, so `skipped = 0`
alone does not mean everything eligible was handled.

**The cutoff is derived, never "today's date".** Days before `starts_on` always expire; once today in the
school's timezone reaches `ends_on`, everything up to and including it expires too. So the job is
idempotent and self-healing: a missed run completes on the next one, and a repeat deletes nothing new.
That is what makes a same-day delete with no grace period acceptable. No window configured means no
cutoff and nothing deleted.

Scheduled daily at 03:30 UTC via `pg_cron` rather than on one date, because scheduling a single day would
turn a missed run into a year of silence. `cron.schedule` upserts on the job name, so re-running the
migration re-points the job instead of creating a second one that would delete twice.

**Archived charts deliberately survive it.** Deleting per-sample rows on `ends_on` with no grace period
is only defensible *because* the rollup and the archived SVGs survive — they are the human-readable
record of the year. A job that took both would remove the thing that makes its own schedule safe.

`expire_session_alerts()` runs on the same `expired_signal_cutoff()` and deliberately with **no rollup
guard** — nothing summarises alerts and nothing should, so copying that guard would mean alerts never
expire at all. It is not batched (a couple of rows per session, not thousands per hour) and has its own
`pg_cron` job at 03:35 rather than a step inside `expire_signal_rows`, which would change that function's
return shape and the callers reading it.

## Erasure is the other request, and nothing triggers it by side effect

`erase_signals(user, channel, by, tz)` destroys one channel's stored signals for one student;
`signal_erasure` records that it happened. It runs **only** when a parent asks by name —
`test_changing_consent_never_erases` pins that, because wiring a revocation to the delete would turn the
reversible control into the irreversible one by a side effect nobody asked for.

**A linked parent only**, so *not* `_consent_actor`: a student may withdraw precisely because a parent
can undo it, and nothing undoes this. The request carries its own `confirm: true` — a dialog is not
auditable.

**Per channel, with the heart deletes keyed on `source`.** `camera` takes `face_signals` and the `rppg`
heart rows; `headband_optical` takes the `muse_optics` ones. Keyed on the table instead, a parent erasing
the webcam would destroy headband data they said nothing about.

**Derived data goes too, and the rollup is deleted before it is rebuilt.** `rollup_signal_day` has
`HAVING count(*) > 0` on every channel, so with the raw rows gone it inserts nothing and *leaves the
existing row standing* — averages of erased data outliving the erasure. Deleting first is what makes the
rebuild a recomputation. The rebuild is not optional either: `expire_signal_rows` refuses a day with no
rollup row, so a day left without one keeps its raw rows past `ends_on`.

**Archived charts go if they draw on the channel at all**, so `camera` takes `heart_rate` and
`stress_pie` with it — those mix both sensors into one picture and no pixel says which is which.
Over-deletion, preferred to serving a chart that still contains what was erased. Object paths are
**derived** in the function, never read from `chart_paths`, where they would be a delete list of the
writer's choosing.

The database half is one transaction; storage removal runs after it commits and is **counted, not
awaited** (`charts_failed`, plus a log line). Once `chart_paths` is nulled the objects are unreachable
through the product either way.

**The control** (`ConsentChannels.jsx`) is per channel and parent-only; a student sees that an erasure
happened but is not offered an action the backend would refuse. It is gated behind an "I understand this
cannot be undone" checkbox, **cleared whenever a panel opens** so an acknowledgement cannot carry between
channels, and sends the **channel** name (`camera`), not the switch key (`camera_enabled`), which 422s.
The confirmation states what goes *and* what stays, and that the setting is unchanged — erasing the past
while leaving the sensor on is the mistake most available to a parent. **That scope belongs in the
confirmation, not as standing copy**: a permanent disclaimer means the control's name overpromised, which
is what retired `FacialRecognitionToggle`.

**The tombstone is the fourth reporting state.** `erased_at` rides on each channel of the consent payload,
independent of `enabled` and `revoked_at` — a parent who erased and re-consented has a channel that is on
and a past that is gone. `_erasures()` fails **open** to `{}`, unlike `_consent()`: it decides only
whether a tile says "erased" or "no sensor", never whether anything may be recorded.

## Admin is a role, and three migrations are what make that safe

Admin is `profiles.role = 'admin'`, read through the same `_role` every other role gate uses, and set
from the dashboard SQL editor.

**It is a role rather than a side table only because the column is server-controlled on both edges**, and
both are load-bearing: one migration revokes UPDATE/INSERT on it from the client roles, another whitelists
`student|teacher|parent` in `handle_new_user` so sign-up cannot ask for it. Widening the CHECK without the
whitelist would have been a self-service admin signup — the trigger copies `raw_user_meta_data->>'role'`
straight into the column, so `signUp({data:{role:'admin'}})` from a console would have made an
administrator. The backfill migration repeats the whitelist for the same reason: it reads the same
client-supplied metadata.

`AdminGuard` asks `GET /api/admin/me` rather than reading a role client-side; it is a UI convenience, and
every `/api/admin/*` endpoint re-checks.

### `profiles` rows come from a trigger, and it was missing from source control

`handle_new_user` was written for an `auth.users` trigger that **no migration created**. That was
recorded and left, correctly, while `profiles` was decoration — it stopped being decoration when `_role`
started gating on it, since a missing row means `_profile` degrades to a student-shaped dict and a teacher
is refused their own classes with nothing to read. The migration creates the trigger and backfills the
rows, and is safe against a hand-made survivor: `on conflict (id) do nothing` makes a second firing a
no-op. Check for one under a different name after applying.

It deliberately **does not UPDATE existing rows**. `raw_user_meta_data` still holds whatever was typed at
sign-up, so refreshing from it would silently demote every administrator.

### Feature flags can only ever say no

`feature_flags` is key/value, read through `_FEATURE_FLAG_DEFAULTS`, which is the contract: **a key absent
from the table still has a value, and it is the value the system had before the table existed.** That is
what let the flags ship without changing behaviour, and it is why an unreadable table falls back to the
*declared defaults* rather than to off — a database blip is not a reconfiguration. The map is also the
whitelist: an unrecognised row is inert and a write to an unknown key is a 404, so a typo cannot create a
switch that reads back as set and controls nothing. Cached 30 s, cleared on every write.

**The three `recording_*` flags are ANDed into `_may_record`, never ORed.** A flag can withhold recording
and can never grant it, so no combination of switches records something a student declined — the same
asymmetry `signal_fusion` documents, and a brute-force-ish test pins it.

Every write lands in `feature_flag_changes`, append-only, written by the backend rather than by a trigger —
the backend already resolved the admin's identity to admit the request, so a trigger would be a second and
worse answer to that question. A failed audit insert never undoes the flag: it is already written, and
raising would invite a retry that changes nothing and audits nothing.

### `consent_enforcement_enabled` — the one switch that records without consent

Off, `_may_record` substitutes a fully-consenting answer. It is for prototyping, it is against the grain of
everything else here, and so it is **bounded rather than trusted**:

- **Expiry is evaluated on every read** (`_consent_enforcement_active`), not by a job that flips the row
  back. A scheduled job that fails to run leaves consent unenforced indefinitely, and not-indefinitely is
  the single guarantee this has to make.
- **A bypass with no `bypass_until` has already expired**, so a hand-edited row resumes enforcement rather
  than running for ever.
- **Disabling it requires an explicit duration**, capped at `_MAX_BYPASS_MINUTES` (4 h). No default — a
  default would be `main.py` choosing how long consent goes unenforced.
- **`_consent()` itself is untouched.** The bypass is a decision about whether to *ask*, not a claim that
  anyone agreed, so the consent screen, the reporting surfaces and the poller status keep showing what the
  family actually decided. `consent_bypassed` rides on the `_may_record` payload so a caller reporting
  *why* something is recorded does not say the student agreed.
- **It does not override the school year.**

### The admin read surfaces send counts and timestamps, never readings

`/api/admin/live-signals` answers "is data arriving" for every open session. **It selects `ts` alone**, so
the readings never leave the database rather than being fetched and dropped on the way out. An admin has no
relationship to those students entitling them to the values, and asking for less is a stronger version of
that property than filtering afterwards: the test asserts on the *select*, which is the only place the
difference shows.

It shares `_LIVE_WINDOW_SEC`/`_STALE_AFTER_SEC` with `class_live` — two sets of numbers would let one page
call a session live while the other called it stale — but **not its row reads.** `class_live` reads the
newest row per channel for the whole roster in **one** `latest_signals_for_sessions` RPC
(`_latest_signals_many`), and that must stay one call: an earlier version fanned a per-session read out
into a shared four-worker pool, and fanning this endpoint's outer loop into the same pool **deadlocked** —
the waiters and the work they wait on ended up in one queue. That pool is gone; do not reintroduce it.
`_admin_live_pool` (8 workers) still exists for this endpoint's per-session work, and nothing submitted to
it waits on anything else in it.

**Five states per channel, and they are not a scale**: flowing, quiet, stale, **never-reported**, and
**unreadable** (`seen: null`). The last two are the ones to keep apart — a session that never had that
sensor is a different fact from one whose sensor stopped, and both differ from a read that failed.

`/api/admin/health` reports `ok` / `degraded` / `unknown`, and **a check that could not run is `unknown`,
never `ok`.** `/api/admin/consent-summary` is counts only. `/api/admin/env-flags` lists the env-var
switches read-only, from a **named list** — `os.environ` also holds the service-role key, and a dashboard
that enumerated the environment would eventually render a secret.

Tests: `backend/tests/test_admin.py`. `conftest`'s `_feature_flags_are_default` pins the defaults for every
other test file, and **deliberately does not take `monkeypatch`** — requesting it from an autouse fixture
pytest orders early hoists `monkeypatch`'s setup ahead of `_join_poller_threads` and inverts their
teardown, which failed three unrelated tests in teardown for a reason nothing in their bodies could
explain. `pytest --setup-plan` shows the ordering directly.
---

# Reporting and UI

## A failed read must not look like a quiet week

Every reporting helper swallows its exception so one broken query doesn't blank a dashboard, and answers
200 with a default payload. Zero samples and a null average are then indistinguishable from a student who
genuinely recorded nothing — rule 1, and how surfaces came to report an absence in data that never loaded.
The payload has to separate all three states: **nothing recorded** (the read succeeded and found no rows),
**not requested** (`face_included: false`), **not retrieved** (`retrieved: false`).

`_shape_summary` carries both flags on every payload, so a consumer never treats "field absent" as a fourth
state. `_signal_summaries` returns `None` for a failed batch read versus `{}` for one that succeeded with
nothing to return. **Any surface that renders "no data" must consult these before saying so**, and a new
aggregate helper has to carry them the same way.

## The facial opt-out means the data is not read

`include_face=False` skips the query outright — `_weekly_signal_report` never touches `face_signals`, and
the summary RPCs take `p_include_face` so the aggregate reads no facial row either. **Nulling values on the
way out is not an implementation of this.** If there is ever no way to tell the database to skip the rows,
the correct answer is a blank tile, not a read — never fall back to a query that reads what the caller opted
out of. Assert on the **filter**, not the payload (rule 4).

That rule now belongs to consent, which is server-side and genuinely skips the read. The viewer-side switch
it was written for is gone: `facePref.js` was a read filter wearing the vocabulary of consent, and it needed
a disclaimer in its own UI copy — *"this does not switch a camera on or off"* — to stop being read as one.
**Needing that sentence was the signal the control was wrong.**

**The teacher's replacement deliberately breaks the rule, and says so.** `frontend/src/lib/viewPrefs.js`
(*"Hide sensor data"*, on `/teacher/students` and `/teacher/students/:id/report`) is **client-side only: it
fetches the data and does not draw it.** Acceptable there and nowhere else — the teacher is already
authorised by relationship, so it is decluttering, not a privacy boundary. Keeping it client-side also avoids
a second `include_face`-style axis through every reporting endpoint. A future reader will otherwise find a
filter that fetches what it hides and assume it is a bug.

It hides **all** sensor data, not just facial: heart rate comes from the headband as often as the camera, so
a facial-only filter would leave HR and HRV on screen and satisfy nobody. Live class monitoring and session
review stay outside it and deliberately don't render the switch — a control that silently changes a page it
is absent from is worse than one with a stated edge. And it never manufactures a reason: a channel off for
consent reasons still reads "not recorded — turned off on \<date\>" when the filter is off.

`_reportable_channels`' `want_heart`/`want_emotion` parameters survive but no client sends them. They default
to True and are not a privacy boundary; don't build one on them.

## A tile never says "no data" for something that was not recorded

`SignalPanel`'s `offLabel` picks between four states, and every tile goes through `valueOrReason` rather than
branching on the channel flag itself:

| State | Shown | Because |
| --- | --- | --- |
| consent withdrawn | `Off since <date>` | the date comes from `*_revoked_at` on the payload |
| consent unreadable | `Unavailable` | "the student turned this off" is a claim a failed read has not earned |
| read, samples arrived, none usable | `Calibrating` | a rejected window or a baseline still forming |
| read, no samples at all | `No sensor` | consented, but nothing produced anything |

Branching on the flag alone is the trap: it leaves `pct()`'s own `'N/A'` standing whenever a *consented*
channel produced nothing usable — the exact string the rule exists to stop showing, surviving in the case
least likely to be tested.

**EEG carries `eeg_enabled` + `eeg_revoked_at`, not an `eeg_included`.** The name is the point: the summary
RPCs have no `p_include_cognitive`, so that channel is *always* read, and withdrawal keeps what is already
stored — a student who switched the headband off last week still has true averages from before then. Calling
it `eeg_included` would claim a read was skipped that was not. Absent reads as **on** — defaulting to off
would tell every reader of an older payload about a decision nobody made. The batch RPC cannot carry it, so
`my_children` stamps it per child, like `emotion_revoked_at` beside it.

**A channel that is off keeps its tile.** Dropping the row tells a parent who switched a sensor off nothing
at all. The one exception is a payload predating the channel (`heart_included` absent rather than `false`):
there is nothing true to say about a channel the payload does not know about.

## A refusal is not an outage

`components/ui/LoadError.jsx` used to say *"make sure the backend is running"* for every failure. That names
a layer, and naming a layer sends someone to inspect it — so a teacher whose Question Bank filter was refused
went and checked a server that had answered perfectly well. It now picks the sentence from `error.status`,
which `apiFetch` attaches: **403** is "you don't have access to X" and gets **no Try again button**, since
retrying a refusal cannot work and offering the button is part of the false claim; **401** says the session
expired and keeps it; **anything else, including an error carrying no `status` at all**, keeps the original
wording, because a dropped connection genuinely is an unreachable backend. Callers pass nothing; a page wires
it by holding the error in the state it already had (`setFailed(e)` — every read of that flag was a
truthiness check).

**Name what was actually refused, not what the page is about.** `questions` is public-read, so a 403 on the
Question Bank can only ever concern the student filter — *"you don't have access to the question bank"* would
deny access to something the teacher can see behind the message.

## A roster row has `user_id` and `name` — not `id`, not `display_name`

`/api/classes/{id}/students` returns `{user_id, name, email, joined_at, ...}`. `Students.jsx` is the exception
that proves the rule: it reads `profiles` straight through Supabase, so its rows really do have `id`.

Getting this wrong in a `<select>` does **not** render a blank option. **An `<option>` with an undefined
`value` falls back to its own text content**, so `value={s.id}` over a label of `{s.display_name || s.email}`
sent the student's *email* to `/api/students/{id}/questions`, which resolves a uuid through
`_verify_can_view_student` — 403 on every pick, from a picker that looked right and named the right student.
Five tests passed over that filter because the fixture also said `id`/`display_name` (rule 4). Build a roster
fixture from what the endpoint returns, `email` included — without that field the failure is not even
representable — and assert on the **request path**, since both the option's value and its label come from one
row and a wrong key still displays the right name.

## Every chart goes through `AccessibleChart`, and a test enforces it

Recharts emits bare `<svg>` with no accessible name and nothing a screen reader can walk, so a chart rendered directly
announces as nothing at all. `components/charts/AccessibleChart.jsx` is the only place that may render one.

**The `sr-only` data table is a *sibling* of the `role="img"` wrapper, never a child.** WAI-ARIA's
presentational-children rule prunes every descendant role from an `img`, so a nested table is invisible to real
assistive technology — while being **perfectly visible to a jsdom test**, because Testing Library reads DOM attributes
rather than modelling the accessibility tree. That is the trap, and it is the opposite way round from how it first
reads: the table is not what the test cannot see, it is the *pruning*. `getByRole('table')` finds the element whether
or not a real reader would, so a hand-assembled call site has nothing to fail against, in the browser or in CI. That is
why this is a component rather than a documented recipe, and why `AccessibleChart.test.jsx` walks the source and fails
on any chart component rendered outside it. **It cannot see a hand-written `<svg>`** — the honest limit of a source
check, and where `Heatmap.jsx`, `BarGraph` and the cohort roster table sit.

**One `columns` spec drives the sentence and the table.** They were separate literals for one PR and disagreed twice in
it: a key named `bpm` where the rows carry `heart_rate_bpm`, so a visibly-plotted line announced "not recorded"; and
raw 0..1 ratios described with a `%` unit, announcing a session ranging 42–78% as "Focus 0% to 1%". Neither is visible
on screen and no test could catch them, because both surfaces were wrong in the same way at once.

**Scaling belongs in the spec, per page, because the pages differ.** `SessionReview` and `Live` plot raw ratios
against `domain={[0, 1]}` and need `scale: asPercent`. `SignalPanel` scales **the fields it names** into its chart data
and spreads every other field across untouched, so a column for one of those others needs a `scale` like anywhere
else. "This page scales on the way in" is the wrong unit of thought and has already cost one bug.

**A column must name a series the chart actually draws** — including when the `<Line>` is conditional, in which case
the column is too. A screen-reader user given a series no sighted reader can see has a different report, not an
equivalent one. Found **twice**: `SignalPanel`'s `engagement` column had no `<Line>` at all, so nothing on screen could
contradict its wrong scaling; `SessionReview` then kept `heart_rate_bpm`/`rmssd_ms` columns whose lines are gated on
`hasHeart`, so a session with no headband emitted "Heart rate: not recorded" on every row. The first fix was applied
where it was found rather than swept for siblings. **Check what the chart plots, and under what condition, before
copying a spec across.**

### A reader can hide a series, so the one-list rule is structural

`SeriesFilter` + `useSeriesFilter` put toggles above three line charts. **Two of them reach a parent as well as a
teacher**, because `StudentProgressReport` is shared by `teacher/StudentReport` and `parent/ChildDetail`: anything
added to those panels lands on both routes. Fine here — the control draws less, never more — but it is the question to
ask of the next thing added, and `viewerRole` is how a panel differs between the two.

Each chart declares **one list per series** (`key`, `label`, `unit`, `scale`, `colour`, `axis`, `name`) and derives the
`<Line>`s, the `columns` spec and the chips from it. That is the point rather than tidiness: "a column must name a
series the chart draws" was a thing to remember while the only gate was `hasHeart`, and it becomes a thing a teacher
does at will — a hand-wired column goes on announcing *"RMSSD: not recorded"* on every row of a session that recorded
it fine and was simply not being shown. Four things follow:

- **The colour is read from the same entry the line is stroked with**, applied inline rather than as a Tailwind class —
  the chip cannot drift from what it names, and a `bg-${…}` would ship no rule at all. **The backend reads that list
  too**: `test_chart_render.py` scrapes `colour` out of it to check the archived SVGs still use the palette the app
  drew, so moving those colours breaks a *Python* test. Its scraper refuses an empty result, because a shape change it
  cannot read is otherwise a check that passes while seeing nothing.
- **An axis mounts only while a *shown* series uses it**, and anything referencing an axis — `SessionReview`'s answer
  markers and its failover lines — is gated the same way. Recharts throws on a line naming an axis that is not there.
- **Everything off says so and offers *Show all*, rather than the last toggle refusing to move.** A control that
  silently does nothing is harder to understand than an empty chart that explains itself, and the message is distinct
  from "no history yet" and "could not be loaded": those are claims about the data, this is a claim about the view. The
  chart is not rendered there at all, so there is no empty axis and no column-less table.
- **No toggle for a series that cannot be drawn** — a control whose only outcome is the state already on screen.

**The hook stores what is *hidden*, and takes no series list.** Both halves are load-bearing. Storing the hidden keys
is what draws a series that becomes available *later*: these charts gain series as data resolves, and a shown-set
snapshotted at mount leaves the newcomer switched off with nothing explaining why. Taking no list is what keeps it
callable above `SessionReview`'s `loading` and `err` early returns — `hasHeart` is derived from loaded rows far below
them, so a hook needing the list was a conditional hook call and threw on all 28 tests in that file. The selection is
per mount and deliberately not persisted.

**Test it on `columnheader`, never on the summary sentence** — `describeSeries` drops a series with no readings on its
own, so an aria-label assertion passes whether or not the column is gated. And a `getByText('Focus')` that used to be
unambiguous now matches the chip *and* the table header; assert the role rather than loosening the query.

### Categorical charts, and the sampled table

**A categorical chart is `sliceSpec(label, rows, noun, {nameKey, valueKey, rowLabel})`, spread into the component.** It
returns the sentence, the rows and the columns together so the noun is written once — it names what the values count in
the sentence and heads the table column. As two literals they drifted: `Analytics` built its sentence from a remapped
`topicData.map(d => ({name, value}))` while its table read `topicData` with key `count`.

The table is **sampled to 60 rows** and says so in its caption. A 4 Hz channel over an hour is ~14,000 rows, built on
every render for a table nobody sighted sees — and unusable for those who do. A silently shortened one would claim the
session was shorter than it was. `sample()` returns the rows alone; "was it sampled" is
`tableRows.length < (rows?.length ?? 0)` at the one place that asks, since two return values could disagree. Note the
`?? 0`: `sample()` guards a nullish `rows` internally, so deriving the flag *outside* it moved that check away from the
guard and crashed on a comparison. **Moving a derivation out of a function moves it out of that function's guards.**

## `set-state-in-effect` is cleared, and the shapes that cleared it are worth reusing

Where the state is a reset driven by a prop changing — an acknowledgement cleared when enforcement resumes, a pulse
started by a new timestamp — adjust it *during render* against a `useState` holding the previous value, which React
re-runs before painting. Where it is a `loading` flag around a fetch, don't store one: keep the key the data in hand
belongs to (`loadedFor`) and derive `loading = loadedFor !== id`, so switching session or class raises the skeleton on
the render that changes the id and no previous subject's charts can be painted under this one's heading. A flag raised
by a *user action* stays a flag — `Sessions.jsx` sets it in the class selector's `onChange`, which is an event handler
and not an effect.

Both shapes have since bitten, and the corrections are the load-bearing half:

- **Derived `loading` needs a remount, not just a derivation.** `loading = loadedFor !== id` reads *false* when you
  navigate A→B→A: B's request is cancelled on the way out without ever advancing `loadedFor`, so returning to A finds
  it still saying `'A'`. `SessionReview.jsx` therefore keys the body on the id (`<Body key={sessionId} …>`), which
  resets every piece of session-scoped state at once — including the `err` that otherwise let a failure on A mask a B
  that loaded fine. `ChildDetail.jsx` does the same, and this is now the pattern for any page whose whole state
  belongs to one route param.
- **The render-time adjustment compares against the previous *render*, and that is not always the question.**
  `useValueChange` (`hooks/useValueChange.js`) is the extracted form and is right for `Flags.jsx`. It was wrong for
  `FlowDot.jsx`, which needs the last value it *acted on*: the pulse timer clears the live state, so a timestamp that
  goes transiently null and comes back unchanged reads as a change and flashes "fresh data" for data that is not new.
  Keep the acted-on value in its own state that nothing else clears. **A hook parameter nobody reads is the tell.**
- **Deriving state does not remove the need to cancel.** Every fetch that can be superseded needs a guard, and the
  slow ones are where it matters: `Sessions.jsx`'s roster read fans out per student, so a class switch let the
  previous class's response land last and repaint the list under the new class's name. It uses a generation ref
  rather than a cleanup flag, because the effect is not the only caller — the retry button is the other, and a retry
  is exactly when someone changes class rather than waiting.

## `react/jsx-uses-vars` is the only rule from `eslint-plugin-react` that is on, and it has to stay on

`no-unused-vars` cannot see JSX, so without it every identifier used *only* inside markup — `motion` from
framer-motion, an `icon: Icon` prop rendered as `<Icon />` — is reported as an unused import. That was **40 of the
65** errors the backlog held, all false, and the noise is what hid the real ones: the same sweep found one genuinely
dead `motion` import sitting among 33 identical false positives. The plugin's `recommended` config is deliberately
**not** extended — it brings a large ruleset that would add to the backlog rather than clear it.

`ignoreRestSiblings: true` goes with it, for the destructure-to-omit idiom (`const { x, ...rest } = obj` to build an
object *without* `x`, which is how the tests construct a payload predating a field). The binding is unused by design;
deleting it to satisfy the rule would put the key back.

With both, **`no-unused-vars` is clean and therefore load-bearing** — a hit is real dead code, so fix it rather than
adding it to the backlog.

## Muted text is `text-gray-600 dark:text-gray-400`, and a test does the arithmetic

Contrast is one of the few accessibility properties a source check can settle outright, so
`src/test/contrast.test.js` computes it rather than trusting a convention. Measured against the surfaces this
app paints (Tailwind 3.4 stock `gray`):

| | best surface | worst surface | AA 4.5 |
| --- | --- | --- | --- |
| light `gray-400` | 2.54 on white | 1.72 on gray-300 | fails everywhere |
| light `gray-500` | 4.83 on white | 3.28 on gray-300 | fails from gray-100 down |
| light `gray-600` | 7.56 on white | 5.13 on gray-300 | passes |
| dark `gray-500` | 4.16 on gray-950 | 2.13 on gray-700 | fails everywhere |
| dark `gray-400` | 7.93 on gray-950 | 4.06 on gray-700 | passes except on gray-700 |

**The dark half has to be added, not just the light half darkened.** 138 of the 146 sites named no `dark:`
variant at all, so they rendered gray-400 in *both* modes — where it already passes. A straight
`gray-400 → gray-600` substitution would have fixed light mode by breaking dark mode.

**A dark class in one ternary branch says nothing about the grey in another.** Three badges reading
`${on ? '… dark:text-indigo-300' : 'bg-gray-100 text-gray-400 dark:bg-gray-800'}` looked paired, so the grey
branch was darkened without a companion and dark mode went from 5.78 to **1.94** — worse than before the fix.
Resolve each branch separately, and model the fallback: an element with no `dark:text-` renders its bare colour
in dark mode too.

**Compute the ratio, never match a class name.** The first version of the test grepped for the literal
`dark:text-gray-500`, so `dark:text-gray-600` — worse, at 2.35 — went straight through a green suite, and so
did the regression above. Both were found by review, not by the test that existed to find them.

`text-gray-500` on white is fine at 4.83 and is left alone; it is only wrong on a `bg-gray-100` card (4.39). A
bare grey with **no** dark companion is a separate failure the same-element check cannot see — it renders
gray-500 on the gray-900 card at 3.67 — so the last test asks whether *the file* ever paints a dark surface.
Coarse on purpose: it separates a page whose cards flip from `MainLayout`, the permanently-white marketing
shell, where adding a companion would put gray-400 on white at 2.54. **Where the background comes from a
parent, no source check can see it.**

**`Adaptive.jsx`'s debug readout is the only exemption.** It paints `bg-gray-950` with no `dark:` prefix, so it
is dark in both modes and every rule above reverses inside it: gray-400 passes at 7.93 and gray-600 would be
unreadable. Its `gray-500` was raised *to* gray-400. Check for an unprefixed dark background before assuming a
grey is too light.

`text-[10px]` (36 uses) is **not** a contrast failure — WCAG sets no minimum font size — so it was left alone.
What mattered was the combination, and the tiny badges that were also sub-AA are fixed.

## A tone is a whole class name

The three consent notices (`ChildWithdrewBanner`, `ParentRestoredBanner`, `ParentLinkedBanner`) share
`NoticeBanner`. What each restated was the part worth having in one place: **a failed acknowledgement leaves
the banner standing**, because the person has not been told yet and a notice that dismisses itself on a failed
write is one nobody sees again. `onAcknowledge` clears whatever made the banner render; the shell owns the
pending flag and swallows the rejection. `busy` is cleared in a `finally`, not only on the failure path — a
caller that acknowledges without unmounting would otherwise be left with a permanently dead button.

**Tone classes are full strings in a map, never interpolated.** Tailwind decides what CSS to ship by scanning
source text for complete class names, so `bg-${tone}-50` renders markup pointing at a rule that was never
generated — a banner with no background at all, **in production only**, since the dev server is not what does
the scan. No test can catch it either: the rendered class string is identical and jsdom has no stylesheet.
Source review is the only check, which is why the map exists. The same applies to `Heatmap.jsx`'s colour scale,
which is a map of complete class strings rather than an interpolation.

## The daily rollup is written as sessions close, never at expiry

`signal_daily_rollup` holds one row per student per school day per channel (`cognitive|heart|emotion`), written
by `_rollup_session_days` at the end of `end_session`. Writing it continuously is what keeps it from being a
race against the end-of-year delete — generating it at expiry would make the one job that destroys data also
the first to read it.

**The aggregation is a Postgres function (`rollup_signal_day`), not backend code**, because a day holds
thousands of samples and the reporting path caps its reads — averaging a capped subset in Python would be
quietly wrong, and this is the copy that survives the delete. It **recomputes** rather than accumulates, so
closing two sessions on one day, or replaying a close, converges; an incremental writer would have to be
exactly-once, which nothing here can promise.

`_rollup_session_days` **never raises**: it runs last in `end_session`, after the writes that matter, because a
failed summary must not cost a student their session record and stats update. It rolls up every school day the
session touched (two if it crossed local midnight), bounded so a corrupt `started_at` cannot spin.

Averages are over **trusted rows only** for heart and emotion, matching what the weekly report publishes — an
untrusted reading is one the quality gate rejected, and averaging it here would smuggle it past that gate
permanently. `heart_sources` deliberately **includes** untrusted sources: its job is to explain a change in the
numbers, and a sensor whose readings were all rejected is exactly such an explanation. `trusted_sample_count` is
defined per channel (cognitive has no trust flag, so it counts rows that produced a measurement rather than the
nulled ones a poor-contact headband writes).

Its access rules differ from `retention_window`'s: the rollup carries a **read-your-own `SELECT` policy** and
`authenticated` keeps `SELECT`, matching the per-sample tables it summarises. There is no insert/update/delete
policy for anyone, so with RLS on, PostgREST cannot write it whatever JWT it carries — the only correct writer
is `rollup_signal_day`.

## The term trend reads the rollup and nothing else

`/api/students/{id}/signal-trend` answers week-over-week averages, and is deliberately **not** built on
`_weekly_signal_report`. That one reads the per-sample tables under `_REPORT_ROW_CAP`, which trims
**oldest-first** — right for seven days and wrong for six months, because the early weeks would come back empty
and read as a quiet term rather than as rows nobody fetched. `signal_daily_rollup` is a few hundred rows for
half a year and needs no cap. It is also the only copy that outlives `expire_signal_rows`, and a trend is the
surface most likely to be read *after* a year ends.

**Weeks are weighted by `trusted_sample_count`, and that is derived, not chosen.** `rollup_signal_day` writes
`avg(focus)` for cognitive and `avg(…) FILTER (WHERE trusted)` for heart; Postgres `avg()` skips nulls, so both
stored averages already have the trusted count as their denominator. Weighting by `sample_count` would divide by
rows the average never saw. A mean of daily means is the other wrong answer — it weights a 4-sample day like a
4000-sample one.

**`avg_stress` needs its own count.** The cognitive `trusted_sample_count` is
`count(*) FILTER (WHERE focus IS NOT NULL)`, focus and stress are derived independently, and the local calm's
hold rule made stress-absent-focus-present the *ordinary* row: a day of 4000 focus rows with 200 fresh calms
weighed its stress as 4000 and read 0.32 against 0.70. The rollup records `stress_sample_count`, and every reader
weights stress on it through `_stress_weight` in `main.py` and
`COALESCE(stress_sample_count, trusted_sample_count)` in the two cohort RPCs — the fallback is **per row**, for
rows rolled before the column, and is the old approximation on exactly the rows it always applied to.

`avg_rmssd_ms` still carries that approximation, because the rollup stores one count per channel and about one
trusted window in five is gated out of RMSSD. `avg_focus`, `avg_heart_rate_bpm` and `engagement` (served from
`avg_focus`) are exact. The error is between days, never within one.

**A week with nothing recorded is a gap, not a missing bar** — dropped, a fortnight off school renders as the
weeks either side sitting adjacent. Weeks are whole and Monday-anchored for the same class of reason: counting
back `weeks * 7` days from today leaves a part-week at each end that looks like a full one.

**A declined channel is filtered out of the query, not out of the result.** The first version read every channel
and dropped the declined ones in Python, on the reasoning that the alternative was three queries — a false
choice, since one `.in_("channel", …)` narrows the single query it already made. Assert on the **filter**, not
the payload. `_FakeSupabase` records every query it builds (`fake.queries`, each with `.filters`) so that
assertion is possible at all.

## The teacher analytics aggregate in Postgres, and one of them is a table not a chart

Five surfaces: a class topic heatmap, class accuracy per school day, a weekday×hour heatmap, a real last-active
column on the roster, and focus-vs-accuracy per student. Three Postgres functions behind them, all
`SECURITY INVOKER` and `service_role`-only — the backend resolves who owns the class before calling, so they are
only ever as safe as the check above them.

**They aggregate in SQL for the reason `rollup_signal_day` does, and the trap is `_REPORT_ROW_CAP`.** A class of
thirty answering fifty a day is 45,000 rows a month, far past the 5000 cap, and the cap trims **oldest-first** —
so a Python-side average would describe the recent tail while the early weeks read as a quiet term.
`class_answer_buckets` returns at most 720 rows for 30 days however busy the class, because it is bounded by the
*range* rather than by the answers.

**One function serves both the day trend and the time-of-day heatmap**, since they are two readings of one
grouping. It is still called once per endpoint rather than cached between them, so a failure in either cannot
blank the other.

**`last_active_for_users` exists because "newest row per student" has no PostgREST form.** One `in_` query ordered
by time returns the newest rows *overall*, which is one busy student's — the same limitation `my_children`
documents. That is why the column was absent rather than wrong. It is the greatest of two clocks
(`coalesce(ended_at, started_at)` and `max(answered_at)`): a student mid-lesson answered more recently than their
session began, and an open session must not drop out on a null `ended_at`. **Three states on the roster** — a
timestamp, `null` for never active, and `last_active_retrieved: false`. Collapsing the last two tells a teacher
the class has stopped working, which is both wrong and something they would act on.

**`focus_accuracy_for_user` pairs each answer with the nearest focus reading in the same session**, within
`_FOCUS_MATCH_SECONDS`. Same *session*, not same student: a reading from another session is a different lesson on
a different day. An answer with no reading in range is **dropped, not counted as focus 0** — sessions run with the
headband off, and a zero would drag exactly the unmeasured answers to the bottom of the correlation (rule 2).

**The correlation is withheld below `_FOCUS_MIN_PAIRS` (30) even when the database computed one.** `corr()` needs
two pairs and will happily answer from them; r over a dozen answers is noise, and it reaches a teacher as a single
objective-looking number with no visible denominator. The *buckets* are still returned below the threshold — a bar
chart of five bins shows its own sample sizes in a way a scalar cannot. Keep three outcomes apart: too few pairs,
enough pairs with a null `corr()` (no variance — every answer correct), and a real coefficient.

**EEG consent skips the query, and the test asserts on the RPC call rather than the payload.**

**`Heatmap.jsx` is a real `<table>` and deliberately not an `AccessibleChart`.** That wrapper exists because
Recharts emits a bare `<svg>`; a matrix has no series to plot — it *is* the table, and `<th scope>` headers let a
reader ask for one cell by its two headings, which the sr-only copy could not. The shading is an enhancement on
top of real markup.

**A cell has three states and two of them look alike in colour**: a number, `null` for a topic never attempted
(not shaded, and *not* the zero colour — a topic nobody was served is not one they failed), and a real 0%. Below
`min_attempts` the figure is still shown and marked thin; what is withheld is the confidence, since one answer
colours as strongly as four hundred.

**The topic grid's `cells` are a list aligned to `topics`, built server-side in one pass.** Two independently
ordered lists is the drift `AccessibleChart`'s single `columns` spec exists to prevent; don't re-sort either end.
The time-of-day grid aligns in the *browser* instead, because that payload is sparse — an hour a given day never
used is a real absence, not a missing row.

## The cohort panels weight across students, and the roster floor is a server-side gate

`GET /api/classes/{id}/cohort-signals` answers a class-wide signal trend and the per-student rows behind it, rendered
by `ClassSignalTrend` and `ClassSignalRoster`. One endpoint for both, because they answer one question together and
two fetches could disagree about when they were taken.

**Consent buckets the roster before anything is read.** `_reportable_channels_many` for the whole roster, then grouped
by `(heart, emotion)` flag pair — at most four calls. One call for the whole class would either read a declining
student's heart rows under a classmate's permission or hide the channel from everyone who allowed it. The RPC does
**not** filter consent itself, so passing it a mixed roster with one flag pair is the way to get that wrong. Heart is
`headband_optical OR camera` — the camera carries the rPPG fallback — so a test that declines the headband and leaves
the camera on has not declined heart.

**Weighted on `trusted_sample_count` at both levels, and the second is the one to watch.** The RPC weights across
students within a bucket; `_merge_cohort_trend` then weights across *buckets* in Python, because the buckets exist for
consent reasons and re-averaging their means would weight a bucket of one like a bucket of twenty. Verified against the
applied migration: two students on one day answer **0.7714**, where `avg(avg_focus)` answers 0.5000. A null daily
average contributes no weight rather than a zero — its count is still real, so it stays in `trusted_sample_count` while
staying out of the numerator's denominator, or a day of poor electrode contact drags the class below every student in
it.

**`_COHORT_MIN_STUDENTS` (5) is enforced in the backend, and gates on the roster.** Below it `per_student` is `null`
and the rows are never built — a client-side hide would leave them in the payload for anyone reading it. It counts the
roster, not the students who recorded something: a class of six where two wore a headband is still a class of six, and
gating on the smaller number would expose that pair exactly when they are most identifiable. The class trend still
renders; it is the aggregate the floor exists to protect.

**`_consent_many` / `_reportable_channels_many` are the batch forms**, and a roster is where they matter:
`my_children` keeps a per-student loop and can, since a family has a handful of children, but a class of thirty made
thirty sequential reads on a page load. They fail closed exactly as `_consent` does and *per student* — a failed read
denies **every** requested id with `retrieved: False`, since none of them was found out, while a student with no row
denies with `retrieved: True`. `_channels_from_consent` is the shared pure mapping, so the single and batch forms
cannot drift and disagree about the same student on two pages.

**One failed bucket fails the whole trend, and takes the roster with it.** Breaking out of the bucket loop skips that
bucket's totals call and every later one, so leaving `summaries` as `{}` reports students nobody asked about with zero
counts and `retrieved: True` — "recorded nothing", which renders as `No sensor` and is indistinguishable from a class
that left the headbands in the cupboard. Both flags go false together. The two reads still fail *independently* in the
ordinary case: a broken totals RPC leaves the chart standing. **And the row's `retrieved` has to be read by the tile,
or that fix stops at the API boundary** — without it the row arrives correct and renders `No sensor` anyway, from the
zero counts an unread row carries. The day count needs the same guard: `0` asserts the student recorded on no day at
all.

**`cellLabel` orders it, and the order is not the order the flags arrive in.** Consent unreadable first, then **a known
revocation, which beats the unread row**, then unread, then the sample count. The consent fields come from `channels` —
a *different query* from the totals RPC — so when that read fails the revocation and its date are still fully known;
reporting the outage there discards a fact we hold for one we do not, and `Unavailable` implies a retry might yield a
number that can never appear for a revoked channel. Folding `retrieved` straight into `consentRetrieved` gets this
wrong and looks right, because both spellings produce the correct answer in the two simple cases. **Four states means
the tests have to cover the *compound* cases**, not each flag alone; and a fixture built from a happy-path helper has
to null *every* average, or a leftover default renders a number where the test expects a reason.

**Both panels read the rollup, and that is load-bearing rather than tidy.** The roster started on
`student_signal_summary_many`, which reads the per-sample tables — the right source for the weekly report and the
parent dashboard, and the wrong one *here*, because this is the first place a rollup-backed panel sits directly beside
a raw-backed one. `expire_signal_rows` deletes the per-sample rows and leaves the rollup standing, so the pair would
have shown a full term of class averages above a table reading "No sensor" for every student in it — on a fixed date,
rather than because anything broke. `class_signal_student_totals` is the same aggregation as its sibling, grouped by
student rather than by day. `test_the_roster_reads_the_rollup_and_never_the_per_sample_tables` asserts on the tables
that must **not** be read — the two sources look identical while both hold the same data, which is every day of a
school year except the ones after expiry, so nothing about the numbers can see this. **The roster counts days
recorded, not sessions**, for the same reason: a session count comes from `sessions`, a table with a different
lifetime.

Its outlier flag compares against an **unweighted** class mean, deliberately a different number from the trend's
weighted one: this asks "is this student unusual among their classmates", where each classmate is one comparison
whatever their session length. Using the weighted figure would flag a student for sitting next to someone who recorded
all afternoon.

**A conditional `<Line>` needs a conditional column, and the sentence cannot test it.** Assert on `columnheader`.

Both panels honour `viewPrefs.js`'s "Hide sensor data"; the academic panels beside them do not, because that switch
hides sensor data and those measure answers. **It gates every series, not the heart one** — focus, stress and
engagement are EEG-derived and are as much sensor data as a bpm is. Gating only heart left the cognitive lines drawing
real values under a note reading "sensor data is hidden", stating the opposite of what the panel is doing. A test
asserting on that note passes either way; assert that no chart, no `sr-only` table and no numbers are on screen.

## Every session close goes through `_close_session`

**Four close sites** — `/end`, the stale-session sweep in `start_session`, `class_live`, and the background
`_sweep_abandoned_sessions` thread — and `conftest.close_sites()` finds all four. Don't hand-write a fifth: the
sequence was copied into each site and every copy drifted separately, none of them raising anything. The sweep
credited a `correct_answers` it had never selected (absent column → `None` → `or 0` → an honest-looking zero), so
every session of a student who shut the tab added its questions and *no* correct answers to their record;
`class_live` never ran the empty-session discard, so a failed pairing it closed stayed in History for ever; and
the credit, the rollup and the archive each shipped at different times as "the third close site to be missed".

**Order is load-bearing: discard first**, because a rollup of nothing and an archive of four empty charts are work
done for a session about to stop existing.

**`_close_session` stamps `ended_at` itself, and the stamp is a claim.** It used to sit at each call site above the
call, which left two things to get wrong per site and both were. `class_live` stamped and closed *before* stopping
its poller, so a tick could insert a signal row after the discard check had looked. And no site made the stamp
conditional, so two closes racing — a delayed `/end` against the sweep — both ran the whole sequence and both
credited the session's *cumulative* counts, landing every answer twice in the lifetime totals. `/end`'s read of
`ended_at` is not the guard; that read and the write are two statements. `_claim_session_close` is:
`is_("ended_at","null")` matches at most one row. **An empty update result is ambiguous** — it is also what a
client not asking PostgREST for the updated row returns — so it is confirmed by reading the row back, and only a
*different* `ended_at` counts as a loss. Guessing "lost" would skip the credit, rollup and archive for every close.

Stopping the poller stays at the call sites — it takes different ids at each — and **before the call** is the whole
of the ordering rule, pinned by `test_every_close_site_stops_the_poller_first`.

**The credit recounts from `session_answers`.** `questions_answered` is a denormalised cache written in a separate
statement from the answer row, and `_discard_if_nothing_recorded` already distrusts it. The credit did not, so a
session correctly *saved* from deletion by that re-check was then credited zero and the student's work never
reached the lifetime totals — permanently, since no later close revisits a stamped session. `_answer_counts` only
ever revises **upward**: rows fewer than the counter means a short read, and crediting less than a previous reading
loses work.

`_close_session` takes `closed_by`, defaulting to `CLOSED_BY_STUDENT`. The function cannot tell which site is
running and the difference is the entire content of the `session_auto_closed` alert. It defaults to the student so
a new site has to opt *in* to raising one — a wrongly-raised alert is worse than a missing one on a surface whose
value is that every row means something happened. `test_every_close_site_says_who_ended_the_session` partitions the
sites: each is either in `STUDENT_DRIVEN_CLOSERS` or must pass `CLOSED_BY_SWEEP`, so a new closer fails until
someone classifies it. No property of the source separates them — `/end` and both sweeps stop the poller and call
the same helper — which is why the list is by name.

The exhaustiveness tests share `tests/conftest.py:close_sites()` — a closer is a function that calls
`_close_session(` **or** writes an `"ended_at":` of its own. Both halves matter: the first catches a site drifting
away from the helper, the second catches a new site that hand-rolls a stamp. A second test pins the helper's own
contents; the indirection is only safe while both halves exist. **They catch a step being removed, not neutered.**

**Don't put a literal end-stamp key in an alert payload** — that scan reads such a key as a fourth close site,
which happened, and then happened again in the comment explaining it. The timestamps are columns on the session the
alert already points at, so `detail` carries none.

### Abandoned sessions, and two surfaces that were lying about them

Both original sweeps are **on demand** — `start_session` collects a student's strays when they next start one,
`class_live` collects a class's when a teacher opens the monitor — so a student who never comes back is collected
by neither. Found in production as sessions still open **two months** after they were started.

`_sweep_abandoned_sessions` is the third, run from a background thread started in `_lifespan`. **It is a backend
thread, not a `pg_cron` job, and that is not a preference.** Closing a session credits lifetime totals, writes the
daily rollup, archives four charts and raises the alerts; SQL can do none of it. A cron job stamping `ended_at`
would be a fifth close site that skipped all of it.

`_SESSION_ABANDONED_AFTER_SEC` (6 h, `SESSION_ABANDONED_AFTER_HOURS`) is **an age, not an idleness**, and the flag
is named for what it can support. A two-hour session with a student answering throughout is not abandoned by this
measure and is correctly untouched; `class_live` keeps its own much tighter `_STALE_AFTER_SEC` computed from real
last activity. This one only has to catch the session nobody has touched since June, so it errs long — closing a
live one would discard the question a child is part way through answering. `STALE_SWEEP_INTERVAL_SECONDS=0`
disables it.

Safe in several workers at once via `_claim_session_close`. **The thread must be joined**, like the pollers — it
prints, and a print during interpreter shutdown is a fatal stdout-lock abort.

Two surfaces were asserting things the data does not support. `Sessions.jsx` decided `live = !ended_at`, so an
abandoned session rendered a *pulsing* `● LIVE` badge indefinitely — three states now (live, `never ended`, done)
with `abandoned` derived in `student_sessions` so the threshold has one definition rather than a second copy in the
browser. And its duration counted to `Date.now()` for open sessions, printing `83132m 45s` for a student who left
within the hour; an abandoned session shows a dash, because we do not know when it ended.

## Session alerts are operations, never a judgement about a student

`session_alerts` is a teacher-facing feed of things that went wrong with a *session*: `session_auto_closed` (the
stale sweep ended it, the student did not) and `signals_missing` (EEG recording was permitted and no cognitive row
arrived). Read at `GET /api/classes/{id}/alerts`, rendered by `AlertFeed`.

**The scope is the feature.** `signal_fusion` produces a `stressed` label that no teacher surface consumes, and
routing it here was considered and rejected: it is an inference from signals this codebase already treats as weak,
and a timestamped event reads as more objective than a tile does. That is what retired `identity_confidence` and
the `attention` surfaces. **Every kind in the CHECK whitelist is checkable against the database without
interpreting a person; keep it that way**, and if that ever changes it needs a labelled reference first, not a
column.

**`_raise_session_alerts` runs after the discard and never raises.** After, because an alert about a session about
to be deleted goes with it on the cascade, and an empty session is not a fault worth anyone's attention. Never
raises, because it runs after the credit and the rollup and a session's record must not be lost because a
notification could not be filed.

**`signals_missing` gates on `_may_record`, not `_consent`, and on `is False`, not falsiness.** A student who
declined the headband is working exactly as configured; so is one whose school year has ended or whose recording
flag is off. Alerting on any of those trains a teacher to ignore the feed, and the first would leak a consent
decision as an incident. And `_session_had_signals` answers `True`/`False`/`None` — `None` is a failed count, which
must not become an accusation that recording is broken.

**A `try/except` around `_may_record` catches almost nothing, and that is the trap.** Both helpers behind it fail
closed by *returning*, not raising: `_consent()` answers `retrieved: False` and `_retention_window()` answers
`WINDOW_UNREADABLE`, and `_may_record` spreads both straight through as `record_*: False`. Read as a plain bool, an
outage is indistinguishable from a student who declined — and the outage is the likelier of the two.
`_recording_was_expected` reads `retrieved` and `window_state` rather than inferring from the composed answer, and
returns `None` for either.

**This generalises: anywhere a `record_*` flag decides whether to report a fault, the `False` is three different
facts.** Withholding is right for the unknown one — nobody can act on a database blip — but it has to be *logged*,
because that branch has no other trace. And the test has to assert on the log: the outcome is identical either way,
so a test checking only that no alert was raised passes against the bug.

**No acknowledge or dismiss, by decision.** Both kinds are about a session that has already ended, so there is
nothing to resolve; dismissal implies a triage workflow this product does not have, and the seven-day window
already bounds what is on screen. **An unrecognised `kind` renders as a visible unstyled row**, never dropped — the
CHECK makes it near-impossible, and if it happens a visible row is what gets it reported.

## Archived charts are the other thing that survives the delete

At every session close, `chart_archive.schedule()` renders the session's four charts to standalone SVG
(`chart_render.py`) and uploads them to the private `session-charts` bucket. With the rollup, these are what is
left of a school year once `expire_signal_rows` has run.

**Off the request path, and it never raises.** A storage failure must not cost a student their session close — the
session row, their stats and the rollup are all written by then. So the work goes to a two-worker pool and
`schedule()` swallows even a submit failure. That makes the log the only place a failure can surface, and it *has*
to surface: the window in which an archive can still be rebuilt closes on `ends_on`.

**`chart_paths` has four states and no column default.** A path, `null` for a channel that produced nothing, an
absent key for a chart never attempted, and column-NULL for a session the archive never ran on. `'{}'::jsonb` would
claim every pre-archive session was archived and found nothing, and `scripts/assert_signal_rls.sql` fails if a
default appears.

**Nothing has a policy on `storage.objects`, deliberately.** RLS is on and no policy grants any role anything, so
only `service_role` reads or writes — not even the student the chart is *about*: an object is fetched by URL, not
filtered by a query, so the access decision belongs in the backend where the relationship checks are, handed out as
a short-lived signed URL. The bucket is private for the reason no policy can fix later: **a public object URL, once
pasted anywhere, cannot be un-shared.** All of that is asserted against a real stack in CI.

Two smaller traps: the archive draws **untrusted rows too**, unlike the rollup, because it is a picture of what the
reviewer was shown rather than a number outliving its evidence; and `upsert` in `file_options` must be the
**string** `"true"` — storage-py passes those through as HTTP headers, so a bool arrives as `True` and a replayed
close 409s instead of overwriting.

**Reading them back is `GET /api/signals/session/{id}/charts`**, which resolves whose session it is, applies
`_verify_can_view_student`, and issues a signed URL per recorded chart with a 300 s TTL. Three states stay apart in
the payload, and a surface saying "no charts" has to consult all three: `archived: false` (the archive never ran),
`charts[name]: null` (that channel drew nothing), and `name in unavailable` (a path was recorded and the object
could not be read). It has deliberately **no `retrieved` flag** — unlike the reporting helpers it raises rather than
degrading, so a flag that is never false would be a state that does not exist.

**The object path is derived there, never read out of `chart_paths`.** That column is ordinary jsonb on `sessions`,
which carries a `FOR ALL` own-row policy — so a student could PATCH their own session row through PostgREST, point
it at another child's object, and the endpoint would sign it, having just correctly confirmed they own *this*
session. The stored value records **which** charts exist; it is not an address. The migration revokes the write as
well, but the endpoint must hold without it — a grant is one migration away from being widened back. The
consequence is that changing `object_path`'s scheme means migrating the objects, which was already true.

**A signed URL cannot be revoked.** It stays valid until it expires whatever happens to consent in between, so the
TTL is the only bound on a leaked one — that is the argument for keeping it short, not convenience.

**Storage does not cascade, so a deleted session orphans its SVGs — `sweep_orphan_charts.py` collects them.** There
is still no delete endpoint in `main.py`, which is exactly why a sweep rather than a hook: those deletes come from
the dashboard or a direct connection, where the backend never runs. Run it to report; `--apply` deletes.

**It deletes on *absence*, which is the dangerous kind of job**, and the guards are the point rather than the
sweeping. One failed read of `sessions` makes every object look orphaned, so: the read failing **refuses** instead
of proceeding, more than `max_orphan_fraction` (default 0.5) looking orphaned refuses, a path that is not
`{uuid}/{uuid}/…` is left alone, and `dry_run` is the default. **The bucket is listed *before* `sessions` is read**,
and that order is a guard too — read the table first and a session created in between has objects whose id is
missing from the snapshot, deleted as an orphan while its row sits there. Listing first can only be stale in the
safe direction. Each guard has a test and each test was checked by breaking the guard; the first version of the
read-failure test passed with the guard removed, because the fraction guard caught it and its message also
mentioned sessions.

An orphan is not a leak — `/charts` resolves the session row before signing, and the bucket has no policies — so
this is storage that should not exist rather than data anyone can reach. It stops being fine when account deletion
becomes a feature. **Objects for a session that still exists are out of scope on purpose**: `expire_signal_rows`
leaves the archive standing deliberately, and a sweep that "corrected" that would remove the thing that makes a
same-day delete defensible.

## The answers table shows the topic and the option text, never the ids

A `session_answers` row carries a question *id* and a `selected_index`, and `SessionReview` rendered exactly that —
a truncated uuid and the bare number `2`. Both true, neither usable.

`/api/signals/session/{id}` embeds the question on the answer
(`select("*, questions(question_text, options, correct_answer, subject, difficulty, figure, ccss_standard)")`) — one
query however many answers, named columns so a later addition to the bank does not start reaching the browser.
**A read path that names its columns has to name every column a surface renders**, which is how `figure` and
`ccss_standard` each had to be added; `/api/questions` uses `select("*")` and needed nothing. **PostgREST
left-joins the embed**, so an answer whose question has since been deleted arrives with `questions: null`; the
answer still happened, so the row is still shown and says why it cannot expand.

**`questions.correct_answer` is text, not an index.** Comparing it against a position marks the wrong option on
every question whose answer is not stored in order, so `isCorrectOption` compares *values* (trimmed,
case-insensitive) with a numeric fallback for a row that holds an index anyway. And `options` is unschema'd
`jsonb` — `optionList` accepts an array or an object and yields `[]` for anything else, which renders as "options
were not recorded" rather than crashing on `.map`. Chosen and correct are spelled out as words next to the glyph,
not left to colour and a `✓`.

## An answer is recorded by the backend, and the topic comes from the question

`Adaptive.jsx` had no `/api/sessions/{id}/answer` call at all — only `Practice.jsx` did — so every question
answered on the adaptive path was counted in `localStorage` and nowhere else. `session_answers`,
`sessions.questions_answered`, `user_stats` and every report built on them read zero however long a student
practised, while the page's own Topic Accuracy panel showed figures: two records of one afternoon, one of them
private to a browser.

**The question id is what made it possible.** `add_question_to_supabase` returned a bool, so the generated question
reached the page with no id and there was nothing to put in `session_answers.question_id`. It now returns the id —
**and returns the existing row's id on a duplicate** rather than False, because answering a question the generator
has produced before is exactly as real as answering a novel one.

**`_record_topic_attempt` derives the topic from the question row, never from the caller.** The client has to be
trusted about correctness; letting it also name the topic would let a page credit one subject for work done in
another, and `user_math_performance` is what the adaptive engine reads to choose what to serve next. It never
raises: it runs after `session_answers` is written, and a topic lookup failing must not turn a recorded answer into
"that answer could not be saved".

**It is one statement in the database** (`record_topic_attempt`). It was four sequential round trips on the hottest
path in the product, and the last two were a read-modify-write with no lock — two answers together both read the
same counts and the second overwrote the first, losing attempts silently. `ON CONFLICT DO UPDATE` incrementing the
*stored* value removes that rather than narrowing it. It returns the topic **name**, which `/answer` hands back to
the page so one figure moves; nothing holds an id-to-name map, so returning the id would cost a second query. The
arithmetic is asserted in `scripts/assert_signal_rls.sql` — the backend suite drives a fake client and can only
check that one call is made with the right three arguments. **Its PGRST202 is the deploy-ordering trap in its worst
form**: the helper swallows exceptions by design, so code deployed ahead of the migration stops attributing
anything with no symptom but the numbers not moving. It logs that case by name and cites the migration.

**Topic accuracy is read from `user_math_performance`, not from the browser.** It was
`localStorage.accuracyStats_<uid>` — the only panel whose numbers were not the database's. It disagreed with the
dashboard on the same screen, started from zero on a school computer, and nothing server-side could correct it: a
parent erasing a channel left the figures standing in the child's browser. The client-side upsert is **deleted, not
merely unused** — the backend owns that table now, and a client upsert would overwrite real counts with one
browser's memory. Its `Number(v) || null` also turned every genuine zero into a null (rule 2), which is why the
table sat empty while the panel showed numbers.

**There is no "Reset stats" button any more.** Against localStorage it cleared a browser key; against
`user_math_performance` the same button deletes a student's academic record with one click and no confirmation.
Erasure here is a parent-only, confirmed action.

### A roster surface reads once for the roster, never once per student

`_profiles_many`, `_topic_performance_many`, `_open_sessions_many` and `_stats_including_open_session_many` are the
batch forms; `class_students`, `my_children`, `class_live` and `leaderboard` use them. The stats half was batched
first and the profile lookup was left in the loop beside it, which is the shape to watch for. One deliberate
exception: `my_children` still reads the five most recent sessions **per child**, because "top N per group" has no
PostgREST form — one `in_` query returns the newest five overall, which is one busy child's five.

## The two model-backed panels on a report page

Both follow the same shape: a deterministic answer that is always available, a feature flag deciding whether a model
gets a chance to replace it, and the four bounds. **Both are on demand and never auto-fetched** — a teacher opening
a class of thirty would otherwise spend a model call per page.

### Strategies

`/api/students/{id}/learning-strategies` always has a rule-based answer; `strategy_llm_enabled` (default **on**)
only decides whether a model gets a chance to replace it. Off, the endpoint never opens a socket — which is what CI
and any deployment without a local Ollama should do. Every failure path degrades to the rules rather than erroring.

The default was `false` for a long time and nothing ever flipped it — it is admin-only — so every deployment without
an admin who enabled it had this pass silently doing nothing: every response `source: "rule-based"`,
indistinguishable from the model being tried and always failing. The migration that flipped it also flips the
already-seeded live row, **guarded on there being no recorded `feature_flag_changes` row for the key**, so a
deployment where an admin deliberately turned it off is not silently overwritten. Turning it on still needs a
working provider underneath — `_llm_strategies` catches every exception and falls back, so a misconfigured provider
produces the exact same symptom as the flag being off.

**Tests must pin this flag explicitly, not rely on the suite's default.** The autouse `_feature_flags_are_default`
fixture reads live from `_FEATURE_FLAG_DEFAULTS`, so flipping the production default flipped it for the whole suite
in one step — several tests started actually attempting the model call, one via a genuine ~20 s
`STRATEGY_LLM_TIMEOUT` wait per test. Every test whose point is the rule-based path, access control, or rate
limiting pins it off, even where the assertion happened to pass either way.

Model output is untrusted text: parsed, length-bounded, stripped of markdown emphasis and list markers, and run
through a clinical-term filter, with anything failing validation falling back to the rules. Extend
`_validated_strategies` rather than rendering raw output.

**The panel is on the teacher report as well as the parent one, and the copy is the only thing that differs.** The
endpoint is gated on relationship rather than role, so a teacher could always ask for this advice and had no way to
see it. `viewerRole` ('parent' by default, and for any value the panel does not recognise) picks the framing;
`_llm_strategies` and `_validated_strategies` are untouched. **The heading stays "At-Home" on both**: the prompt
says *"you are helping a parent support their child's maths practice at home"* and the rule-based fallback says
*"ask your child to explain one solved problem out loud"*, so a classroom-sounding label would claim the model had
been asked for something it was not.

On the teacher page it is **behind "Hide sensor data" with the charts**, because the advice *is* sensor data in
prose — the rule-based list says *"stress indicators ran high this week"*, and the model pass is handed the same
averages. The whole panel goes rather than its individual lines: the advice mixes topic accuracy with signal
readings and nothing downstream can separate them, and asking the endpoint for a signal-free list would change the
advice rather than hide it. **Assert on the Generate button's absence, not the heading** — hiding a heading over a
live button satisfies a heading check and none of the point.

### Chart summary

`POST /api/students/{id}/chart-summary` describes a student's report charts in plain sentences, flagged by
`chart_summary_llm_enabled`. **That is the fourth copy of the bounds block** — ingest, generation, strategies,
this — and consolidating the four is a standalone change rather than a rider on a new endpoint, because the other
three are reached into by name from their tests (`main._strategy_hits`, `main._STRATEGY_LLM_POOL`).

**The model is handed the finished sentences, not the aggregates.** It is asked to rephrase, never to interpret, and
that is what makes numeric fidelity checkable at all: every number it may use is already in front of it, so one that
is not is an invention. `_validated_chart_summary` rejects the whole reply on any numeral not in the allowed set.

**The allowed set is read out of the deterministic sentences, never enumerated from the basis fields.** Enumerating
was the first shape and rejected *correct* replies in two ways a reader would not predict: the sentence prints a
rounded heart rate where the basis holds a fractional one, and a revocation date puts a day number on screen that no
basis field carries. Reading the text the prompt actually sends closes the whole class, and makes drift between the
two impossible — the same reason `AccessibleChart` drives its sentence and table from one spec.

**What it does not check is that a number is attached to the right measurement.** A reply that swaps the focus and
stress figures uses only allowed numbers and passes. That is the residual hallucination risk on this endpoint and it
is not closed; closing it means parsing the reply back into measurements, which is a second implementation of the
sentences being parsed.

**The reply must have exactly the baseline's number of points.** A range let a reply drop one silently, and the
likeliest one to go is the channel-absence sentence — the single point whose whole job is to say something is
missing.

Four reads sit behind one response (the weekly aggregate, the rollup-backed trend, the academic totals, the topic
figures) and **each reports its own `retrieved`**. Collapsed into one, a summary missing only its trend sentence is
presented either as entirely fine or as entirely broken. Three consequences that were bugs first: `sessions` comes
from the *signal* aggregate, so a failed signal read leaves it at 0 and printing it reports a quiet week for a query
that never ran; an empty trend is indistinguishable from a student's first week, so a failed trend read must not say
"only one week so far"; and `_topic_breakdown` swallows its exception and answers `[]`, which most callers degrade on
identically — the strategies endpoint falls back to generic advice — but which here becomes the *assertion* "no topic
has been attempted yet". **`_topic_breakdown_with_state` is the form that reports the read**, split out rather than
added as a parameter so a caller that did not know to ask for the flag cannot drop it; reach for it wherever an empty
list would become a claim.

**Zero weeks and one week are different facts, and `_trend_direction` returns a dict for both.** It answered `None`
for each, so a student part way through their very first session — raw rows, so a focus average, but no rollup row
yet, so no week at all — was told that one week had readings. The rollup row is not written until the session closes,
so that state is ordinary rather than an error. **A helper that computes a count and returns it only on the success
path cannot be asked the question the count answers.**

**But the sentence for it names no cause, and the first version did.** A first session is one way to reach zero
weeks; a rollup writer that failed on every day in range is another, and so is a set of rolled days all carrying null
for that series. The read succeeded in all three, so nothing can tell them apart — and *"from this session's own
readings"* contradicted the session count two sentences above it whenever one of the others was the real one.
**Where a branch exists precisely because the code cannot establish a cause, its sentence may not supply one**; state
the observable ("no week has a reading for it yet") and stop.

Channel absence is ordered as `cellLabel` is on the cohort roster and for the same reason. `engagement` is never
named — it is the focus index, and a sentence naming both describes one measurement as two agreeing ones. The heart
channel gets **no trend**: `_CHART_SUMMARY_TREND_MIN_DELTA` is written for the 0..1 ratios focus and stress are
stored on, and against bpm the same number is a twentieth of a beat.

`ChartSummaryPanel` mounts on both report routes, and on the teacher route is behind *"Hide sensor data"* with the
charts — a stronger version of the reason the strategies panel is: that list mentions sensor readings in passing,
where this panel's whole job is to state them.
---

# Question generation

## Learning preferences live on `profiles`, and difficulty is a bias

Three columns: `difficulty_bias`, `session_duration_minutes`, `practice_reminders`. They were
`localStorage.al_prefs`, written by the Preferences tab and read by nothing — the backend picks the difficulty and
cannot see a key in one browser's storage.

**`difficulty_bias` is a shift, never an absolute difficulty**, and that is a safety property rather than a
simplification. `_shift_difficulty` applies it on top of what the model chose from the student's accuracy history,
and `LLM_topic_decider` overrides it *downward* whenever the fused signal says stressed — the same asymmetry
`signal_fusion` documents. Storing "always hard" would store a value the ease-off rule has to contradict, and a
setting the system routinely ignores is worse than one that does not exist. It is why the control offers three
options and not four: medium and adaptive would both mean no shift.

**Bounds are stated twice on purpose** — Pydantic on `UpdateProfileRequest` and a CHECK in the migration — and they
must agree, or a value that passes one and fails the other surfaces as a 500 from the client library instead of a
422 naming the field. The CHECK is a **range**, not the four durations the UI offers, so a fifth button is not a
migration.

**Duration is advisory.** The page asks between questions; nothing ends on a timer. A session closed mid-question
discards an answer a child was part way through giving. **And its clock starts at the first question, not at
Connect.** Under pull, `toggleHeadband` creates the session before anything has been asked, so a clock keyed on
`sessionId` charged the 12 s scan, seating the electrodes and every reconnect against the student's planned
duration. `fetchQuestion` starts it, beside `armRecording` and for the same reason — a paired headband is not a
lesson. Push never had it, since `toggleHeadband` skips session creation there. The test costs two 22 s waits and
they are not padding: the reminder is checked on a 20 s interval and the tick the clock *starts* on reads ~0
elapsed, so a short settle passes against the bug. `Adaptive.jsx` now has a `finishSession` — before this it never
called `/end` at all, so an adaptive session stayed open until the stale sweep on the student's *next* start.

**`practice_reminders` is a dashboard banner and is named for that.** There is no push infrastructure — no service
worker, no VAPID, no scheduled fan-out — so "Notifications: daily reminders to practice" described a system that
does not exist. The banner needs *both* reads to have landed before it renders: derived from a failed
`/api/sessions`, it tells a child they skipped a day they did not skip. Its "today" is the **browser's local day**,
deliberately not `_school_day` — that helper buckets recorded data against the school's timezone, and this is a
nudge about the student's own afternoon.

### A run of correct answers pushes difficulty up on its own

`_decide_bias` in `LLM_topic_decider`: at least `PERFORMANCE_PUSH_MIN_ANSWERS` (3) of the session's last ten answers
at `PERFORMANCE_PUSH_ACCURACY` (70%) or better shifts up — **and the newest `PERFORMANCE_PUSH_RECENT_CORRECT` (2)
must be right**, because the aggregate cannot tell a rising student from a falling one: 7 of 10 is 0.7 whether the
misses were the first three or the last three, and pushing a child who has just failed three in a row is the harm
the asymmetry exists to prevent. `get_session_performance` keeps the order as `recent` (newest first) for that — its
`desc=True` is what makes `recent[:2]` the newest two, and the fake in its test sorts by the flag so that direction
is pinned. A caller without it gets no push.

Also required: the control on Auto, the fused label not `stressed`, and no channel having withheld an increase
(`FusedState.increase_withheld`, the facial veto, carried on every fused state past the ease-off step). It used to
need a `focused` reading at the moment of choosing, and on hardware that is a state a student cannot hold: five
correct answers at grade 1 stayed on easy throughout, because every decision landed on `stressed` (a loose strap) or
`neutral`.

**A run of misses vetoes a push from either source.** Correctness is the one channel here with no quality gate, so
three straight misses is a trusted opinion that the student is falling, and every channel with an opinion must agree
to raise; a focused reading over that run is the false-focused case the asymmetry exists for. No answers yet is no
opinion, and focused pushes. The asymmetry is untouched — stressed still eases whatever the answers say, and a manual
Easier/Harder still wins — and `test_decide_bias.py` brute-forces it.

**`start_session` prewarms at the student's bias, not 0.** `QUEUE_SIZE` questions are generated before the first
answer and served first, so a hardcoded default there makes the setting do nothing for the opening of every session.

### A practice test's length is a prop, and flashcards have none

`PracticeSetup` offers 5/10/15/20 and hands the number to `Practice` through `onStart(session, count)`, which passes
it to `PracticeTest` as `questionCount` (default 10, the value it was a module constant at). **Nothing is sent to
the backend** — generation is one question per request, so the count is only ever a client-side stopping rule.

Two differences from Adaptive's question goal, both deliberate. There is **no "No limit"**: Adaptive's number raises
a dismissable banner beside a Finish button, and a test has no manual-finish affordance, so it must always auto-end.
And the picker is **hidden in flashcard mode** — a deck ends on "Done", at any point, so a count there would name a
limit that does not exist.

## Every model call goes through `llm_client`, and the provider is a setting

`backend/llm_client.py` is the only place either provider is reached. Fourteen call sites used to import `ollama`
directly, so a provider switch was fourteen edits and the bounds below had nowhere to live. Count the `generate_text(`
sites rather than trusting a number here — today it is the seventeen generators, the decider and the strategies pass.
Two of the decider's belonged to `parallel_topic_and_difficulty_calculation`, which spent *two* model calls on what the
live path does in one and was reachable from nothing; deleted with its sole caller, since dead code that bills twice
per question is a trap for whoever wires it up next.

**A question served costs exactly two model calls**: the topic-and-difficulty decision, and the generation. Each has
its own three-attempt loop, so six is the worst case — but the decider's loop is *around its own call only*, so a
retried decision never re-runs a generation. `CLAUDE_MAX_RETRIES` defaults to **0**, against the SDK's 2, so nothing
multiplies underneath: the call sites' loop is the one worth keeping, since it also rejects a *well-formed* response
for being bad JSON or the wrong shape.

**`LLM_PROVIDER` defaults to `ollama`.** A fresh checkout must not begin billing an Anthropic account; a deployment
opts in with `LLM_PROVIDER=claude` and `ANTHROPIC_API_KEY`. Neither package is imported until its branch is taken.
**`start.ps1` skips Ollama when `backend/.env` says `claude`** — it ran unconditionally, so a Claude deployment still
started `ollama serve` and would pull a multi-gigabyte model nothing calls.

**`temperature` is not a parameter of `messages.create` in `anthropic` 1.x, so the Claude branch sends no sampling
parameter at all.** It went with the 0.x → 1.x major version along with `top_p` and `top_k`; passing it raises
`TypeError` *before a request is built* — every question, from the first call. The API's own default is 1.0, exactly
what `CLAUDE_TEMPERATURE` defaults to, so the hot path asks for nothing and cannot be refused for asking. A caller
wanting something else goes through `_claude_sampling`, which puts it in `extra_body`. **That path is unverified** and
degrades safely, but a strategies response reading `source: "rule-based"` against a working key is the first place to
look.

**A test double must not be more permissive than the thing it stands in for.** This survived every review and a green
suite because `_FakeMessages.create` took `**kwargs`, so `test_llm_client.py` pinned a request shape the SDK cannot
accept and the one assertion anyone would have trusted could not fail. It now validates every kwarg against
`inspect.signature` of the **installed** SDK, read at call time rather than copied into a list that ages.

Verification stops at the network boundary without credits, and that boundary is worth reaching: **a `400` carrying a
`request_id` proves the request was built, sent and validated — a `TypeError` proves it never left the process.**

### The four bounds

| Bound | Setting | Why the existing one was not it |
| --- | --- | --- |
| Per-call deadline | `GENERATION_LLM_TIMEOUT` (30 s) | The SDK's default is **ten minutes**; a prefetch worker blocked that long never refills the queue |
| Concurrency | `GENERATION_MAX_CONCURRENCY` (8) | `_prefetch_active` bounds *per user*, so the peak was however many children pressed start at once |
| Per-student volume | `GENERATION_RATE_LIMIT` / `_WINDOW` (60/min) | The queue bounds calls *in flight*, not calls *over time* |
| Waiting callers | `GENERATION_MAX_WAITERS` (30) | The fourth bound, and it was missing |
| Spend | `GENERATION_DAILY_CALL_LIMIT` (2500/24 h, Claude only) | Nothing bounded it; free against a local model |

**The spend ceiling counts calls, and a question served is two of them.** It was 5000, justified as "eightfold
headroom" on ~600 generations a day — comparing a call ceiling against a question count and overstating it by two.
**Size it against the workload, not against whichever example is written down**, and against the worst case:
`max_tokens=2048` at $5/MTok makes one call cost up to ~$0.0102, so 2500 is ~$25/day where the ~$0.0023-per-question
average suggests ~$2. It bounds neither tokens nor a restarting process (in-memory, so several uvicorn workers
multiply it and a crash-loop defeats it). **Claude-only** on purpose: a ceiling against a free local model would
refuse a child a question to protect nothing.

**`GENERATION_MAX_WAITERS` is the threadpool bound, and it is not the concurrency one.** That one bounds calls *in
flight*; this bounds callers *blocked waiting to become one*, and only the second protects the app:
`_generation_slots.acquire(timeout=…)` blocks in the caller's own thread, and FastAPI runs these sync endpoints on
anyio's shared ~40-slot threadpool — so at a concurrency of 8 a class of thirty starting together puts twenty-two
requests to sleep in threadpool slots and `/api/signals/*` queues behind them. The per-student rate limit does not
help: that counts one student over time, this is thirty at one instant. Latent while the prefetch queue absorbed it;
`QUESTION_QUEUE_SIZE=0` made the inline path the only path. **Both** generation endpoints take it.

Measured against a real server with `scripts/load_test_generation.py`, which runs uvicorn and fires a class at it.
Nothing is billed: only the *network peer* is faked, so the semaphore, the budget arithmetic and every refusal are the
shipped code. Three findings:

- **The waiter cap *subsumes* the concurrency cap, it does not add to it.** `_generation_waiter` wraps the whole call,
  so it bounds requests **in flight**; the semaphore bounds model calls inside that. A check written the obvious way
  asserted 8 + 12.
- **The threadpool is only starved past ~40 in flight.** anyio's default limiter is exactly **40** threads (measured),
  shared with every other sync endpoint. At 30 waiters a probe on `/api/topics` stayed at 31 ms; at 60 in flight its
  **worst** probe was **11.9 s**, at 80, **23.9 s** — while **p50 and p95 stayed under 25 ms in every run**. A
  percentile-only report shows a healthy service that is intermittently hanging for twenty seconds. **Watch the max.**
- **The cost is refusals, and how many depends entirely on arrival.** 30 students, 2 s per call, at the old cap of 12:
  **40% served on a simultaneous start, 87% over 10 s, 100% over 30 s.** A synchronised start is a teacher saying
  "everyone start now", and on it 18 of 30 got a 503. **The cap is now 30**, which serves the whole class and costs no
  extra model calls, only threads and waiting, since a refused student generates nothing. Not 40, because anyio's pool
  is 40 and every other sync endpoint draws from it.

**The 503 carries `Retry-After: 5`, and `apiFetch` honours it** — for **GET only**, so a retry can never replay a side
effect; both generation endpoints are GETs, which makes that free. Bounded at two retries and clamped to 10 s, because
the header is a request from the server and not an instruction. **Jitter is the load-bearing half, not a refinement**:
every browser refused in one burst holds the *same* `Retry-After`, so honouring it exactly reforms the burst one round
later. `jittered()` is full jitter over `[0, delay]` — a tight band around a common centre is still a herd. **A retry
added anywhere else needs the same treatment.**

**Take it through `_generation_waiter()`, never a bare acquire/release pair.** The refusal raises `HTTPException`
*inside* the guarded block, so a hand-written release is skipped on the path most likely to run, and a
`BoundedSemaphore` acquired with `blocking=False` turns leaked permits into generation being off for the life of the
process.

**The budget covers the whole call, so time spent queueing comes out of it** — the model call is charged the
*remainder*, and a caller that queues its budget away is refused rather than started with no deadline left. Charged
twice, one caller blocks for nearly double what it asked for. It belongs here rather than at a call site, because this
is where the queueing happens. Tests on both must assert `<=` the budget, never `==` it.

`_ensure_queue` submits to a pool sized to `GENERATION_MAX_CONCURRENCY` rather than spawning a daemon thread per
question. **A submit that fails must roll the in-flight count back**, because `_prefetch_worker` owns that decrement in
its `finally` and a worker that never starts never runs one — the count would stay raised for the life of the process
and that student's queue would never refill again. Swallowed rather than raised for a separate reason: `_ensure_queue`
runs *after* the response is assembled, so letting a failed refill out turns a served question into a 500.

**`QUESTION_QUEUE_SIZE` defaults to 0 — prefetching is off.** A queued question is billed when *generated* and earns
its cost only when *answered*, so any depth above 0 pays for the unanswered questions of everyone who closes the tab —
precisely the rows `expire_old_questions` collects. Free against Ollama, which is why it was 2 and unconditional
before. The cost of 0 is latency. An env var so a deployment can raise it once the per-question cost and the real
abandonment rate are known, without a deploy.

**A test that reads `QUEUE_SIZE` instead of pinning it goes vacuous at 0**: `assert len(submitted) == main.QUEUE_SIZE`
becomes `0 == 0` after submitting nothing. Any test whose point is prefetch *behaviour* must
`monkeypatch.setattr(main, "QUEUE_SIZE", n)`.

### Refusal, not fallback

**On breach the answer is to refuse — `GenerationUnavailable`, surfaced as 503, never a fallback.** Serving from the
bank or a cheaper model would change what a child is asked with nothing saying so (rule 1). 503 rather than 500
because a ceiling is a decision this deployment made. The prefetch worker is the one place a refusal is *silent*, and
that is safe because it is invisible by construction: the queue stays short and the next question is generated inline.

**An API that cannot be *reached* is a 503, not a 500, and the message names the base URL.** `generate_text` catches
`anthropic.APIConnectionError` (which `APITimeoutError` subclasses) and re-raises it. Unclassified it was a 500 with a
200-line traceback, and the student's page said *"make sure the backend is running"* while the backend was running.
**`AuthenticationError` is deliberately excluded** — a different branch, and a bad key is a misconfiguration that must
stay loud rather than read as a passing outage. That is the line between classifying and swallowing.

The URL is in the message because it is the whole diagnosis when it is wrong: a stale `ANTHROPIC_BASE_URL` in the
*user's Windows environment* gives exactly `WinError 10061`, and took three rounds to find because "Connection error."
names nothing. Inherited at process start, so clearing it needs a new terminal.

### The Claude branch constrains its replies with a schema; the Ollama branch does not

`extract_json` hunts a JSON object out of prose because `llama3.1:8b` wraps replies in fences and preamble — a property
of *that model*, carried across to Claude untouched, so the retry loop went on absorbing malformed JSON from a provider
that can be told not to produce any. `question_schemas.py` is one schema per topic, sent as `output_config`.

**It replaces no code-level check, and that is the whole caveat.** `grade_appropriateness`, `question_consistency`,
`SCENARIO_VARS`, the scenario-grade checks and the bounded solvers all still run. A schema constrains the *shape* of a
reply, never whether the question is solvable, in band, or consistent with the data it will be scored against. It is
enforced by the provider and **only on one branch** — Ollama sends no schema, so a dev run exercises the unschema'd
path. `extract_json` stays for that, and because a reply truncated at `max_tokens` is the one malformed-JSON path left
on Claude.

What it buys beyond fewer retries: `scenario` is pinned to an enum of the **one** scenario selected, so the
wrong-scenario class becomes unrepresentable rather than merely rejected. Geometry's `variables` keys and the scenario
enums are **derived** from `SCENARIO_VARS` and the block tables, never restated — a second copy of a scenario's keys is
how the schema and the solver drift.

**Two JSON Schema keywords are refused by this endpoint, and neither is guessable from the spec.** Both found by
sending a request and reading the 400:

    For 'array' type, 'minItems' values other than 0 or 1 are not supported
    For 'object' type, 'additionalProperties: object' is not supported. Please set it to false

So `angle_solvers.SCENARIO_ARITY` **cannot** be expressed and stays a runtime check, pinned by a test because every
other shape here is constrained and a reader would assume this one is too. Probability's two bag scenarios return
**`None` rather than a schema**: their `items` maps invented category names to counts, and a schema listing every key
*except* the one carrying the data would be accepted, constrain nothing, and read as covered.
`test_no_schema_uses_a_keyword_the_api_refuses` walks every schema for both, because a violation is a 400 on the first
question of that topic. **A generator added without a schema keeps the `extract_json` path silently**, which is why
`test_every_generator_sends_a_schema` is an exhaustiveness check.

**Switching provider does not invalidate the checks below it, but it does invalidate every measured rate.** Those
checks are code and provider-agnostic — that is why the rules were moved out of prompts. Every "measured on
llama3.1:8b" figure below describes the **Ollama** path. Before a deployment runs on Claude, redo the sampling:
**count how often each fail-open check *engages*, not just how often it fires.** A check whose input it can no longer
locate reports a perfect record while doing nothing, and that has already happened here. Label new figures with the
model.

## `grade` reaches a prompt rebuilt from its number, never as the caller wrote it

`grade` is interpolated into **nineteen** prompts — `Student Grade Level = {grade}` in `LLM_topic_decider`, and one
`a {grade} student` line in each generator — and every one of those strings is client-supplied.
`GET /api/generate-question?grade=` is a query parameter with no request model behind it; `PUT /api/profile/me`, the
two class endpoints and `POST /api/practice-sessions/start` all declared it a bare `str | None`. A value carrying a
newline closes the line it sits on and opens an instruction of its own, in a prompt whose whole job is to be
followed.

**Escaping it is the weaker answer and is not what is there.** A grade is not free text: the only thing any consumer
wants from it is the number `grade_levels.grade_number` already reads. So `grade_for_prompt` hands the prompt a label
**rebuilt from that number** — `CANONICAL_GRADE_LABELS`, fourteen fixed strings, plus `UNKNOWN_GRADE_LABEL`.
Nothing the caller wrote survives, so injection is unrepresentable rather than filtered for. **Assert membership of
the closed set, never the absence of a payload**: an absence test passes against a filter that strips one sequence
and misses the next.

**The labels round-trip** (`grade_number(CANONICAL_GRADE_LABELS[n]) == n`), which is why the substitution is
behaviour-preserving — `_allowed_topics`, `grade_band` and every generator's `GRADE_OVERRIDES` key on the number and
never on the string. Break the round-trip and the grade gates move with no other symptom. Two dropdown labels are
relabelled on the way through ("Highschool" → "9th Grade"), safe for the same reason.

**Applied at two chokepoints, not nineteen**: `question_generation` is the sole dispatch point to all seventeen
generators, so sanitising there covers eighteen of the sites and the decider covers its own. That is sound only while
it *is* sole — a test walks the module's AST and fails on a generator called from anywhere else.

**The edge checks (`validated_grade`, on all five entry points) are the second layer and are not what stops an
injection.** They keep an unreadable grade out of the column, off a teacher's class list and off a profile badge. The
first draft was a length cap plus "does it parse", and `"5th Grade\r\nOUTPUT"` cleared both at seventeen characters:
**a cap bounds how much can be said, never whether a second line can be started.** It now refuses control and format
characters and the line and paragraph separators (`Cc`, `Cf`, `Zl`, `Zp`) that `\n` is not the only spelling of.
`test_grade_prompt_injection.py` keeps three short payloads from being simplified away, and one long single-line one
keeps the cap from reading as redundant.

**A backend test must not hold a `main` class object from collection time.** `test_consent_gates_polling` calls
`importlib.reload(main)`, which rebinds every class in the module — so a `@pytest.mark.parametrize` capturing
`main.CreateClassRequest` gets a stale object, and anything keyed on it raises `KeyError` in a full-suite run while
the file passes on its own. Parametrize by **name** and `getattr(main, name)` inside the test.

## Question generation can be grounded in a lesson plan

`lesson_plans` holds curriculum text keyed on `(topic_name, grade_band)`, at the same `early`/`middle`/`upper`/
`advanced` granularity the generators' own `_grade_band()` uses — not per exact grade, since one lesson plan already
covers a band. Public read, like `math_topics`/`questions`; written only via the dashboard, since it is reference
content the backend never mutates.

`lesson_plan_context.append_lesson_context(prompt, topic_name, grade_band)` is the one-line call site wired into
every generator, right after the grade-magnitude block. Cached with the same 30 s TTL and `time.monotonic()` pattern
as `_feature_flags()` (a lesson-plan edit lands within the TTL, not on the next restart), and clamped to 2000 chars
before it reaches a prompt — dashboard-authored text is still bounded like every other prompt input here.

**The Supabase client is created lazily, on first lookup, not at import.** `main.py` imports `LLM_topic_decider`,
which imports the generation modules, which import this one — so eager `create_client()` ran ahead of `main.py`'s own
`RuntimeError` for missing credentials, and a misconfigured deployment saw a bare `KeyError` three imports away
instead of the clear error `main.py` exists to give it.

**A cell has four ways to contribute nothing, and they are named.** `_lookup()` returns `(text, reason)` —
`NO_ROW` / `BLANK_ROW` / `READ_FAILED` / `NO_CREDENTIALS` / `FOUND` — and each logs its own line. All degrade
identically to the difficulty/grade heuristics, which is right (this is prompt grounding, not a consent or access
gate), but they are very different problems: a content gap somebody has to write, a half-finished edit, an outage,
and a misconfigured process. Nothing in generation branches on it, so this is diagnostics, deliberately.
**`READ_FAILED` and `NO_CREDENTIALS` are not cached**, unlike the other three: caching an outage would keep answering
`None` for the full TTL after the database came back, and credentials can be loaded later in a process's life.

**Don't scrape third-party worksheet sites into this table.** Vendors gate real content behind membership and hold
copyright on what isn't; their topic taxonomy for a grade also doesn't line up with this product's topics. Write
original objectives per topic/grade_band instead.

### What a lesson plan may ask for

**A lesson plan must describe question shapes the generator can actually emit, and the limits are tighter than the
grade band.** Objectives are prompt text, so anything they invite the model will attempt — and the solver then scores
it, correctly or not. Read off the code, then confirmed by generating: `algebra` takes `solve(...)[0]` and splits on a
single `=`, so one linear equation with one solution; `probability` has three scenarios (one named category, its
complement, a die condition) and no compound or conditional events; `rationals` is `a/b` fractions with mixed numbers
forbidden; `mean`/`median`/`mode` are a listed dataset and one statistic — no box plots, no MAD, no comparing
distributions; `angle_relationships` is two angles in one stated relationship, with no diagram to refer to. So at
these bands **`advanced` means harder numbers and one more reasoning step inside the same question shape, not
different mathematics.**

**Three wrong-answer bugs came from seed text alone, all found by reading generated output and none catchable by
`grade_appropriateness`** (llama3.1:8b). "Counted from a described condition" produced *"either blue or yellow"* — a
compound event — scored **1 against a true 10/21**. "Recognise that a dataset may have no mode at all" is true of the
subject and wrong as an instruction: nine distinct readings, **no mode, answer 0**. And a percentage framing scored
**1**, because percentages give the solver no counts to divide. Each is now forbidden in the objectives *and* in the
row's `notes`. **An objective that is pedagogically true can still be an instruction the solver cannot score** —
check what a cell generates before trusting it, not just what it says.

**`notes` is prompt text, not a margin note.** `_lookup` appends it to `objectives` and sends the pair, and the
2000-char clamp covers both — so a `notes` field written as documentation for the next editor is documentation the
model reads, and repo-internal references are noise inside a prompt. Keep it to constraints on the question; the
reasoning aimed at a person goes in the seed file's `--` comments, which are sent nowhere.

**Five seed files, 34 rows** (count the INSERT tuples rather than trusting this):
`supabase/seeds/lesson_plans_priority_topics.sql` covers `ordering`, `geometry` and `expressions` across all four
bands; `lesson_plans_remaining_topics.sql` covers seven topics at `upper`/`advanced` only, since `_allowed_topics`
already keeps them out of grades 1–5 and an unseeded cell fails open to the heuristics;
`lesson_plans_young_topics.sql` covers the four young topics in **five rows, not sixteen**, because `TOPIC_MAX_GRADE`
makes most of that grid unreachable — and `patterns`' `middle` row is written for grades 4–5 rather than the band
ceiling of 6, since **a capped topic's band text is not the band's text**; `lesson_plans_hs_topics.sql` and
`lesson_plans_spread_topic.sql` cover the grade-9 topics. All five are dashboard-run scripts rather than migrations,
because a migration would re-apply their text over any later dashboard edit on every rebuild.
## Grade appropriateness is code-enforced twice: which topic, and what that topic asks

Both layers were prompt hints once, and both leaked. `randomize_selection()` — the fallback whenever an LLM call
fails to parse — picked uniformly across all topics with **no grade parameter at all**, which is how a 1st grader
landed on algebra.

### `_allowed_topics(grade)` is the single source of truth, keyed per topic

`TOPIC_MIN_GRADE` has **one entry per topic**, not grade brackets. A bracket has to be *remembered* for every topic it
should exclude, and two were not: `angle_relationships` was allowed from grade 4 against **7.G.5** (30 of 30 above
grade) and `probability` from grade 6 against **7.SP.5** (10 of 10). Neither is reachable by prompt tuning — the topic
arrives before the concept, so no version of the question is grade-appropriate.

**The generalisation, needed three times now — topics inside a grade bracket, scenarios inside a topic, scenarios
inside a band: a per-bucket minimum cannot describe an item that arrives after the bucket it belongs to. Whenever a
gate is one number for a group, ask which member of the group arrives last.** A per-item minimum cannot omit an item,
and one added without a minimum fails a test rather than defaulting to available everywhere.

**`TOPIC_MAX_GRADE` is the answer to `TOPIC_MIN_GRADE` being a floor with no ceiling.** "8 + ? = 11" is 1.OA.8 and
does not become a grade-9 question by using bigger numbers; past grade 3 that skill is `algebra` with proper
notation. Without a ceiling the difficulty tiers would rank it as somebody's "easy". Deliberately **not** applied to
the original ten, which all scale. It repeals a property a test used to assert — that topics only ever accumulate
with grade — and `test_each_topic_is_offered_over_exactly_the_grades_it_declares` replaces it with what that test was
protecting: availability follows the declared tables and is contiguous.

**A grade is read numerically, through `grade_levels`, and an unreadable one counts as the youngest.**
`profiles.grade_level` is free text. Every grade rule used to match exact strings and fall through to its *most
permissive* branch, so `"Grade 1"` missed every branch of `_allowed_topics` **and** of `_grade_band` — algebra and
`advanced` content to a 6-year-old. Both halves matter: fixing the topic gate alone still leaves advanced material
reaching a child. `grade_number` parses a digit or a named label, rejects anything outside 0–13 so `"2026 cohort"`
cannot become grade 2026, and answers `None` when it cannot tell, which every caller treats as the youngest.
`_grade_band` in every generation file delegates to it.

`_safe_topic(topic, grade)` checks the LLM's own selection against the same table and `randomize_selection()` draws
from it, so there is no third way a topic reaches `question_generation()`.

### Difficulty and grade are one table, not two independent scales

`DIFFICULTY_COMPLEXITY[difficulty]` described the question's *structure* and `GRADE_COMPLEXITY[grade_band]` only
scaled a number's magnitude on top, so "easy" meant "one-step equation with x" at every grade.
`COMPLEXITY_BY_GRADE[grade_band][difficulty]` replaces both with one self-contained instruction per cell, "early"
grounded in grades 1–3 arithmetic rather than smaller versions of the same structure.

**Eleven of the seventeen topics use it, and the six that do not are not an unfinished migration.** In `geometry`,
`angle_relationships` and `probability` difficulty already selects a *scenario*, so a second table would state the
difficulty rule twice in two places that can disagree; they keep `GRADE_COMPLEXITY[band]` for magnitude alone, with
grade gating their scenarios through `_pick_scenario`. `quadratics`, `functions` and `spread` choose difficulty in
coefficients in code.

**`GRADE_OVERRIDES` is a per-grade line appended to the prompt**, deliberately not folded into the band-keyed table —
a thirteenth column for one rule would make every other topic's table wrong by omission. Prompt-level, so it can leak;
`grade_appropriateness` is where a code check belongs if it does.

### A gate has two halves, and the second is easy to leave out

**`SCENARIO_MIN_GRADE` decides which prompt block is *sent*; nothing about that constrains what comes *back*.** Both
scenario-gated topics shipped with only the first half, so a `sphere_volume` reply to a grade-4 request (8.G.9) and a
`triangle_sum` reply to a grade-7 one (8.G.5) were each solved and served, walking past the gate written to stop
them. The reply is now checked against the same allowed set inside the retry loop.

That is not defensive: **Haiku returned a scenario other than the one asked for twice in this work**, once with
another scenario's variable keys. Selecting a block is a prompt-level act; only validating the reply is enforcement.

Two scenario leaks were the concrete bugs, both now keyed on `grade_band` rather than `difficulty` alone (a
"hard"-difficulty 1st grader is a real state, since the two are independent inputs): `expressions`' `simplify`
(`2x + 3x`) was picked by unconditional `random.randint(1,3)` at every grade, and `angle_relationships`' scenario 5
(`algebra_complementary`) was gated to "hard" difficulty with no grade check. Both are withheld from `early`/`middle`.
`geometry` gained an `EARLY_BAND_SCENARIOS` filter orthogonal to its `DIFFICULTY_SCENARIOS`.

**That filter was itself too generous, and it filtered one band.** It admitted `triangle_area` (**6.G.1**) to a band
meaning grades 1–3, and fixing that band alone left the larger half: `middle` was unfiltered entirely, offering circle
area (7.G.4), the Pythagorean theorem (8.G.7), and a hard tier of **only** volumes — so a 4th grader on that tier was
always asked a grade-8 question. `SCENARIO_MIN_GRADE` now records the grade each formula is introduced at and
`_pick_scenario` filters **every** band. **Nothing else catches this class** — `grade_appropriateness` looks for
variable notation, and a lesson plan steers what a scenario *asks* rather than which are offered — and both instances
were found by reading generated output. **Check a band's scenarios against the standard they claim to match, not
against whether they look simple**: area of a triangle looks as elementary as area of a rectangle and is three grades
apart.

A per-scenario grade is the pattern in three places. `angle_relationships` sits at grade 7 for 7.G.5 but
`triangle_sum` is **8.G.5**, and its medium tier is *only* that scenario — so every grade-7 student on that tier got a
grade-8 question, 4 of 10 overall. Grade 7's medium tier now falls back to the rest of the topic.

### Difficulty tiers are relative to what a grade can see

A topic used to map a difficulty to a fixed list of scenario numbers — right while every scenario is available, wrong
once a grade filter removes some. Geometry's hard tier is the volumes and the hard ones are 8.G.9, so gating on grade
left grade 6's `medium` inverting a formula while its `hard` multiplied three numbers.

**That is not cosmetic, because difficulty is what the biosignals move.** `signal_fusion` labels a student `focused`,
the decider shifts medium → hard, and at those grades that handed them an *easier* question — the fusion firing
correctly and being undone one layer down. **Anything that narrows what a tier can offer has to be checked against
the tier ordering, not just against the grade rule it was written for.**

`scenario_tiers.pick` ranks the *available* scenarios by `SCENARIO_DIFFICULTY` and slices them into thirds, so `hard`
is the hardest third of whatever remains and cannot invert. Small sets overlap rather than emptying, so
`random.choice` never sees an empty list.

**`SCENARIO_DIFFICULTY` is ordered by steps to solve, deliberately not by the grade that teaches it.** Conflating the
axes reads `algebra_complementary` (set up and solve an equation) as easier than `triangle_sum` (one subtraction). A
CCSS-grade metric reported angles as broken when it was correct.

### A band's tiers are written for its ceiling, so its youngest grade is over-served

`grade_band` buckets 1–3, 4–6, 7–8, 9+, and every tier is written for the top of its band. At grade 4, bottom of a
three-grade band: **66% of questions above grade**, from four mechanisms — geometry gated on the band ceiling rather
than the grade (volume is 5.MD.5), `expressions` middle tiers allowing parentheses (5.OA.1), `rationals` middle tiers
using unlike denominators (5.NF.1), and `mean`/`median`/`mode` offered from grade 4 against 6.SP.5c.

**Where a per-item minimum exists, use the grade; the band ceiling is only a fallback for a grade that cannot be
read.** The next two are `GRADE_OVERRIDES`. Grade 4 went **66% → 43% → 0%**, the last step when
`mean`/`median`/`mode` moved from 4 to 6.

**That step cost breadth, and the cost is the point of recording it**: grades 4–5 now offer five topics where they
offered eight. `test_the_cost_of_that_decision_is_four_topics_for_grades_four_and_five` (whose name predates
`patterns` reaching grade 5) and `test_mean_median_mode_wait_for_the_grade_that_teaches_them` hold both decisions, so
a later widening has to be a choice rather than a drift.

Grades 1–2 had the same shape and were fixed by **adding a scenario rather than removing the topic**: the easiest was
`rectangle_area` (3.MD.7), so a strict reading left those grades no geometry and dropping the topic left them two.
`rectangle_area_by_counting` is **2.G.2**, the one numeric geometry standard below grade 3. It reuses
`solve_rectangle_area` because rows × columns *is* length × width — the difference is entirely in the wording, which
is the scenario block's job, and a second solver would be a copy free to drift.

### Grade 1 had two topics, and now has six

`missing_number` (1.OA.8 through 3.OA.4), `patterns` (1.NBT.1 and 2.NBT.2 through 5.OA.3), `graphs` and
`shape_fractions`. All answer to a single whole number an exact solver produces, which is the constraint that rules
out most of 1.G and 1.OA — a shape-partitioning question has no number to score.

**The first two write the unknown as `?`, never `x`**, which is the whole distinction from `algebra` (6.EE.7);
`grade_appropriateness` lists them in `FORBIDDEN_BANDS` because the prompt asks and only the check enforces. **Neither
solver touches sympy**, so neither needs the bounded subprocess — the arithmetic is one operation on integers matched
by `^\d{1,4}$`; `test_young_topics.py` pins the first three. Both refuse rather than guess, and `solve_pattern`
derives the step then **checks it against every known term**: `2, 4, 6, ?, 9` has a first-pair step of 2 and is not an
arithmetic sequence, so taking the first pair would answer 8 confidently for a question with no single right answer.

**Each has its own shown-versus-scored check rather than `dataset_mismatch`**, which locates a dataset after the last
colon. There is no dataset here, there is an equation — and the question *is* the equation, so a text reading
"8 + ? = 12" over variables scoring 11 is answered correctly and marked wrong. Both also refuse any digit outside the
equation, since a second number on screen leaves a young reader unable to tell which one is meant.

**Grade 1 has no geometry at all**, and that is the end of this thread rather than a gap in it. 1.G produces no number
a solver can score, and the tempting fix — *"3 triangles and 4 squares, how many shapes?"* — is addition wearing a
geometry label. So `TOPIC_MIN_GRADE["geometry"]` is 2 and grade 1's list is `ordering`, `expressions` and the four
young topics: **six**, none of them geometry. That is the honest size of what this system can ask a 6-year-old. **An
unreadable grade lands there too.**

**A new topic needs five things wired, and the third fails silently.** `ALL_TOPICS` and `TOPIC_MIN_GRADE`; a `case` in
`question_generation`'s match (which now raises by name rather than falling through to an `UnboundLocalError`); **a
`math_topics` row, via a migration** — `record_topic_attempt` joins on `questions.subject` and attributes nothing when
that finds none, so a topic without one serves and scores questions while crediting the work to nothing; an entry in
`FORBIDDEN_BANDS`; and `frontend/src/lib/topics.js`.

**That last was four hardcoded lists, and was six.** `Analytics.jsx` counted the newer topics in *nothing* — its chart
drops empty bars, so they vanished with no hint anything was missing — and `Questions.jsx` offered no way to filter
the bank to them. Both bite hardest for grades 1–3, whose topics those are. `lib/topics.js` is now the only place a
list is written down, and **`topics.test.js` parses `ALL_TOPICS` out of `LLM_topic_decider.py` and fails if the two
disagree** — a React bundle cannot import Python, so the copy is checked rather than trusted. A third test fails on
any new file that writes a list of its own. `get_user_history` derives its per-topic history from `ALL_TOPICS` rather
than listing them again: `question_generation` reads `history[topic] if topic in history else []`, which fails *open*,
so a forgotten topic quietly lost its repeat-avoidance.

### Grades 9+ have no content of their own, and prompts cannot give them any

`advanced` was `upper` with the magnitude clause deleted. **An empty restriction reads to a model as no requirement,
not a harder one**, so it produced the easiest shape that fit: **83% of grade-9 questions three or more grades below
grade**, including `Simplify 5/9 + 7/11 - 2/9` (5.NF.1) on the **hard** tier. Every `advanced` tier now states a
requirement and the model complies.

**It barely moved the number — 83% → 81% — and that is the real finding.** The score is by the CCSS grade of the
*concept*, and every concept these solvers could score topped out at grade 8. Harder numbers inside 8.EE.7b are still
8.EE.7b. **Closing it needed solvers, not prompt text**, and each new solver has to be able to *score* what it asks.

Re-measured with `scripts/audit_grade_appropriateness.py` (which exists because the original audit left no script and
could not be repeated) after `quadratics` and `functions` landed: grade 9 **81% → 56%**, then **69%**, **81%** and
**100%** at grades 10–12.

**The grade-12 figure is arithmetic, not a sample.** The highest concept anything here can *score* is grade 9, so every
question this system can ask a 12th grader is ≥3 grades below by construction. **Adding topics at grade 9 cannot move
grades 11–12**; only a solver above grade 9 can.

**And the metric over-reads for practice topics.** It counts the grade a concept is *introduced*, so a 9th grader
finding a median under S-ID.2 is doing grade-appropriate work this measure scores three grades below. Good for "the
topic arrived before the concept", poor for "the student has outgrown this".

**Capping the grade-8 topics was considered and rejected, on arithmetic rather than taste.** "Concept grade ≥ student
grade" leaves grade 9 two topics and grades 10–12 **zero**; "within two grades" leaves 8, 5, 2 and **zero**. **Capping
cannot fix a ceiling** — it converts "serves below-grade content" into "serves no content", and `_safe_topic` calls
`random.choice` on that list, so empty is a 500 on every question at that grade.
`test_the_grade_eight_topics_are_knowingly_uncapped` holds the decision.
(`test_topics_at_eighth_grade_are_those_inside_both_bounds` replaced a test reading "allowed iff it has no ceiling",
true only until a topic had a floor above 8; assert the range, not the absence of a cap.)

### `quadratics`, `functions` and `spread` are the grade 9+ content

All three sit at grade 9 and are the first topics whose concept is above grade 8. `hs_solvers.py` is **pure
bounded-integer, no sympy, so no bounded subprocess**: every value goes through `parse_int`, matching `^-?\d{1,4}$`
*before* `int()` sees it. Reach for `safe_solve` only when something downstream needs a sympy object.

**The equation shown is rendered from the coefficients being scored, never parsed out of the text.**
`render_quadratic`/`render_polynomial` are the only things that write an equation and each generator requires the
output verbatim in `question_text` — same direction as `question_figures`: derive the presentation from the scored
data and a disagreement stops being representable. Sign handling is load-bearing, since `x^2 + -5x + 6 = 0` is
something the model "corrects", costing a retry on every negative middle coefficient.

**A two-root equation is only scoreable because the question names which root**, chosen *before* the call and pinned
in the prompt — `target` is deliberately absent from the schema. `shown_matches_scored` checks that too, because a
text asking for "the smaller solution" scored against the larger is a well-formed question, correctly solved, marked
wrong. It also refuses a text naming neither.

**The coefficients are chosen in code, and `quadratics` is the worked example for why.** Across three promptings —
the constraint as a description, as a construction recipe, and as a recipe with a worked example — llama3.1:8b
produced a factorable quadratic **0 of 3, 2 of 3 and 1 of 4** times, nearly always failing on `irrational roots`: a
freely chosen `b` and `c` almost never leave `b² − 4ac` a perfect square. Of the successes, two dropped the constraint
and one copied the example verbatim, so the tier was *also* not producing the content it named. No wording fixed it,
because it is not a wording problem.

`_choose_coefficients` builds from two distinct integer roots, so the equation is factorable by construction. Three
things follow, and the second generalises: **every retry that class caused was a billed model call that could not have
succeeded**; the `hard` tier can be the AC method, the right Algebra I rung and the *least* achievable thing to ask
for; and the tiers get a uniform meaning. Same move as `target`, and as the scenario in geometry and probability —
**decide the part with a right answer in code, and let the model write the sentence.** Reach for it whenever a
generator retries against a constraint the model keeps missing rather than against a malformed reply.

Four quadratics are refused rather than served: no real roots, a **repeated** root (where "the larger" names nothing
and every distractor would simply not be a root), irrational roots, and roots that are not whole numbers. That
restricts the topic to equations factorable over the integers, which is A-REI.4b's core rather than a limitation
worked around.

**`functions` hands its coefficients over too, and needed it more.** Its `_FOOTER` required each function verbatim
under a `FUNCTIONS AS THEY MUST APPEAR` section — **and that section was never emitted**, because the footer was
written for a design only `quadratics` implemented. The model was pointed at instructions that did not exist:
`compose` failed **3 of 3**, two of the topic's three tiers. **A prompt that references a section it never emits is
worth grepping for whenever a generator retries on formatting.** The inner function of a composition is always
degree 1, which bounds the answer by construction.

**`functions` is grade 9 on a narrower claim than its name suggests.** Evaluating a rule at a value is 8.F.2 and grade
8 does not require function notation; what is high school is the notation (F-IF.2) and composition (F-BF.1c) — which
is why `compose` is the medium *and* hard tier, since a version whose every tier was `evaluate` would be 8.F.2 wearing
an `f(x)`. `MAX_ABS_RESULT` bounds it because composition squares its input: a question answered 48,271,009 tests
calculator ownership.

**`spread` is S-ID.2, and it is standard deviation only.** The IQR and MAD S-ID.2 also names are **6.SP.5c** —
offering them would put grade-6 content inside a topic added to serve grades 9–12. Four things are load-bearing, and
the last three were each found after the first looked finished:

- **The answer is exact because the data is built to make it so.** Most datasets have an irrational standard
  deviation, and an answer rounded to whatever precision the formatter chose is the
  answered-correctly-marked-wrong failure wearing a decimal point. `_DEVIATION_PATTERNS` holds multisets summing to
  zero whose population variance is a perfect square, scaled and shifted by `_choose_dataset` — hardcoded rather than
  searched, because that is a property of the numbers and a search is a loop whose termination depends on its input.
- **The question must say "population standard deviation", and the generator refuses without it.** Sample standard
  deviation over n−1 is what many high-school courses teach and gives a different number, so "standard deviation"
  alone has two defensible answers and scores one. Not fixable by any solver; only the wording removes it.
- **Checking the data is *contained* in the text binds neither its extent nor its label.** The containment check every
  sibling uses was wrong here three ways at once: `"14, 16, 17, 18, 20, 25"` contains `"14, 16, 17, 18, 20"`, so an
  appended value passed; the sets could be written in either order; and — the one no ordering check catches — the same
  sets in the same order under **swapped labels** asks for A−B while B−A is scored. `shown_matches_scored` compares the
  text's comma-separated *runs* exactly and in order, and requires each set verbatim under its `Set A:`/`Set B:`
  label. **When the semantics live in a label the prose writes, the label has to be part of what is checked.**
  Deliberately strict where `question_consistency` fails open, because the generator supplied every number: any other
  number is a reply that ignored its instructions, not an ambiguity to read generously.
- **Binding the data is not binding the question, and the ask needs its own check.** Two correctly labelled sets in
  the scored order still pass every data check under a text asking for *Set B's own* standard deviation — scored 3
  where the screen says 5, with 5 on the option list, since `near` offers each set's own spread as a distractor. **It
  applies to the interrogative clause, not the whole text** — "a coach checks how much more consistent the team is" is
  context this prompt asks to vary, and refusing it costs three retries and a 503. Scoping has two traps, both found
  only by mutation: the clause may *contain* the data, whose labels then win the ordering, so the verbatim anchors are
  stripped before the labels are located; and **an ask is not always a question** — with no `?` anywhere a
  clauses-ending-in-`?` search finds nothing, and two arms reading that empty result in opposite directions is how one
  silently skipped its guard while the other refused a correctly worded imperative. Fall back to the closing sentence,
  not the whole text, which reinstates the context false-positive. Then **it must not pin word order**: direction rides
  on which label appears first, and a magnitude phrase separates it from "which is larger, Set B or Set A?", whose
  answer is a label where a number is scored.

**A rule a scenario cannot use is not noise in a prompt, it is a suggestion.** The shared footer told a *one-set*
prompt about `Set A:`/`Set B:` labels and comparisons, and llama3.1:8b duly invented both — labelling the single set
"Set A", making up a `Set B: 74`, and asking for Set B's standard deviation, which would have been scored as Set A's:
4 refusals in 6 one-set generations. Splitting the rules per scenario took Ollama from **1.25 to 1.00 model calls per
question**. **What the prompt *shows* beats what it *says*, and that includes rules it shows for a case that is not the
one being asked.**

**None of the three appears in `FORBIDDEN_BANDS`, deliberately, exactly as `algebra` does not.** Variable notation is
what they *are*; a band rule would refuse every question either exists to ask.

**Measured on both providers with the seeds injected, which is the path no test covers** — an unseeded cell fails open
to the heuristics, so any validation that does not inject the lesson text exercises the wrong path. Haiku 4.5: **6 of
6 in 6 calls**, no retries, answers correct by hand. llama3.1:8b: 3 of 3 per topic. Redo it after any edit to a seed
row or to a prompt these topics send.

### Angle answers are whole numbers through 5th grade, and decimals after

`algebra_complementary`'s coefficients are unconstrained, so it returns things like 11.875, and the lesson-plan text
asking for whole numbers did not stop it. `LLM_angle_relationship_generation` checks the **solved value** and
regenerates when it is fractional for a young student. A decimal is not a defect in itself — from 6th grade it is
ordinary mathematics — so the rule is scoped to the grades where it is not.

**Keyed on the raw grade, not `_grade_band()`.** The line falls between grades 5 and 6 while `middle` spans 4, 5 **and**
6, so no band boundary is in the right place; pinned by
`test_the_cutoff_splits_the_middle_band_which_is_why_it_is_grade_keyed`. An unrecognised grade falls through to
"decimals allowed", matching `_grade_band()`'s own `advanced` default: the constraint is a scaffold, so the safe
direction when the grade is unknown is to leave the mathematics alone.

**The solve moved inside the retry loop to make this possible** (`_solve_scenario`). Whether the answer is a whole
number is a property of the *solved value*, so it cannot be checked until the scenario has been evaluated — and a
question that fails has to be regenerated, not patched.

## A question may carry a figure, and it is a spec the client draws

`questions.figure` holds a specification built by `question_figures.py`;
`components/questions/QuestionFigure.jsx` is the only thing that turns one into pixels. It exists because grades 1–3
mathematics is largely visual and the standards this system could not ask were mostly the visual ones —
`rectangle_area_by_counting` (2.G.2) was being asked in *words*, "a rectangle split into 3 rows of 4 same-size
squares", which is a description of a picture rather than the picture.

**The figure is derived from the data the solver uses, and no generator asks a model for one.** That is the design,
not a preference. `question_consistency` exists because a model free to write the question text and the scored data
separately eventually disagrees with itself, and the student answers the version on screen while being marked against
the other. A picture is the same hazard with **no text for any check to read**. Reading `variables`, the same dict
`geometry_solvers` indexes, makes that disagreement unrepresentable rather than unlikely.

**A spec, not an SVG**, for two reasons. The drawing and the sentence a screen reader is given come from one object,
so they cannot describe different pictures — the rule `AccessibleChart` exists for, and it binds harder here because
a figure has no text of its own. And a renderer fixed later applies to every question already in the bank; stored
markup bakes today's renderer into rows that outlive it.

**A figure is an enrichment and never a requirement.** `figure_for` returns `None` for a scenario with no figure,
values it cannot use, or a size it will not draw, and it never raises — it runs after the solve, so an escaping
exception would turn a checked, grade-appropriate question into a 500 over a decoration. The client matches that: an
unrecognised `type` renders nothing rather than throwing, which is what an older bundle meets against a newer bank.
Both ends bound the grid at 12 a side, because a bank row outlives the code that wrote it.

**The column is nullable with no default** — same four-state rule as `sessions.chart_paths`. A `'{}'::jsonb` default
would claim every question ever generated was considered for a figure and found to need none. The generators return
the key as `None` rather than omitting it.

**Every surface that *presents* a question renders its figure, and there are five.** This shipped wired into two —
the adaptive view and flashcards — leaving the teacher's session review, the bank modal and practice test mode showing
the wording with nothing to count, which is a different question from the one the student answered.
`normalizeQuestion` returns a fixed object, so a key it does not name does not exist for any caller downstream; it
carries `figure` through. `QuestionFigure.test.jsx` walks the source and fails on a file that renders question text
without it — matching `<QuestionFigure`, **not the bare name**, which a dangling import satisfies and which is
exactly what deleting the element leaves behind. The first version of that check passed against a build with the
render removed and the import kept. The teacher dashboard's "Recent Questions" list is the one stated exception: a
`line-clamp-2` row is a *reference* to a question, not the question.

## A question carries its Common Core code, resolved by grade and scenario

`questions.ccss_standard` is the machine-readable copy of the codes `TOPIC_MIN_GRADE` and the two
`SCENARIO_MIN_GRADE` tables only ever cited in comments. `ccss_standards.ccss_for(topic, grade, scenario)` resolves
it and every generator attaches it; `CCSSBadge.jsx` renders it on the same five surfaces `QuestionFigure` reaches,
with the same source-scan exhaustiveness test.

**Resolved by grade, not band, and by scenario first.** A band spans three grades and the standard changes inside it
(`1.MD.4` at grade 1, `2.MD.10` at grade 2, both "early"), and a scenario names the standard inside a topic. A
*ladder* of `(floor_grade, code)` per topic or scenario picks the highest floor at or below the student's grade; a
grade below every floor takes the lowest rung, since the defense-in-depth tiers still describe content.
`test_every_scenario_in_a_gate_table_has_a_code` pins the scenario tables to the gate tables. The one imprecision is
difficulty inside a band: grade 6 algebra's two-step medium tier is 7.EE.4 content and reads `6.EE.7`, because the
resolver does not see the tier.

**`add_question_to_supabase` dedupes on text *and* standard.** The code is the first stored field derived from the
student's grade, so one text generated at grade 6 and again at grade 8 is two rows (`6.EE.7`, `8.EE.7b`) rather than
one whose badge belongs to whichever grade wrote it first and then contradicts what the second student saw. Nothing
constrains `question_text` unique, so the second row inserts cleanly — and a text regenerated after the column landed
no longer matches its NULL-coded predecessor, so the bank gains one row per such question, visible to a teacher as a
duplicate. That is the accepted trade: updating the old row in place would stamp a grade-8 code on a row grade-6
answers already reference. And **every scenario-selecting generator checks the reply's scenario name**
(`expressions` was the one that did not): an off-name reply misses `SCENARIO_LADDER` and takes the topic's grade-1
rung.

## `shape_fractions` reads a fraction off a picture, and refuses an ambiguous one

1.G.3 (halves and fourths), 2.G.3 (thirds), 3.NF.1 (a/b as a parts of b). **Distinct from `rationals`, which is
4.NF.3 onward and is fraction *arithmetic*** — this is recognition, which is why it sits at grade 1 while `rationals`
starts at 4. Its figure is required, for the same reason `graphs`' is.

**Lowest terms is required, and refusing otherwise is the point.** Two shaded parts in four is a perfectly good
picture and an ambiguous question: `2/4` and `1/2` are both correct readings, and whichever the solver picked, a
student giving the other is marked wrong for a right answer — the failure this codebase treats as the worst
available, and worse than a refusal, which costs one retry. Reducing the answer instead is worse still: the student
is asked to read the picture, and the picture says two of four. `question_figures._part_whole` deliberately does
**not** check it — a reducible fraction is perfectly drawable, and drawability and answerability are different
questions.

**Grade 1 has exactly three legal pictures, and the model reaches for a fourth.** 1.G.3 holds it to 2 or 4 parts, and
lowest terms leaves only `1/2`, `1/4` and `3/4` — while half-of-four is the shading a model produces first. On
llama3.1:8b at grade 1 / easy: `2/4` on all three attempts, so the request **failed outright** rather than degrading.
That is the cost of a refusal landing on a cell with almost no legal answers left, and it is not visible from either
the standard or the refusal rule alone. The fix is in the lesson plan, which names the three fractions for grade 1:
4 of 4 generate afterwards, 2 of them still spending one retry on `2/4`. **Check a narrow cell's retry rate, not just
that it can succeed** — a tier whose legal answers you can count on one hand is where an exhausted retry budget stops
being theoretical.

**The distractor space is checked exhaustively rather than sampled**, because it is 21 fractions. Two properties,
both violated before: an option of one or more cannot be part of a shape, so `2/1` is not a misreading a child could
make but an option nobody considers — which quietly makes a three-way choice a two-way one; and `1/1` and `2/2` were
both offered against `1/2`, two options of equal value that go together with a single thought. Halves is what forced
neighbouring denominators into the candidate list: with only near-misses of 2, the sole proper distractor available
was `1/3`.

**The whole is a constant width and the parts divide it**, not the other way round. Fixed-size parts drew eighths at
twice the width of halves, which says the wrong thing about what a whole is — and is exactly the misconception these
standards are about. Found by rendering it and looking.

## `graphs` is the one topic whose figure is required

1.MD.4 ("how many more or less"), 2.MD.10 (a bar graph with up to four categories), through 3.MD.3. Two scenarios:
`how_many_total` is one addition, `how_many_more` a reading *and* a subtraction. It is the visual precursor to
`mean`/`median`/`mode` — counts read off a graph at grades 1–2, statistics over a listed dataset at grade 6.

**Everywhere else a figure that cannot be built costs the picture and nothing else**, because the question text
stands alone. "How many more cats than dogs?" is not answerable read aloud — the counts live *only* in the graph. So
this generator treats an unbuildable figure as an unusable reply and retries. `figure_for` keeps its own fail-open
contract and still returns `None` rather than raising; **the decision that `None` is fatal belongs to the topic that
cannot do without it**, so a new figure type does not inherit a requirement it does not have.

**A digit in the question text is refused.** Writing the counts out hands the student the reading the question exists
to ask for — it stops being a graph question and becomes arithmetic. Checked, not merely requested.

**`categories` is a list of `{name, count}`, not a map.** The obvious `{"cats": "7"}` cannot be schema'd at all — the
API refuses an open `additionalProperties`. Choosing a shape that *can* be schema'd costs the generator one
indirection and is cheaper than accepting the gap.

**Asking how many more of the *smaller* bar is refused, not answered with an absolute value.** The question on screen
asks for something with no answer; scoring the difference would mark a student right for answering a question nobody
asked.

Capped at grade 3: 3.MD.3 is the last bar-graph standard, and grades 4–5 move to line plots (4.MD.4, 5.MD.2), which
is a different figure and a different reading.

**Two things about the renderer were found by looking at it, not by a test.** Column width is derived from the widest
label — at a fixed width "storybooks" and "picture books" printed on top of each other, with both labels present and
correct in the DOM, on a figure whose entire job is to be read. And gridlines are ruled at every unit, so bars can be
*counted* rather than compared, which is the reading 1.MD.4 asks for. The component is named `BarGraph` because the
obvious name is one `AccessibleChart.test.jsx` matches as a bare word to find Recharts charts rendered outside it —
and a hand-written `<svg>` is the case that guard states it cannot see, so the match would be a false accusation. The
word cannot appear in that file's comments either; the guard reads the file, not the syntax tree.

## `grade_appropriateness` checks the output, because everything else only checks the prompt

`COMPLEXITY_BY_GRADE` and the lesson-plan text are both **prompt-level** — they ask the model for something and
nothing verifies it complied. So `find_violation(question_text, topic, grade_band)` runs inside each generation retry
loop: a violation retries, and exhausting the retries raises, which `_prefetch_worker` already catches. Thirteen of
the seventeen topics are wired in; `algebra`, `quadratics`, `functions` and `spread` are the exemptions above.

**It tests one thing — algebraic variable notation reaching a band that must not see it — and the narrowness is the
design.** A check with a real false-positive rate is worse than no check: it burns retries, and a question rejected
for a bad reason looks exactly like a model that cannot follow instructions. `x` as a multiplication sign is the
false positive that actually occurs, so the pattern is `\d+[xyn]\b` — anchored so `2x` matches while **`6 x 4` and
`6x4` do not** (the trailing `4` kills the word boundary). `test_ordinary_questions_are_not_refused` is the
load-bearing half of its test file; a naive `/[xyn]/` fails exactly those cases. `geometry` is early-only, since
`upper` legitimately labels triangle sides `a`, `b`, `c`.

What it deliberately does **not** check: magnitude/decimal/negative rules (`-` is also a hyphen and a range
separator, so detection would be guesswork), and whether the question reflects the lesson-plan text's *content* —
that needs a model to judge, which puts an unbounded LLM call on the hot generation path. **So this bounds the damage
a bad lesson plan can do; it does not confirm a good one was followed.**

**Forbidden operators in early-band `expressions` are the one exception, and they are checked because reading the
output found them.** On llama3.1:8b with the lesson plans seeded, grade 1 / easy: **2 of 8 questions came back with
parentheses** — `Solve 5 + (2 - 1).` — while the *same prompt* said "ADDITION AND SUBTRACTION ONLY. Do NOT use
multiplication, division, or parentheses." **A few-shot example beats a textual constraint**: every scenario example
in `expr_prompt` is written for older students (scenario 1's is `36/3+(8*2)-(15-7)+4`), and the model followed their
shape over the rule. Three changes, all needed: scenario 2 (`order_of_operations`) is withheld from `early` — it is
5.OA.1 and is *defined* by mixing precedence, so it cannot be expressed within the band's rule at all —
`EARLY_BAND_EXAMPLE` gives the band a worked example in the shape it is allowed, and the operator check rejects what
still slips through. Re-measured: **10 of 10 compliant.** Unlike negatives and decimals these characters have exactly
one reading inside a generated expression.

**That is the general lesson: seeding a lesson plan does not make the model follow it, and neither does an
unambiguous instruction sitting next to a contradicting example.** Anything added to these prompts needs its effect
read off generated output before it is believed.

## The question shown and the data scored are two fields, and they must agree

Every generator returns a `question_text` the student reads and a separate structured field the solver computes from —
`variables` for the dataset topics, `values` for `ordering`, `items` + `scenario` for `probability`. **Nothing checked
that they described the same thing.** On llama3.1:8b, **2 wrong answers in 12 generated**: a `mode` question shown
`8, 4, 12, 16, 4, 14, 8, 10, 20, 4` and answered `[8, 4]` when 4 is the only mode, because the scored `variables` were
not the numbers on screen; and a `probability` question asking *"what is the probability of selecting an EDM band?"*
answered `18/23`, the **complement**, because the text asked a positive question while the JSON said
`scenario: not_probability_of`.

**This is the worst failure shape available here** — the question is well-formed and answerable, the student answers it
correctly, and is marked wrong against data they never saw. Worse than a refused question, which costs one retry.

`dataset_mismatch` compares the numbers **after the last colon** against the scored list (these prompts all put the
dataset there, which is what makes locating it reliable); `negation_mismatch` requires a negated wording and
`not_probability_of` to imply each other, **in both directions**, since a negated question scored as `probability_of`
is wrong by the same amount.

**The two checks do not cover the same topics.** `dataset_mismatch` is in `mean`/`median`/`mode`/`ordering` only, and
`negation_mismatch` is in `probability` only. This file said `dataset_mismatch` covered probability too, for months; it
never has — probability's counts live in the sentence body, not after a colon, so the check would be inert there
anyway. But *documented as wired and absent* is the worst of the three states, because it is the one nobody re-checks.
Verify with `grep -l dataset_mismatch LLM_*_generation.py`, which is cheaper than trusting this paragraph.

**Both fail open**, which is what makes them safe to run on every question: order is ignored (the solvers sort anyway),
non-numeric `variables` are skipped, and a question with no colon-delimited list is left alone rather than compared
against stray numbers in the sentence. A false rejection burns retries and looks exactly like a model that cannot
follow instructions. They catch a clear contradiction; they are not a proof of agreement. `algebra`, `expressions`,
`geometry` and `angle_relationships` are **not** covered: their scored fields mix operators and labels with numbers, so
there is no comparable multiset.

**Measure how often a fail-open check *engages*, never just how often it fires.** A check that never finds anything to
compare reports a perfect false-positive rate while doing nothing, and reads as evidence that it works. The dataset
check was inert on **half** the `ordering` questions, because `_as_floats` rejected fractions as "not comparable" —
while `solve_ordering` sorts on `float(sympify(v))` and handles them fine. Fractions are now one token and both sides
are compared **by value**, so `4/5` shown against `0.8` agrees. A **mixed number** anywhere in the text fails open:
`1 1/2` is one value to a reader and two tokens to the regex.

`scripts/measure_generation_checks.py` is where that measurement lives. Run it against `ollama` for a free baseline and
`claude` for the number that describes production; it bills like any other caller. On claude-haiku-4-5 at 5th grade /
medium, 3 per topic: the dataset check engaged on **12 of 12** applicable questions and agreed on all of them,
`negation_mismatch` 3 of 3, and `grade_appropriateness` on all 27 of the nine topics it is wired into, refusing none.

**Two things about running it are worth more than the numbers.** It must observe the checks *where they run* — the
generators call them against the raw model JSON, and the dict they **return** has the scored field stripped, so a
harness that re-derives the inputs from the return value reports `inert` on everything. That produced a confident "0 of
15, the check is switched off" that was entirely an artefact of the measurement, which is this rule eating its own
tail. And it must keep **`n/a` (never called) apart from `inert` (called, found nothing)** — collapsing them is how a
check that was never wired reads as one that is working, which is exactly how the probability gap above survived.

### Two retry-loop rules every generator shares

**A key read *below* the `for/else` must be validated *inside* it.** `probability` reads `sides`, `items` and
`scenario` after the loop, and `required_keys` can list neither of the first two — they belong to one scenario each. So
a dice reply with no `sides`, a bag reply with no `items`, and a bag question mislabelled `dice` each raised `KeyError`
on attempt 1 and reached the student as a **500**, where every other malformed reply costs a retry. It also never
compared the returned `scenario` against the one it asked for, and this prompt sends all three blocks and names the
wanted one by *number*. The schema covers none of this: it closes the dice half only, and only on Claude. **The
question to ask of any generator is which keys the code below the loop reads that the loop never checked.**

**The `if not raw:` guard comes before anything that touches `raw`.** `extract_json` answers `None` for a response with
no JSON in it — prose, a refusal, an empty completion — which is the exact case the three attempts exist to absorb, so
a `.replace` or a `.strip()` above the guard raises `AttributeError` straight out of the loop and the retry never
happens. `tests/test_generation_retry_loop.py` is parametrised over every `LLM_*_generation.py` file by glob so the
next copy cannot be a one-off.

## The solvers trust model output the retry loop above them already distrusts

Switching to Haiku found five bugs, none in the migration: all five were in code that had only ever seen
`llama3.1:8b`'s output shape, and every one is the same mistake — a solver, or a distractor generator, taking the
reply as well-formed *after* the retry loop that exists to reject malformed replies has already broken.

**Solve inside the retry loop, never after it.** That placement is what turns each of these from a retry into a 500.
Measured against Haiku 4.5 at 8th grade, 3 generations per topic:

| Site | Failure | Was |
| --- | --- | --- |
| `LLM_geometry_generation` | a scenario name the `match` has no branch for | `UnboundLocalError` on `solution`, 2 in 3 |
| `LLM_geometry_generation` | a known scenario missing a variable the solver indexes | `KeyError: 'b'`, 2 in 3 |
| `LLM_algebra_generation` | `solve` returning `[]` for `x+1 = x+2` | `float(None)` → `TypeError`, 1 in 3 |
| `LLM_algebra_generation` | `solve` returning two roots | **a wrong answer** — `[0]` scored one root and marked the other choice wrong |
| `incorrect_solution_generation` | a one-term answer like `5*x` | **an infinite loop**, 28 minutes at 100% CPU |

The algebra multi-root case is the one to notice: this file already *said* the topic is one linear equation with one
solution, and nothing enforced it. **A constraint documented as a limit is a wrong answer waiting for a model that
writes one.**

**`while len(results) < n` needs a bound and a deterministic filler.** All three generators in
`incorrect_solution_generation` were unbounded, and the symbolic one hung *deterministically* on its commonest input:
`wrong_coefficient` only perturbed an `Add`, so for `5*x` it returned the expression unchanged, leaving
`sign_error`'s negation as the only alternative — two distinct values where three are needed. **Whether a randomised
search can reach `n` distinct results is a property of the *input*, not of how long you try**, so the retry count is
not the bound; the filler is. It read as intermittent because `_pick_scenario` reaches `simplify` about one time in
three, and only above `middle`: **a hang that depends on a random branch below a grade gate looks like flakiness and
is not.**

**A `raise` from a helper does not reach a retry loop the call site sits below.** Every generator's `for ... else` has
already run by the time the solve happens, unless the solve was deliberately moved inside. **If the recovery is a
retry, the check belongs above the `break`, and returning `None` says so where raising does not.**

**Use `faulthandler.dump_traceback_later(n, exit=True)` rather than reasoning about where a hang is.** Two plausible
mechanisms were proposed and implemented against this one before it was measured, and neither was the bug. The stack
dump named it in one run.

**A diagnostic print must not be able to kill what it is describing.** The generators print the raw reply on their
error paths; Windows console streams are cp1252, so a `π`, an em-dash or an accented name raised `UnicodeEncodeError`
from inside the retry loop, which absorbs bad JSON and bad shapes and then died on printing them.
`console_encoding.make_console_safe()` sets `errors="replace"` on both streams and is applied from `llm_client`, which
every generator imports — deliberately at the stream and not at the 23 print sites, so the next print cannot forget,
and so it covers the ones passing model-derived values without looking like it. The console's own encoding is left
alone: forcing UTF-8 onto a cp1252 console trades a crash for mojibake.

### `safe_solve.py`: the boundary is the whole solve, not its last step

A CPU-bound sympy call cannot be bounded in-process — `parse_expr("9**9**9")` never returns, the operand is the
model's, and the spin holds the GIL inside CPython's long-integer code, so a watchdog thread is never scheduled and a
signal handler never runs. **Only an external kill works.** It costs ~0.85 s of sympy import per call, a minority
addition to a ~1–3 s model call, paid only where something is solved.

**All eleven topics that touch sympy are wired, and getting there took four rounds of finding the next unwired one.**
`SCENARIO_VARS` checks that keys are *present*, which says nothing about the values behind them, and a `try/except`
catches exceptions rather than non-termination. **If a topic touches model text with sympy anywhere, all of that topic
belongs in the worker.**

`geometry_solvers.py` and `angle_solvers.py` exist for that: the worker cannot import a generator module, which pulls
in supabase, flask and dotenv at import, so the pure arithmetic lives apart from the prompt and the retry loop.
Anything a bounded worker must run needs the same separation — which is why `invalid_reason` moved alongside the angle
solvers rather than staying beside its caller.

**Most topics need only the parse bounded, not the whole solve.** `safe_sympify_values` covers five of the eleven:
they parse the model's numbers and then do ordinary arithmetic, which cannot hang. Only `geometry` and
`angle_relationships` needed their solvers moved, because both keep sympy *expressions* past the parse. Three parses
stay in-process deliberately, and all three read the **worker's own output** rather than the model's: `sympify(solved)`
in `expressions` and `rationals`, and `format_number`'s fallback in `median`, all bounded by `MAX_RESULT_CHARS`. **A
parse whose operand came from the model belongs in the worker, full stop.**

**Every worker branch checks its result is usable, and the `evaluate`/`simplify` one did not.** `1/0` came back as the
string `zoo` and `0/0` as `nan`; `rationals` served them — `correct_answer='zoo'` among the options — and
`expressions` raised `TypeError` building distractors. **`is_number` cannot be the test**, since `simplify` exists to
return `5*x`. **And `is_finite is False` cannot be it either: `nan.is_finite` is `None`, not `False`**, so that form
catches `zoo` and the infinities and lets `nan` through. The guard tests both.

### Two budgets, two meanings

**`SOLVE_TIMEOUT` (3 s) bounds the arithmetic; `SOLVE_STARTUP_BUDGET` (15 s) bounds getting there.** There was one
budget and it was the wrong one: it covered launching Python and importing sympy, ~99% of an ordinary solve, so a
nominal 3 s was ~3× margin over *startup* and any co-tenant spent it. With CPU hogs running, **8 of 8 solves were
killed at 3.0 s** — and since a solve failure became `SolverUnavailable`, that was a **503 with no retry** on eleven
topics.

`_solve_worker` prints a readiness line once sympy is loaded and `_run` times the two phases separately. Re-measured, 8
concurrent solves (give the hogs ~5 s to spin up — a 2 s settle measures an unloaded machine and reports a fix that
isn't there):

| load | before | after |
| --- | --- | --- |
| idle | — | **8/8**, slowest 2.2 s |
| saturated (18 hogs / 18 cores) | 0/8 | **7/8**, slowest 30.7 s |
| 2× oversubscribed (36 hogs) | 0/8 | **1/8**, slowest 32.4 s |

**2× oversubscription is not fixed.** What the split buys is *saturation*; past that the machine cannot start
interpreters fast enough. The other half of the trade is the tail: a contended startup that retries holds an anyio slot
for ~30 s where the old code failed in 3.

**Only a *startup* timeout is retried, and the split inverted that.** While the budget was ~99% startup, "timeout"
almost always meant contention. Now the two mean opposite things: a solve timeout is 3 s against ~10 ms of arithmetic,
so it is a spin, and a spin spins again — retrying costs a second full startup to reach the same kill with the student
waiting through both. A startup timeout is the machine failing to launch Python in 15 s, which passes. Worst-case hold
on an anyio slot drops to 18 s absolute rather than 42 s.

Tune them differently: `SOLVE_TIMEOUT` has ~300× margin over the maths and can be tightened; `SOLVE_STARTUP_BUDGET`
absorbs contention, and tightening *it* reinstates the old failure. **Treat any startup multiplier quoted here with
suspicion, including these**: startup is ~0.8 s idle on the machine these were taken on and 1.4–2.2 s under ordinary
background load. `SOLVE_RETRY_BUDGET_FACTOR` is **gone** — it widened the solve budget on a second attempt that no
longer exists, and a knob whose name promises tuning that is not available is worse than its absence.

`_probe_startup()` runs at import: it times one trivial solve and raises the **startup** budget if the configured value
is under `_STARTUP_SAFETY_FACTOR` (3×) of the measurement. It **clamps up and logs; it never refuses to start** —
raising there would take the whole backend down over one topic's tuning knob. A probe that cannot run keeps the
configured value and says so separately: that means the subprocess mechanism is broken, a different problem, and a
budget guessed from a failed measurement is worse than one someone chose. `SOLVE_STARTUP_PROBE=0` skips it. **The probe
must escape the budget it validates** — it runs a solve, so a too-small value made it time out reporting the problem it
should have measured; `_run` takes a `startup_timeout` override for that one caller.

**A threshold that depends on how fast the machine is has to be measured on that machine, not written down.** Both
tests around the probe got this wrong in different directions: one pinned the shipped default against the measured
floor and failed *locally* under suite load; the other hardcoded 1.0 s as "obviously below the floor" — true on a
laptop where startup is ~0.8 s and the floor ~2.4 s, false on CI where startup is 0.32 s and the floor 0.97 s, so the
clamp correctly did not fire and the test failed for asserting it had. **CI is the faster machine here, which is the
opposite of the usual flakiness direction.** Probe first, derive the value from that measurement, leave an order of
magnitude rather than a factor of two, and assert what the probe guarantees — that the *effective* budget clears the
floor — not that the shipped default does.

### Concurrent solves are bounded

Solves contend for CPU, and the budget collapsed for all of them at once rather than degrading: unbounded, 16
concurrent all succeeded, **32 gave 7 of 32, 48 gave 0 of 48.** Nothing bounded it —
`GENERATION_MAX_CONCURRENCY` bounds *model calls*, and prefetch, the inline path and practice reach the worker
independently — so a class starting together took every solve-backed topic down. `SOLVE_MAX_CONCURRENCY` (8) fixes it
by queueing: 48 of 48 now succeed, and 96 of 96 within `SOLVE_QUEUE_TIMEOUT` (20 s). The bound stays after the phase
split for a different reason: it is what keeps a class starting together from putting a hundred interpreters on one
machine.

**The permit is taken per attempt**, not around both: holding one through a wait that has already failed shrinks the
effective concurrency exactly when the machine is busiest.

**Waiting for a slot deliberately does *not* come out of the solve budget** — the opposite of `llm_client`, and for a
stated reason. There the wait is the caller's own deadline. Here the deadline exists to bound a CPU spin, and a spin
does not start until the process does; charging the wait would fail solves that then had no budget left to run in,
under exactly the load the bound exists for.

**Take the permit through `_solve_slot`, never a bare acquire/release** — the refusal raises *inside* the guarded
region, which is the path most likely to run under load. Same rule as `llm_client._generation_waiter`.

**Test the bound by holding the permits, not by measuring a peak across threads.** An earlier version passed against a
build with the bound removed, because the pool happened to start its threads in groups no larger than the bound — a
peak is not deterministic, and a mutation check that passes is worse than no check. What *is* deterministic is that a
caller finding no free slot is refused rather than run.

### `SolverUnavailable`: a solver that could not run is not a bad reply

Five things in `_run` returned `None` and only one was the model's fault. A timeout, a failure to spawn, a non-zero
exit and unreadable output all say nothing about the reply. Collapsed into one `None`, a load-dependent timeout retried
three times — **billing a model call each time that could not possibly help** — and then raised *"Failed to generate
valid JSON after retries"*, a claim about the model for a subprocess that never ran. It surfaced as an intermittent
failure in an unrelated topic's test. It now raises on the first attempt, at one model call instead of three, naming
the cause.

It subclasses `llm_client.GenerationUnavailable`, so both `main.py` call sites already turn it into a **503**. A worker
that ran and refused the input still returns `None` and still retries, because a different reply genuinely might work.
**`_probe_startup` must catch it**: that function runs at import, and raising there would take the whole backend down.

## An answer's subject is the generator's own name, never the model's

Each `LLM_*_generation.py` returns `question_topic`, which `add_question_to_supabase` stores as `questions.subject` —
the column `record_topic_attempt` joins on. Nine generators hardcode their own topic; `rationals` returned
`question_data["question_topic"]`, and its prompt named `"algebra"` in prose and `"rations"` in the JSON example.
Measured 3 of 3 against Haiku: **every fractions question was stored as algebra**, so a student's rationals work was
credited to algebra in `user_math_performance` — the table the adaptive engine reads to choose what to serve next —
while `rationals` accumulated nothing. A subject outside `ALL_TOPICS` is the other half: the join finds no row and the
attempt is attributed to *nothing*, silently, since the helper never raises.

Letting the model name the topic is the same hazard as letting the caller name it, one layer up.
`test_every_generator_stores_its_own_topic_name_not_the_models` pins both halves — the value must be a **literal** (a
generator reading the model's value could still pass a membership check on any given run) and must be in
`ALL_TOPICS`.

**The repair migration's rule is a prose regularity, not a structural fact.** An earlier version of this entry claimed
an algebra question must contain `=` because `_solve_worker` splits on it; it splits `variables`, which is **never
stored** — `questions` has no such column. What is filtered is `question_text`, which the model writes freely. So it
is a pattern observed on a sample and applied irreversibly, and it is built for that: **three signals must agree** (no
`=`, no coefficient-variable `\d+[xyn]`, a fraction present), a row where they disagree is left alone, and every
change is recorded in `question_subject_reclassification` so it can be audited and reversed. That table is why a
heuristic is acceptable here at all — which is also why its `attempts_moved` counts **only** rows that moved
something: a `rations` row credited nothing, so recording its answer count would tell a reversal to push attempts back
onto algebra that were never there.

Over 25 real rows the three signals partition them completely — 19 with `=` and a coefficient-variable and no
fraction, 6 with a fraction and neither. **A fractional answer is not a usable signal** (genuine algebra answers are
frequently fractions, `7/2`, `17/6`) and neither is "mentions x", since one of the six reads *"Solve for x: 3/4 +
2/5"* — a rationals question wearing algebra's phrasing, because the prompt told the model the topic was algebra.
Only the coefficient form separates them.

It moves the `user_math_performance` counters too, and the repair is **all-or-nothing**: if `math_topics` has no
`rationals` row, nothing moves — subject included. Guarding only the counters would let the subject change while the
attempts stayed on algebra, and a re-run would then find nothing to correct, making that inconsistency permanent.

Rows written before the fix still carry the wrong subject; nothing distinguishes them from genuine algebra rows except
the question text.
