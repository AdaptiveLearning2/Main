# Project conventions

AdaptiveLearning is an EEG- and camera-assisted adaptive maths platform: students answer
LLM-generated questions while a Muse headband and a webcam feed cognitive and facial signals into
per-session records; teachers and parents read those back as live views and weekly reports.

## What is here, and what is one file away

This file is loaded in full into every session, so its size is a standing cost — about 40% of the
window when it held everything. The two largest and most self-contained areas are now docs, read
on demand:

| Read | When you are touching |
| --- | --- |
| **`docs/signals.md`** | the EEG sidecar or native bridge, the Muse simulator, ingestion in either mode (`eeg_poller`, `push_client`, `/api/signals/*`, `signal_mapping`), the heart or optics path, the camera, gaze or FER+, or anything writing `cognitive_signals`, `heart_signals` or `face_signals` |
| **`docs/question-generation.md`** | a `LLM_*_generation.py` generator or its prompt, `LLM_topic_decider`, `llm_client` or either provider, a schema in `question_schemas.py`, a solver (`safe_solve`, `geometry_solvers`, `angle_solvers`, `hs_solvers`), grade or topic gating, a lesson plan seed, question figures or CCSS codes, or the difficulty bias on `profiles` |

**Those are trigger conditions, not a table of contents.** Match them against what you are about to
do, before the first edit — the same move `_MODE_AWARE` and `close_sites()` make, and for the same
reason: a rule nobody re-checked is how every stale claim in this file got there. What stays here is
everything that binds regardless of area — the canary, the four numbered rules, `stress` is
`1 − calm`, fusion asymmetry — plus Database, Privacy, and Reporting and UI.

## How to edit this file

It reached 5,000 lines once by accumulating incident reports; these rules are what stop that
happening again, and they apply to the two docs as much as to this file.

- **One entry = one rule, its reason, and where it is enforced.** A third paragraph means it
  belongs in a doc beside the code, cited from here.
- **Write the rule, not the incident.** No date stamps, no PR numbers, no account of what the rule
  used to be. A correction *replaces* the entry it corrects and is never appended beside it.
- **Measurements live in `EEGResearch/tests/fixtures/*.md` and `EEGResearch/docs/*.md`.** Here: the
  verdict and the pointer. Keep a date only on a measurement, or on a decision deferred to a
  future capture.
- **File it under one of the four parts below, or under the doc whose triggers it matches.** An
  entry that binds regardless of area belongs here, not in a doc — and if you have to think about
  which, that is the test: would a session working in the other area need it?
- **Re-check a count or a path before trusting it.** This file has been wrong about `main.py`'s
  size and about seven script paths.
- **Ceilings: 1,200 lines here, 1,200 in each doc.** Past one, a new entry means an older one is
  merged, cut, or moved to a doc beside the code. Numbers, because "keep entries short" has already
  failed once.

The four parts here: **Orientation**, **Database**, **Privacy**, **Reporting and UI**.

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
*The network edge*, the `STRATEGY_*` / `CHART_SUMMARY_*` groups under *The two model-backed panels*,
and the `LLM_PROVIDER` / `CLAUDE_*` / `GENERATION_*` / `SOLVE_*` groups in `docs/question-generation.md`.

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

**A response header the page must read has to be named in `expose_headers`.** Only the CORS-safelisted few are
readable cross-origin by default, and the frontend is a different origin from this API in every deployment — local dev
on `:5173` against `:8000` included. `Retry-After` is not safelisted, so the allowlist alone left it hidden: seven
refusals set it, `apiFetch` reads it to size its wait and then jitters that delay, and unexposed every one of them
falls back to the fixed delay — `retryAfterMs` reads null and the arrival-rate measurement behind
`GENERATION_MAX_WAITERS` describes behaviour no browser performs. Nothing else is exposed; it is a read permission,
granted per header.

**`ENV` names both sides, and an unrecognised value hides the docs.** `== "production"` is silent in the one direction
that matters — `ENV=prod` leaves `/docs`, `/redoc` and `/openapi.json` published with nothing in the boot log saying
so. `_is_production` recognises spellings on both sides and falls to production with a `[config]` line otherwise,
which is the **opposite** fallback direction from `_env_number`: there the safe side is the feature's own default,
here it is publishing less. Unset stays development, silently, since that is the ordinary local state and a warning on
every boot is one nobody reads.

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

## A write names its columns; a request model refuses what it does not declare

**The service-role client bypasses column grants as well as RLS, so a migration that revokes a column's
UPDATE does not reach any statement in `main.py`.** `20260824010000` takes `profiles.role` away from
`anon`/`authenticated`, which constrains PostgREST and nothing here. So **build a database update
from named attributes, never from the payload as a dict**, wherever the table holds a column the
caller must not set: `profiles.role`, `classes.teacher_id`, `classes.join_code`. The named columns
in `update_my_profile` and `update_class` are what keep a posted `role` out of that column; a
`payload.dict()` write leaves the job to the model happening not to declare the field, which is one
edit away from a self-service role change.

**A test of that has to hand the handler more than the model declares.** Against today's model a
named-column write and `payload.dict()` produce identical keys, so a test using the real model passes
either way — it has to simulate the future the guard exists for.

**`StrictModel` carries `extra="forbid"` and every request model inherits it**, which is defense in
depth rather than a live fix (Pydantic v2 already drops an unknown key). `test_input_bounds.py` pins
the list of models that do *not*.

**The six ingest models are exempt, by name.** A sidecar runs on a student's laptop and updates on its
own schedule, so a field it gained before this backend did is ordinary version skew — and under
`forbid` that skew 422s the **whole batch**, losing every valid sample travelling with it. That is
the failure `CognitiveBatch.samples` is already `list[Any]` for. The cost of staying lenient is a
column reading "not measured" for ever, which
`test_every_column_the_mapper_writes_can_be_supplied_by_the_endpoint` already covers.

**Don't add `ge`/`le` to `days` or `weeks`.** All three are clamped in their handlers
(`max(1, min(payload.days, 30))`), which is this codebase's convention for a caller-supplied range. A
field bound turns that clamp into a 422 for the same input — two bounds over one number, the stricter
winning silently. **The decision rests on the clamp existing, so all three are pinned**:
`test_learning_strategies_clamps_the_day_range` for the strategies one, and
`test_the_chart_summary_clamps_both_of_its_ranges` for the other two — citing only the first left two
thirds of the argument resting on nothing, and deleting either chart-summary clamp passed.

**A cap on a free-text field is that field's only bound, not a nicer error.** Every column they guard
(`display_name`, class `name`, session `title`) is unbounded `text` in the schema, so there is no
database limit being converted into a 422 — Postgres would have stored a megabyte. That is the
argument for the caps, and describing them as error-shaping overstates the schema and understates
them.

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
