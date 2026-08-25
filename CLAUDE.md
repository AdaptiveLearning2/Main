# Project conventions

AdaptiveLearning is an EEG- and camera-assisted adaptive maths platform: students answer
LLM-generated questions while a Muse headband and a webcam feed cognitive and facial signals into
per-session records; teachers and parents read those back as live views and weekly reports.

**Keep this file current.** This is the only thing loaded into every chat, so anything a future
session must know before it starts — a changed convention, a new constraint, a gotcha that cost
someone an afternoon — belongs here, as the rule and its reason. Update it in the same change that
makes it true, not afterwards. Keep entries short; it is read in full every time.

## Layout

| Path | What it is |
| --- | --- |
| `Website/AdaptiveLearning/backend` | FastAPI app (`main.py`, ~2.3k lines) — the product API on port 8000. Also the `LLM_*_generation.py` question generators and `LLM_topic_decider.py`, which reach a model through `llm_client.py` — a local Ollama by default, the Claude API when `LLM_PROVIDER` says so. |
| `Website/AdaptiveLearning/frontend` | React 19 + Vite + Tailwind SPA on port 5173. Routed by role: `src/pages/{student,teacher,parent,auth}`, one layout each. `src/lib/api.js` wraps the backend; `src/lib/supabase.js` holds the anon client. |
| `EEGResearch` | Separate FastAPI sidecar on port 8001 (`src/app`), packaged as `eeg-learning-platform`. Owns headband access and signal derivation; the website backend talks to it over HTTP only, via `backend/eeg_client.py`. |
| `EEGResearch/native_bridge` | C++ bridge to the libMuse SDK, TCP on 8765. Windows-only (`winsock2`), and the interesting half is behind `ENABLE_LIBMUSE`. |
| `FacialRecg` | Vendored rPPG / facial-recognition reference code. |
| `supabase/migrations` | The schema. Timestamp-prefixed, applied in order. |

Two backends, deliberately: the website backend never reads a headband directly, and every caller
gates on `eeg_client.is_alive()` first, so the whole EEG stack is optional at runtime. Don't add a
hard dependency on port 8001 to a path that must work without hardware.

## Running and testing

Whole stack, Windows (Ollama, EEG sidecar, backend, frontend, each in its own window):

```bash
./start.ps1
```

Add `-Muse` for the real headband — it builds the native bridge if needed, copies `libmuse.dll`
next to the exe, and flips `EEG_SOURCE` in `EEGResearch/.env`. Without it you get `EEG_SOURCE=sim`.
`-Camera` adds the webcam device (`-CameraIndex N` picks one), `-Gaze` additionally enables the
landmark channel and implies `-Camera`, and `-NoEmotion` turns FER+ off — gaze needs no 35 MB model,
so gaze-only is a real and much cheaper deployment. Each model-backed flag provisions its model at
setup rather than on the first frame of a lesson, and `-NoEmotion` skips the FER+ fetch entirely.
`-Optics` turns the headband's optical channels on (`-OpticsPreset 103N` picks the rung) and is
refused without `-Muse`, rather than promoted the way `-Gaze` promotes `-Camera`: the alternative to
a headband is the simulator, which models no optical channel, so guessing would produce a run that
looks exactly like the flag not working. A preset outside `1031`–`1036` is refused too — the bridge
falls back to `1035` and says so on its own stderr, in its own window, so the session would record
on a rung nobody chose. `1031`/`1032` warn and proceed, since reproducing the cliff needs them.
**Gaze needs `pip install -e ".[face,gaze]"`** — MediaPipe is its own extra, deliberately, since it
is ~50 MB and a second ML runtime for a channel that is off by default. **`face` pins `opencv-contrib-python`, not
`opencv-python`** — they install the same `cv2`, contrib being the superset, so having both means
whichever landed last owns the import. That is what `.[face,gaze]` produced: mediapipe requires
contrib, `face` required plain, and the resolver installed 4.14 of one beside 5.0 of the other,
silently defeating the `<5` cap. One distribution, one version. The cap's stated reason —
`cv2.data.haarcascades` and the CAP_PROP_* constants — is behaviour no test here covers, so
`verify_landmarks.py` now cross-checks the Haar cascade against the mesh on the same frames: a Haar
miss alone is ambiguous (lighting, framing), a Haar miss where the mesh saw a face is not. The scripts check for it
whenever gaze is asked for, because `ensure_model` imports nothing heavy: without that check setup
succeeds, writes `FACE_GAZE_ENABLED=true`, and the channel dies on the first frame of a lesson as
`landmarker_unavailable`, indistinguishable from a missing model file.
Every `FACE_*` key is written on **both** branches, `FACE_EMOTION_ENABLED` included: its config
default is `true`, so leaving it unwritten made emotion silently on whenever the camera was and put
a third of the camera's configuration in a Python default rather than in the `.env` a reader checks.
`-Camera -NoEmotion` without `-Gaze` is refused — the adapter would refuse it too, and a flag
combination is a better place to say so than a sidecar that starts and then will not connect.
`start.sh` is the mac equivalent and is kept at flag parity (`--gaze`, `--no-emotion`, the same two
guards); per-machine setup lives in `DEVELOPER_SETUP_{MAC,WINDOWS}.md`.

**Guard every read of a `.env` in `start.ps1` with `Test-Path`.** `Set-EnvKey` returns silently when
the file is missing, so nothing before the read notices, and `Select-String -Path` on a missing file
is a *terminating* error under this file's `$ErrorActionPreference` — a first-ever `-Camera` run on
a fresh checkout aborted the whole launcher before anything had started. Guard the *match* too:
`.Matches[0].Groups[1]` on a key that is not there indexes a null array, which fails the same way one
step later. `start.sh` carries the same guard for parity.

**Never redirect a native command's stderr in `start.ps1`.** PowerShell 5.1 wraps each stderr line
from an exe in an ErrorRecord, and `$ErrorActionPreference = "Stop"` at the top of the file makes
that *terminating* — so `python -c "import cv2" 2>$null` killed the script at the failing import,
before the block that exists to explain it, and surfaced as a bare `NativeCommandError` naming
neither the module nor the fix. Silence it inside Python instead
(`import sys, os; sys.stderr = open(os.devnull, 'w'); import cv2`) and probe **one module per
call**, so the error can say which import failed. Applies to every dependency check in that file.

**Two venvs exist and only one is the sidecar's.** `EEGResearch/.venv` is what `start.ps1` uses;
there is also a `.venv` at the repo root with a different OpenCV. `pip install -e ".[face,gaze]"`
has to run *from* `EEGResearch`, or pip resolves `.` to the repo root and reports "neither setup.py
nor pyproject.toml found".

**Both venvs are rebuilt on Python 3.14.7** (2026-08-20; previously 3.12/3.13). Every direct
dependency in both trees already ships a `cp314`/`win_amd64` wheel or a version-agnostic
`py3-none-any` one — `mediapipe`, `opencv-contrib-python`/`opencv-python` (the `<5` pin still
resolves), `onnxruntime`, `numpy`, `scipy`, `jax`/`jaxlib`, `h5py`, `av` all installed clean.
`EEGResearch/.venv` (`pip install -e ".[dev,face,gaze]"`) passed all 557 tests; the root venv
passed all 814 backend tests, and `keras`/`jax` load fine once `KERAS_BACKEND` is set the way
`rppg/models.py` already sets it at import. **Run `pytest` from the repo root, not from
`EEGResearch`** — `Settings` loads `.env` relative to cwd, and a locally edited
`EEGResearch/.env` (e.g. `FACE_EMOTION_ENABLED=false` left over from a camera-off `start.ps1`
run) silently overrides field defaults for any test that constructs `Settings()` without passing
that key, which reads as a code regression and isn't one.

One pre-existing gap, unrelated to the version bump: neither venv has ever carried `setuptools`
(Python's `venv` module stopped bundling it), so `import rppg` / `import heartpy` fail on a
missing `pkg_resources` if run directly against either persistent venv. Not a regression — the
`open-rppg` measurements in `EEGResearch/docs/RPPG_DEPENDENCY_COST.md` were always done in a
throwaway `pip install --target ... "setuptools<81"` env for exactly this reason, never against
the root venv.

Individually, from each directory:

```bash
uvicorn main:app --reload --port 8000
```

```bash
uvicorn src.app.main:app --host 127.0.0.1 --port 8001 --reload
```

```bash
npm run dev
```

Tests — all five jobs run in CI (`.github/workflows/ci.yml`) on PRs and pushes to `main`:

```bash
python -m pytest tests/ -q
```

```bash
npm test
```

Backend tests need `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`; they never reach a real
database, but the client validates the URL at import, so `SUPABASE_URL` must be URL-shaped
(`http://localhost:54321`) — a placeholder like `x` fails collection with "Invalid URL".
`SUPABASE_SERVICE_ROLE_KEY` can be anything non-empty. EEGResearch tests need `EEG_SOURCE=sim`,
`API_TOKEN`, `ADMIN_TOKEN`.

### Frontend tests mock through `src/test/`, not through a hand-rolled `vi.fn()`

`src/test/mocks/apiFetch.js` and `src/test/mocks/supabase.js` are the shared doubles, reached by
pointing the factory at the file so the mocked module and the handle driving it are one instance:

```bash
vi.mock('../../lib/api', async () => await import('../../test/mocks/apiFetch'))
```

**`apiFetch`'s double is a router, and an unmatched path throws.** Most pages here fetch two to four
endpoints in parallel on mount, and the interesting tests need *one* to fail. `mockResolvedValueOnce`
chains express that by call order — which is the order `Promise.all` happens to start them in — so a
test written that way passes for a reason unrelated to what it claims and breaks when a page adds a
fetch. `mockApi({...})` registers the happy path, `overrideApi(path, fn)` layers one failure over it.
Throwing on an unrouted path is the load-bearing part: a silent `undefined` reaches a page as a
successful read of nothing, which is the state most of this suite exists to tell apart from a
failure, so a gap in a test's own setup would arrive dressed as the bug it was written to catch.
**Method-scoped routes are tried before methodless ones**, whatever order they were written in;
first-match-wins alone made `{'/api/x': …, 'PUT /api/x': …}` answer the write with the read, fixable
only by reordering two object keys — not a rule anyone would infer. Reset with `resetApi()`, never
`mockReset()`, which drops the implementation and every route with it.

Mocking `lib/supabase` as a *module* also sidesteps its import-time throw on missing
`VITE_SUPABASE_*`, which is the normal state under `vitest` — CI supplies those to the build step
only. `fireAuthEvent(event, session)` is how the properties that only exist post-mount are reached:
the `SIGNED_OUT` cleanup an expired refresh token triggers with nobody calling `signOut()`, and the
`TOKEN_REFRESHED` handling that must not await anything reading the session.

**`lib/api.test.js` is the one place `apiFetch` runs for real**, with only `fetch` and `lib/supabase`
mocked. Every other test replaces it wholesale, so nothing otherwise exercises the URL it builds,
whether the bearer is attached, or how a non-2xx becomes an `Error` carrying `.status` — the
behaviour all of those tests implicitly trust. Fixtures live beside the mocks in
`src/test/fixtures/` as builders rather than constants (`buildWeeklyReport`, `buildConsentState`,
`buildChartArchive`, …): every interesting case is one field off the happy path, and a test that
restates a whole payload to move one field tends to move two. `CHANNEL_REASONS` there is the
`offLabel` four-state matrix, named for the state each input must produce rather than for its field
values, since that mapping is the thing under test.

**A daemon thread that prints must be joined before the process exits.** A print landing during
interpreter shutdown, while the stdout `BufferedWriter` lock is already held, is a fatal
`_enter_buffered_busy` abort — exit code 134 *after* every test passed, which reads as unrelated
flake. `eeg_poller.stop_all()` is the join, called from `main._lifespan` on shutdown and from an
autouse fixture in `backend/tests/conftest.py` after every test. Loops in such threads wait on the
stop event rather than `time.sleep`, so `stop()` is not a poll interval away from taking effect.

The native bridge is compile-checked on `windows-latest` with `ENABLE_LIBMUSE=OFF`, which covers
syntax and signatures but *not* the packet handling inside the guards — that still needs a manual
Windows build with the SDK before release. The SDK is vendored (and gitignored) at
`EEGResearch/libmuse_windows_8.0.5`, so that build is a local `cmake` away and worth running on any
change inside an `ENABLE_LIBMUSE` guard:

```bash
cmake -S . -B build_on -DENABLE_LIBMUSE=ON -DLIBMUSE_SDK_DIR=../libmuse_windows_8.0.5 && cmake --build build_on --config Release
```

It compiles what CI cannot: enum values, SDK signatures and the guarded packet handling. It still
proves nothing about a real headband.

`npm run lint` is non-blocking in CI against a backlog of **11** pre-existing errors, none of them
`no-unused-vars` or `react-hooks/set-state-in-effect`. Don't add to it, and don't make it blocking
until the backlog is gone.

**`set-state-in-effect` is cleared, and the two shapes that cleared it are worth reusing.** Where the
state is a reset driven by a prop changing — an acknowledgement cleared when enforcement resumes, a
pulse started by a new timestamp — adjust it *during render* against a `useState` holding the
previous value, which React re-runs before painting. Where it is a `loading` flag around a fetch,
don't store one: keep the key the data in hand belongs to (`loadedFor`) and derive
`loading = loadedFor !== id`, so switching session or class raises the skeleton on the render that
changes the id and no previous subject's charts can be painted under this one's heading. A flag
raised by a *user action* stays a flag — `Sessions.jsx` sets it in the class selector's `onChange`,
which is an event handler and not an effect.

Both shapes have since bitten, and the corrections are the load-bearing half:

- **Derived `loading` needs a remount, not just a derivation.** `loading = loadedFor !== id` reads
  *false* when you navigate A→B→A: B's request is cancelled on the way out without ever advancing
  `loadedFor`, so returning to A finds it still saying `'A'`. `SessionReview.jsx` therefore keys the
  body on the id (`<Body key={sessionId} …>`), which resets every piece of session-scoped state at
  once — including the `err` that otherwise let a failure on A mask a B that loaded fine.
  `ChildDetail.jsx` does the same, and this is now the pattern for any page whose whole state
  belongs to one route param.
- **The render-time adjustment compares against the previous *render*, and that is not always the
  question.** `useValueChange` (`hooks/useValueChange.js`) is the extracted form and is right for
  `Flags.jsx`. It was wrong for `FlowDot.jsx`, which needs the last value it *acted on*: the pulse
  timer clears the live state, so a timestamp that goes transiently null and comes back unchanged
  reads as a change and flashes "fresh data" for data that is not new. Keep the acted-on value in
  its own state that nothing else clears. A hook parameter nobody reads is the tell.
- **Deriving state does not remove the need to cancel.** Every fetch that can be superseded needs a
  guard, and the slow ones are where it matters: `Sessions.jsx`'s roster read fans out per student,
  so a class switch let the previous class's response land last and repaint the list under the new
  class's name. It uses a generation ref rather than a cleanup flag, because the effect is not the
  only caller — the retry button is the other, and a retry is exactly when someone changes class
  rather than waiting.

**`react/jsx-uses-vars` is the only rule from `eslint-plugin-react` that is on, and it has to stay
on.** `no-unused-vars` cannot see JSX, so without it every identifier used *only* inside markup —
`motion` from framer-motion, an `icon: Icon` prop rendered as `<Icon />` — is reported as an unused
import. That was **40 of the 65** errors the backlog held, all false, and the noise is what hid the
real ones: the same sweep found one genuinely dead `motion` import that had been sitting among 33
identical false positives. The plugin's `recommended` config is deliberately *not* extended — it
brings a large ruleset that would add to the backlog rather than clear it.

`ignoreRestSiblings: true` goes with it, for the destructure-to-omit idiom (`const { x, ...rest } =
obj` to build an object *without* `x`, which is how the tests construct a payload predating a
field). The binding is unused by design; deleting it to satisfy the rule would put the key back.

With both, `no-unused-vars` is now **clean and therefore load-bearing** — a hit is real dead code,
so fix it rather than adding it to the backlog.

Dependencies are pinned: `backend/requirements.txt` (runtime, direct deps only, cross-platform by
design — no `pip freeze`), `requirements-dev.txt` pulls it in and adds pytest. EEGResearch uses
`pyproject.toml` plus `requirements*.lock`.

### The Supabase CLI is a repo-local npm install

It is **not on `PATH`** — `which supabase` and `Get-Command supabase` both report it missing, which
looks like "not installed" and isn't. It lives at
`node_modules/@supabase/cli-windows-x64/bin/supabase.exe` (platform-suffixed, so the directory name
differs on mac). Run it through npx from the repo root:

```bash
npx supabase migration list --linked
```

`supabase/.temp/project-ref` holds the linked project ref, which is what `--linked` resolves
against.

### How a migration reaches production

**Merging to `main` applies the migration to production, a few minutes later.** The Supabase
GitHub integration does it — it is configured in the Supabase dashboard, which is why nothing in
`.github/workflows/` describes it. Confirmed on `20260801000000` and `20260803000000`; there is
nothing to run by hand, and `npx supabase db push` answers "Remote database is up to date".

The **"Supabase Preview"** check on PRs comes from that same integration and verifies nothing:
per-PR preview branches are switched off in the project's integration settings, so it reports
`skipped` every time. Never read it as the migration having been exercised.

**CI applies the migrations; it does not gate the merge.** The `Database migrations` job applies
every migration to an empty local Supabase stack, so one that cannot apply goes red on the PR.
Nothing enforces that — branch protection and rulesets need a paid plan on this private repo, so
every job in `ci.yml` is advisory and a red PR still merges. Read the check before merging; it is
the only thing standing between a laptop-only migration and production. It proves the SQL
*applies*, not that the grants below are right.

**The delay is the trap.** It is minutes, not seconds, so a check run straight after the merge
reports the migration as *not applied* — Local populated, Remote blank — and that is
indistinguishable from an integration that never fired. Don't conclude anything from one look
immediately after merging; re-check before acting on a negative:

```bash
npx supabase migration list --linked
```

A version in Local and absent from Remote has genuinely not landed only if it stays that way. That
command is the answer to "did the migration land", and it is worth running before any deploy whose
code depends on a new signature — see the deploy-ordering rule below.

It confirms only that the migration *ran*. The CLI has no arbitrary-SQL command, so verifying the
resulting schema — policies, ACLs — means the dashboard SQL editor. The local `.env` files point
at a local stack, not production, so nothing in the working tree reaches the production database.

## Configuration

Backend: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (required), `BACKEND_PORT`, `EEG_API_URL`,
`EEG_API_TOKEN`, `EEG_ADMIN_TOKEN`, `EEG_POLL_HZ`, `INGEST_MAX_BATCH` / `INGEST_RATE_LIMIT` /
`INGEST_RATE_WINDOW`, the `STRATEGY_LLM_*` / `STRATEGY_RATE_*` group below, and the
`LLM_PROVIDER` / `CLAUDE_*` / `GENERATION_*` group under *Every model call goes through
`llm_client`*. The ingest bounds
matter because the sidecar posts with the *student's* token: that endpoint is a trust boundary, and
neither the session check nor the consent check bounds volume. Frontend: `VITE_SUPABASE_URL`,
`VITE_SUPABASE_ANON_KEY`, `VITE_API_URL`, `VITE_EEG_DEBUG`. EEGResearch reads `.env` through
`src/app/config.py` — `API_TOKEN` and `ADMIN_TOKEN` required, `EEG_SOURCE` picks sim vs muse,
`EEG_DEVICES` (`station1:muse@8765,...`) drives the multi-headband registry. The native bridge reads
its own env directly, not through `config.py`: `MUSE_BRIDGE_PORT` (8765), `MUSE_ENABLE_OPTICS` (off)
and `MUSE_OPTICS_PRESET` (`1035`).

Read numeric settings through `_env_number(name, default, cast, minimum=...)`, never
`int(os.getenv(…))`. These are read at import, so a typo would otherwise take every endpoint down
over a tuning knob for one optional feature. It falls back on unparseable and non-finite values
(`inf` passes a `minimum` check, `nan` fails every comparison, and both break call sites in ways that
look like the feature being off) and clamps below the floor. **Give every one a floor:** a number is
not automatically a usable setting.

**`MUSE_ENABLE_OPTICS` stays off unless you are testing the heart channel.** On it, a 2025 Athena
moves off `PRESET_21` onto an optics-carrying preset — a bandwidth trade with a sharp edge. Measured
on hardware: 4 CH EEG at 256Hz alongside **16** CH optics at 64Hz drops the BLE link within ~20s
*and* collapses electrode contact from `[1,1,1,1]` to `[4,4,4,4]`; 8 CH and 4 CH hold for minutes at
~63 packets/s. `MUSE_OPTICS_PRESET` picks the rung — `1031`/`1032` 16 CH, `1033`/`1034` 8 CH,
`1035`/`1036` 4 CH (odd = low power). The default sits at the bottom deliberately: the 16-channel
failure took EEG down with it. An unrecognised value warns once and falls back.

**Turn it on with `./start.ps1 -Muse -Optics`, not by editing a `.env`.** The bridge is a C++
process that reads `getenv` directly and never loads `config.py`, so a `MUSE_ENABLE_OPTICS` line in
`EEGResearch/.env` is read by nothing — the version of this mistake that looks like it worked. The
flag sets the variable in the window that launches the exe. Same for `MUSE_OPTICS_PRESET`, and for
`MUSE_BRIDGE_PORT` if it ever needs one.

### Battery is device telemetry, and null for the first stretch of every session

`battery_percent` rides on the bridge's ingestion block through to the badge beside Disconnect on
the student page. Registered on **every** preset, not just the optics ones — libMuse fires BATTERY
on its own schedule rather than as part of a preset's stream, so it costs nothing on `PRESET_21`.

**Null until the first packet arrives, which is most of the first minute.** That is normal, not a
fault, and it is why the badge renders nothing rather than `--%`: a permanent empty slot reads as a
broken sensor. The three-state rule applies with unusual force here because **0% is a real and
alarming reading** — `pct || null` anywhere on this path erases exactly the value the badge exists
for, so the checks are `typeof pct === 'number'` and `!= null`. The bridge stores −1 for "not
reported" and `main.cpp` turns that into JSON null; `EEG_SOURCE=sim` reports null too, on the same
grounds as it emitting no heart block.

Cleared on disconnect in both places — `reset_device_fields_locked` and the page's own state. A
charge percentage left standing describes the headband that just went away, and it is the one
number here a student is asked to act on.

### Headband BPM: cleared seated, fails under gait

Two regimes with different mechanisms, and the rule is scoped to the right one.

- **Seated — cleared.** Against a simultaneous watch ECG (2026-08-09): **14 of 16 windows accepted,
  max error 2.1 bpm** against a true 70–72. That is a student at a desk, and it is good enough to
  record and to act on.
- **Desk fidgeting — degrades into refusal, not error.** 12 of 16 windows rejected at confidence
  0.00–0.50; the 4 accepted were within 7.5 bpm. The *watch's own ECG* failed one of three attempts
  with `Poor recording`, so a medical-grade contact sensor could not cope with movement the headband
  survived while correctly reporting that it could not.
- **Gait — confident error.** Through exercise `ppg_processing` reported 162–167 bpm at **confidence
  1.00** for six consecutive windows against a watch-verified 104: step cadence. 166/104 = 1.60, no
  harmonic relation, so no periodicity test sees it and four have been tried.

Confidence discriminates in the second case and not the third because running supplies a *sustained
clean rival oscillator* for the autocorrelation to lock onto, while fidgeting merely destroys the
pulse. The accelerometer remains the only signal independent of the periodicity being confused, and
is what a walking-around deployment would need — it is not a prerequisite for a maths lesson at a
desk.

Two limits worth keeping in view: the seated validation is one adult over three minutes, not a child
over a lesson; and 7.5 bpm at high confidence is harmless for fusion (which can only ease difficulty)
while being a real if modest error on a parent-facing chart. Evidence and the failed discriminators:
`EEGResearch/tests/fixtures/README.md`.

### Camera rPPG is validated-and-rejected. `FACE_HEART_ENABLED` stays off

Against a simultaneous watch ECG (2026-08-08): **47.7 bpm at confidence 0.74 against a true 88**,
over five minutes with the face found in 8988 of 8988 frames, autocorrelation peak 0.02 where a real
pulse gives 0.3–0.7.

**That scope was right, and it is now closed — including for the RLAP weights, so don't chase the
licence.** The claim was only that the pulse is not recoverable *from the mean RGB of our three ROI
boxes by POS*, since a learned model over per-pixel input has far more to work with. All of it was
then run against ECG (2026-08-14) on a paced-breathing capture where the true rate rose **16 bpm**:

| | moved, vs truth's +16 | r against the 5 strips |
| --- | --- | --- |
| POS | −8.9 | +0.36 (that is raw green) |
| `RhythmMamba.pure` | −0.3 | +0.30 |
| `RhythmMamba.rlap` | +1.4 | +0.37 |
| `FacePhys.rlap` | +9.2 | +0.21 |

**None tracks the rate, and the raw green channel scores as well as any of them** (n=5 needs |r|>0.88
for significance; all four are noise). `FacePhys.rlap` is the package's best model on the largest
dataset and reported **128.8 bpm for a true 89**. Correlate ungated — the confidence gate is
inapplicable to a single channel, so letting it discard windows throws away the only test there is.
The remaining suspect is the camera's own temporal denoising: **31.4% of consecutive frames carry
bit-identical ROI means**, ~20 distinct frames per second inside a 30 fps stream. Testing that needs
a camera exposing raw frames, not another model.

**Between-half comparisons on a paced capture are confounded** — deep breathing moves the chest and
head as well as the heart rate, so a model responding to breathing motion produces the same
signature as one tracking a pulse. `FacePhys.rlap`'s +9.2 is exactly that shape, and it collapses to
r = +0.21 within the half, where breathing is constant. Correlate against strips inside one
breathing regime, never across the switch.

**A narrow-range capture cannot validate this, and one nearly passed a broken method.** Over the half
where the truth held near 68, RhythmMamba accepted 70/83 windows at a median error of **−5.9 bpm** —
a shippable-looking number from a model that emits ~62 whatever the heart does. Score any future
attempt against a *moving* truth: paced breathing (4 s in, 6 s out) swings the rate 10–20 bpm while
the subject stays seated and still, which is what caught this. A capture whose rate never moves
cannot tell a measurement from a constant.

**Always compare against the best constant, never against zero.** `.rlap`'s errors are +6.0 at rest
and −8.6 elevated, which reads as "works for resting and slightly elevated" and is instead what
emitting ~75 produces when the truth sits either side of 75. Scored properly it has **MAE 8.5 against
a best-constant 5.8** and **r = −0.14** — *a model that always answered "68" beats all four front
ends*, and `.rlap` is worse than a flat 75 exactly in the elevated half (12.8 vs 9.2). Single-digit
absolute error is not evidence of measurement when the truth barely leaves the predictor's output.

**An illuminant that changes colour breaks POS at the premise, not at the noise floor.** A television
in the room put chromaticity CV at 5.00% against 0.20% with it off, with a colour jump every ~0.6 s;
POS projects onto a plane chosen for a *fixed* illuminant. Check chromaticity stability before
blaming a result on the method. It is also not a lighting-level problem: in-band fluctuation on the
clean capture is 0.533% of mean against a photon-noise floor of 0.03–0.12%, so more light lowers a
floor nothing is limited by. Raw R/G/B of those means show the same as POS, so POS is not at fault.

**Nor is the licence a blocker, and `.pure` is why.** Both `zizheng-guo/RhythmMamba` (Zou et al.,
AAAI 2025) and `KegangWangCCNU/open-rppg` are MIT and ship pretrained weights; the `.rlap`/`.pure`
suffix is the training protocol. The Data Usage Agreement is on the **RLAP dataset**, not the
weights — you need it to train on or evaluate against RLAP, not to run inference. What is unresolved
is whether that agreement reaches *derived* weights, and nobody here has read its terms. `.pure`
weights avoid the question entirely, which is the cheaper path for a commercial product used by
children.

`RhythmMamba.pure.weights.h5` is published. **`FacePhys` is `.rlap`-only and is the package default**
(`rppg.Model()` with no argument), so the model that comes for free is the one with no RLAP-free
alternative — name the model explicitly. Every live selection in `FacialRecg/` pins `.pure`;
`ubfc_rppg_exp_dataproc.py` is the deliberate exception, since it sweeps the whole grid and its
committed report would otherwise be unreproducible. Nothing has been *run* since the switch —
`open-rppg` has never been installed here — so treat `.pure` as licence-safe, not as measured.

What actually stands in the way is engineering and evidence, and one of the three blockers now has
a number. Measured 2026-08-12: `open-rppg` costs **~600 MB installed** beyond what the camera path
already brings (jaxlib alone is 252 MB), `import rppg` takes 5.3s and loading `RhythmMamba.pure`
another 27.5s — about **34s of start-up** on a student's laptop. It also imports `pkg_resources`,
removed in setuptools 81, so adopting it means pinning a deprecated setuptools. `.pure` weights do
load, so the licence-safe path is real rather than theoretical. The ONNX escape route — onnxruntime is already
a dependency, so exporting the weights would drop nearly all of that — was tried and **does not
currently work**: `jax2onnx` cannot convert `Fusion_Stem`'s channels-last 3D convolution, and the
model hardcodes that layout. It fails *before* reaching the Mamba scan, so whether the scan exports
is still unknown; don't read the conv error as the only obstacle. **The ONNX export works and the cost objection is gone**:
`scripts/export_rhythmmamba_onnx.py` patches a vendored `open-rppg` (~20 lines: the JAX-only
`.at[].set()` in `Block_mamba`, Mamba's grouped Conv1D, and `Frequencydomain_FFN`'s RFFT, none of
which tf2onnx converts) and emits a 22 MB model that runs under **onnxruntime alone** — already a
dependency — loading in 1.5 s against ~34 s, and matching **the unpatched package** at
correlation 0.99985 — measured against a baseline captured *before* patching, because comparing the
export to the patched model only proves it reproduced what it was exported from. Inference is 0.97 s
per 160-frame window, about 6× real time on CPU. The `.onnx` is not committed: it derives
from weights whose licence terms are the authors', and the script regenerates it. **This settles the
cost, not the accuracy** — that still needs the video + ECG capture, and the POS rejection stands.
`scripts/capture_face_video_ecg.py` is that capture: 128×128 face crops (what the model takes),
lossless because every lossy codec discards exactly the variation rPPG reads, and it **refuses to
write inside the repo** — this is the one artefact that must never be committable, and `git add -A`
does not ask. `--delete` clears the frames and stamps the header, since a cleaned-up capture with no
trace is indistinguishable from one nobody cleaned up. The `.npy` is **trimmed on close to the frames
actually captured**: it is allocated for the worst case, `open_memmap` zero-fills, and an untrimmed
tail reads back as black frames rather than as absent data — which a windowing script would feed to
the model as a sharp non-physiological edge. Numbers and method: `EEGResearch/docs/RPPG_DEPENDENCY_COST.md`. The gate below still has to be designed and *measured*
against a reference, which is unchanged and still needs a capture.

**The part that generalises past this webcam: `ppg_processing`'s confidence does not apply to a
single-channel source.** Its three terms were built for four contact channels — `agreement` is 1.00
by construction against one waveform, `margin` is highest exactly when there is no rival structure to
beat, and noise scored an snr of 0.314, inside the range the code documents as a clear pulse. The
gate is not weak, it is **inapplicable**, and better hardware would not fix that.

The camera ships **emotion-only**. POS is kept because it is correct and is the front half of any
future attempt; do not read its passing tests as evidence it measures a heart rate. Full analysis:
`EEGResearch/tests/fixtures/FACE_RPPG_ECG.md`.

### `attention` has no producer; `gaze_x`/`gaze_y` now do

**Gaze and head pose are wired** (Phase 11 step 2). `FaceCaptureAdapter` runs the face-mesh
landmarker on its own `GAZE_INTERVAL_S` cadence — 5 Hz, not the frame rate, because it is a *second* detector doing its own
face detection rather than reusing the Haar box — and `build_face_record` carries the reading.
`FACE_GAZE_ENABLED` is **off by default**: it needs `models/face_landmarker.task`, which is not in the
MediaPipe wheel. Turn it on with `./start.ps1 -Camera -Gaze` (or just `-Gaze`, which implies
`-Camera`) — that fetches and checksums the model at setup, exactly as it already does for FER+,
because a 4 MB download in front of a student's first lesson looks like a broken feature rather than
an incomplete install. The sidecar deliberately **never** fetches it itself; `ensure_model` is a
setup-time call and `FaceMeshLandmarker` only ever refuses.

**The URL is pinned to `/1/`, not `/latest/`.** Google serves both and they are the same bytes today,
but a checksum pinned against a moving URL fails on the next release *as a checksum mismatch* — which
reads as a compromised download rather than an upstream version bump.

The digest is re-checked when the landmarker loads, not only at setup: `ensure_model` protects the
moment of install and nothing after it, and a truncated or hand-swapped `.task` would otherwise
produce landmarks that are wrong rather than absent.

**A missing model costs gaze, not the camera.** `connect()` tolerates a landmarker it cannot build,
logs, and lets the channel report `rejected_by="landmarker_unavailable"`. That is deliberately unlike
the emotion classifier beside it, which is allowed to refuse the whole device: emotion is the
camera's primary measurement, gaze is an opt-in extra nothing yet renders, and taking heart and
emotion down over a hand-edited `.env` is the wrong trade. The channel stays *enabled* while
unavailable — reporting it as off would be a false claim about how the deployment is configured.

Three things about that path are load-bearing:

- **It samples before the Haar early-return.** A Haar miss says nothing about whether a mesh is
  available, so returning early on one would make gaze silently depend on a detector it does not use —
  and it would fail exactly on the faces that are hardest to find. `_sample_gaze` also never raises,
  because it runs *before* the colour sample and an escaping exception would cost the heart channel
  every frame.
- **Emotion and gaze are two measurements, so they get two refusal fields.** `rejected_by` stays the
  emotion refusal and `gaze_rejected_by` is its own, exactly like `rmssd_rejected_by` on the heart
  block. Collapsed into one, a refused gaze on a well-classified face explains the wrong null.
- **A reading is an emotion *or* a gaze.** `push_client` gated on `emotion is not None`, which was
  right while emotion was the only measurement here; unwidened, a window where FER+ refused and the
  landmarks did not is dropped. It still refuses when *both* refuse, or the all-null flood that gate
  was added to stop comes back.

**`gaze_x`/`gaze_y` are eye-in-head, so they need `head_yaw`/`head_pitch`/`head_roll` to mean
anything about where a student is looking.** Point-of-regard is head pose plus eye offset; with only
the second term, a student turned 30° away with centred eyes reads as `gaze_x ≈ 0`, identical to one
facing the screen. `20260820000000` adds the three pose columns and `head_pose()` — already verified
against a camera — fills them on the same landmark call. **A column here needs a field on
`main.FaceSample` or it can never be stored**: `/api/signals/face` is the *only* writer of
`face_signals` in either `INGEST_MODE` (the poller never writes it), and Pydantic drops undeclared
keys silently — so the sidecar posts them, the endpoint discards them before the handler runs, and
the column reads as "not measured" for ever. That happened to these three with every hop between the
landmarker and the mapper wired and tested. `test_every_column_the_mapper_writes_can_be_supplied_by_the_endpoint`
derives the check from the mapper so the next column cannot fail the same way. They refuse *independently* of gaze (near
profile the fit refuses while the eyes are readable; a closed eye refuses gaze while the pose is
fine), so `pose_rejected_by` is its own field beside `gaze_rejected_by`. Pose is deliberately **not**
in the rollup: averaging an angle over a day is close to meaningless — ±40° of swinging averages the
same 0 as never moving — so the useful aggregate is time-past-a-threshold, and that threshold belongs
with whatever first renders it. Until then pose and gaze both expire with nothing summarising them.

Gaze keys are **absent** when the channel is off, `None` + a reason when refused, a number when
measured — the same three states as everything else here. 0.0 is a valid gaze (dead centre), so a
refusal must never be recorded as one. A landmarker that raises stores
`rejected_by="landmarker_failed"` rather than leaving the reading unset: unset reads as `no_reading`,
which is the *warming-up* state, so a corrupt model would otherwise claim to be starting up for a
whole session.

**`face_signals` is the one signal table with two producers, so its counts are per *measurement*, not
per row.** `rollup_signal_day`'s `'emotion'` channel takes `sample_count` as
`count(*) FILTER (WHERE emotion IS NOT NULL)` (`20260819000000`) — unlike the cognitive and heart
channels, which count every row in their table, because those tables have one producer each. A window
where the landmarker read a gaze and FER+ refused is a real face row with no emotion in it, and
counting it would make enabling gaze read as *emotion coverage improving* in the summary that
outlives `expire_signal_rows`.

Two halves that have to move together: `_weekly_signal_report`'s raw-day fallback counts the same
thing, or `face_samples` means something different depending on whether the day has been rolled up
yet. The row's *existence* still gates on `count(*) > 0` over all face rows — `expire_signal_rows`
refuses a day with no rollup row, so a gaze-only day must still get one or its raw rows never expire.
Asserted against a real stack in `scripts/assert_signal_rls.sql`, which is the only place this
arithmetic runs: the backend suite drives `main.py` with a fake client, and CI applying the migration
proves the SQL parses, not that it counts.

**`attention` is still unproduced, and deliberately.** That is Phase 11 step 3, blocked on a labelled
reference rather than on code: "attention" inferred from head direction is least valid for exactly
this product's users, and unlike a FER+ label it renders as a percentage, which reads as objective.
One adult is not a validation set for a construct whose failure mode is population-specific.

**Every surface that rendered it has been removed** — the teacher's Live gauge, `SessionReview`'s
ribbon field, the parent and teacher `face_attention` tiles, the weekly chart series and the LLM
strategy prompt's sentence. The three-state logic meant none of them lied, but a tile that can only
ever say `Calibrating` teaches a reader to ignore it, and it occupied space on the surfaces where
trust matters most. `hasSignalSummary` on the parent dashboard dropped `face_attention` with them:
that list tracks what the tiles can render, so leaving it in would admit a child whose only reading
is attention to a card with no tile to show.

**The column, the payload field and `face_geometry` all stay.** The measurement is still the plan;
only the claims about it are gone. Fill it when there is a labelled reference, and put the UI back
in the same change — not before.

`face_geometry.py` is the arithmetic half: named landmarks in, head pose and iris offset out, pure
numpy so CI can test it. `face_landmarks.py` is the other half — MediaPipe Face Mesh (Apache 2.0,
models downloadable without an agreement, the constraint that blocked RhythmMamba) mapped onto those
names, and the only file that knows a mesh index from a face part, so swapping detector rewrites it
and nothing else. **Neither is wired into the capture loop yet.**

**MediaPipe 1.0.0 removed `mp.solutions` — the entire legacy Solutions API.** `mp.solutions.face_mesh`
raises `AttributeError: module 'mediapipe' has no attribute 'solutions'`, which reads like a broken
install and is not one; the top level exposes only `Image`, `ImageFormat` and `tasks`. The Tasks API
(`vision.FaceLandmarker`, `RunningMode.VIDEO`, `detect_for_video`) replaces it and still returns the
478-point mesh, so `MEDIAPIPE_INDICES` is unaffected. Two consequences worth knowing before touching
it: the model is **no longer in the wheel** — `_TasksMesh` loads `models/face_landmarker.task`
(gitignored; override with `FACE_LANDMARK_MODEL_PATH`) and refuses with the fetch command when it is
absent, since a silent download onto a student's laptop is not something to do by accident. And the
Tasks call shape is adapted at *construction* rather than in `locate()`: `locate()` is the half with
tests and its injected collaborator's shape is the legacy `process()`/`multi_face_landmarks` one, so
porting the untested half to fit the tested half keeps every existing test on real code.

**Its index table is unverified against hardware** — MediaPipe 1.0.0 ships no canonical mesh file
and there is no camera in CI, so the mapping comes from published topology rather than measurement.
A left/right swap would produce a *mirrored* gaze, which every aggregate reads as healthy. So the
table is not trusted: `check_topology` re-derives what any real face satisfies (eyes above mouth,
nose between the eyes, iris inside its own eye) and refuses a set that does not, turning a wrong
index into a first-frame refusal. It cannot catch a mirror — a mirrored face satisfies every
relation — so it needs the manual camera check. **Passed three times on 2026-08-12** (one adult,
laptop webcam), across the `opencv-contrib-python` swap:

| run | square on | eyes left | head left |
| --- | --- | --- | --- |
| first | `yaw 5.97 / pitch -13.76 / roll -4.40` | `gaze.x +0.442` | `yaw +32.97` |
| after the opencv swap | `yaw 7.78 / pitch -6.69 / roll -3.61` | `gaze.x +0.457` | `yaw +41.26` |
| with the emotion check | `yaw 7.74 / pitch -7.15 / roll -5.53` | `gaze.x +0.475` | `yaw +36.99` |

That confirms the table's left/right, both sign conventions, and that the model handedness matches a
real frame — the same three steps refused every frame an hour before the first run. Two things the
later runs added, each the first of its kind:

- **the detector cross-check** — 61 frames, mesh and Haar cascade both found a face on all 61. The
  only time `face_roi.FaceLocator` has been exercised against a real face, and what clears the
  opencv swap.
- **the emotion path end to end** — crops accepted, FER+ classified, confidence 0.94–0.99. Every
  other emotion test injects a fake ONNX session, so this is the first time the real model has run
  on a real crop. **Plumbing only**: high confidence means it ran, not that it read the face right,
  and FER+'s accuracy on this product's users is the weakness no self-check reaches.

It says nothing about pitch/roll *accuracy* against a reference, and nothing about children. **Pitch
at square on is posture, not a fixed offset** — −13.8, then −6.7 and −7.2 in the same setup within
the hour. An earlier version of this note attributed it to camera height and the adult mean face,
which would be roughly constant for one rig; the spread says the subject's head angle dominates. The
20° tolerance absorbs it either way. `gaze.x` on the eyes-left step is the steadiest number here
(+0.44, +0.46, +0.48), which is what makes it a usable signal rather than only a sign test. Re-run
the check after any change to the index table or the canonical model:

**Everything left of the camera is measured in image coordinates, and the frame is not mirrored**, so
a subject's own left is the image *right*. Looking left drives `gaze.x` **positive**; turning the head
left drives `yaw` **positive**; `pitch > 0` is the face pointing *up*. `CANONICAL_FACE` must therefore
put the subject's left at **positive x** — it did the opposite until 2026-08-12, and because the fit
solves for a rotation and a rotation cannot reflect, a person sitting perfectly square on was refused
`implausible_pose` on 120 frames of 120. **Round-trip tests cannot catch this**: rotating the model
and recovering the rotation is self-consistent under either handedness, which is how 32 of them
passed over an unusable model. Tests that pin it construct a frame from the image convention instead
(`test_the_model_handedness_matches_a_real_frame`).

**`gaze` cannot detect a left/right swap and must never be described as doing so.** Both eyes are
averaged in image coordinates and `_eye_offset` divides by an absolute width, so permuting the labels
returns a bit-identical number. `head_pose` is the adjudicator — a mirrored table makes the
correspondence unfittable, so it *refuses* rather than answering wrongly.

```bash
python scripts/verify_landmarks.py --gui
```

Three prompted steps with automatic verdicts — square on, eyes left, head left — because a check
that costs twenty minutes of assembling a camera loop is a check nobody runs. Records no video; `--gui` previews it,
deliberately **unmirrored**, since the whole question is which way is left. `capture_face_video_ecg.py`
has the same flag for a different reason — it *does* write frames, so its preview is about not
wasting a five-minute capture: the face box, the 128×128 crop the model will actually see, and the
counters. Neither preview adds a way to persist a frame, and a test on each asserts that.
Steps 2 and 3 test different things, not two halves of one thing: step 2 is iris tracking and the
image-x sign, step 3 is the pose fit's handedness. **Step 2 cannot detect a mirror** — see above —
and it claimed to until the run that found all this. It deliberately scores no attention:
the geometry has a right answer and can be checked against one, the inference to "attending" is a
judgement, and keeping them apart is what lets the judgement be revised without re-deriving
anything.

It uses an orthographic fit, **not `cv2.solvePnP`**, because solvePnP needs camera intrinsics we do
not have — a guessed focal length yields a systematically wrong pose that still looks like a face
turning. The trade is that perspective is ignored, so it degrades at close range and large angles.
**Yaw is measurable only within ±90°**: past that the Euler recovery returns the other branch of a
two-fold ambiguity no rotation matrix can resolve, corrupting pitch and roll by 180° as well, so it
refuses with `implausible_pose` rather than reporting a mirrored angle.

**The attention score is the part that still needs a measurement**, against a reference, before
anything reaches a parent: "attention" inferred from head direction is least valid for exactly this
product's users, and unlike a FER+ label it renders as an objective-looking percentage. A child
looking away while thinking is not inattentive. One adult is not a sufficient validation set here —
the failure mode is population-specific — so that step is blocked on a labelled recording rather than
on code.

**`identity_confidence` was retired instead (#86, `20260812000000`) — do not add it back without a
consent decision first.** Matching a child's face against a stored identity is a *different purpose*
from what the camera consent asks about ("works out how they are finding the questions"), so it needs
its own consent channel and copy before it needs a model. Its removal also closed a live footgun:
`face_signals` carried two confidences and `signal_fusion`'s face channel read the wrong one, so a
clearly identified face with a garbage FER+ label withheld a difficulty increase while a
well-classified expression on a poorly identified face was discarded, both silently.
`emotion_confidence` keeps its qualified name for that reason.

## Database — Postgres functions are world-executable by default

**Every `CREATE FUNCTION` in the `public` schema is EXECUTE-able by every logged-in user unless
you explicitly revoke it, and the usual boilerplate revoke does not catch it.**

Two things stack up:

1. Postgres grants `EXECUTE` on new functions to `PUBLIC` automatically (unlike tables).
2. Supabase additionally ships `ALTER DEFAULT PRIVILEGES` granting `EXECUTE` to `anon` and
   `authenticated` **by name**.

Explicit grants to a named role survive a revoke aimed at the `PUBLIC` pseudo-role, so
`REVOKE ALL ... FROM PUBLIC` alone leaves `anon` and `authenticated` still holding `EXECUTE`.
Verified against `pg_proc.proacl` on a local instance — without the named revokes the ACL comes
back as `{postgres=X/postgres,anon=X/postgres,authenticated=X/postgres,...}`.

`scripts/check_function_grants.py` enforces this, as the `Function grants` CI job. It matches by
function **name**, not signature, so it catches a forgotten revoke block but not a migration that
adds an overload and revokes only the old signature — review still has to. Deliberate exceptions go
in its `ALLOWLIST` with a reason.

**Don't try to fix this with `ALTER DEFAULT PRIVILEGES`.** Making `EXECUTE` deny-by-default is the
obvious move and it does not work here: tested on a local stack 2026-08-04, the `pg_default_acl` row
records correctly and the `anon`/`authenticated` named grants do disappear from new functions, but
Postgres's `PUBLIC` grant (`=X`) survives and both roles can still execute. Reproduced with three
throwaway functions, with the grantees combined in one statement and separated, and with no event
trigger re-granting. A default that silently fails to deny is worse than none.

### The same trap applies to tables — `GRANT` does not narrow, only `REVOKE` does

Supabase's `ALTER DEFAULT PRIVILEGES` grants **every** table privilege to `anon` and
`authenticated` by name in `public`, so a new table arrives as
`anon=arwdDxtm,authenticated=arwdDxtm` before your migration grants anything. Adding
`GRANT SELECT` on top is a no-op that reads like a restriction.

RLS covers most of it — with no policy for a command, that command is denied — but **RLS does not
filter `TRUNCATE`**. Verified on a local stack: as `anon`, `INSERT` is blocked and `TRUNCATE`
succeeds. PostgREST does not expose `TRUNCATE`, so the anon key in the frontend bundle is not a
path to it; it needs a direct Postgres connection. "Not reachable from the client we ship" is a
weaker property than the one a narrow grant appears to claim.

So for a new table, revoke before granting:

```sql
REVOKE ALL ON TABLE "public"."my_table" FROM "anon";
REVOKE ALL ON TABLE "public"."my_table" FROM "authenticated";
GRANT SELECT ON TABLE "public"."my_table" TO "authenticated";
GRANT ALL ON TABLE "public"."my_table" TO "service_role";
```

Sequences need the same treatment. `20260805110000` swept every remaining table, and
`scripts/check_table_grants.py` enforces it as part of the `Database grants` CI job.

**What to grant back is per-table judgement, and the lint deliberately does not check it.** Most
tables carry a `FOR ALL` "own" policy that RLS evaluates against `auth.uid()`, and the frontend
relies on one: `Adaptive.jsx:290` upserts `user_math_performance` directly through PostgREST, so
that table keeps `INSERT`/`UPDATE`. `math_topics` and `questions` have `USING (true)` public-read
policies, so `anon` keeps `SELECT` on those two and nothing else anywhere. Tables written only by
the backend get `SELECT` for `authenticated` and nothing more.

`sessions` was the one that had been missed, until `20260817000000`. It kept `authenticated=arwd`
next to a `FOR ALL` own policy, so a student could rewrite any column of their own sessions through
PostgREST. Found via `chart_paths` — a path pointed at another child's chart object and then signed
— but `started_at`/`ended_at` drive the rollup's day bucketing and the expiry cutoff, and a DELETE
there cascades all three signal tables. **RLS narrows which rows a command touches, never which
commands exist**, so an own-row policy is not a substitute for withholding the grant. Nothing in
`frontend/src` reads or writes `sessions` directly; they reach the browser through the backend.

### When adding a function

Revoke from the named roles, then grant only what the caller needs:

```sql
REVOKE ALL ON FUNCTION "public"."my_function"("uuid", integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."my_function"("uuid", integer) FROM "anon";
REVOKE ALL ON FUNCTION "public"."my_function"("uuid", integer) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."my_function"("uuid", integer) TO "service_role";
```

Also:

- **Prefer `SECURITY INVOKER`** (the default). A `SECURITY DEFINER` function returning rows or
  aggregates over student data is a ready-made way to read anyone's data. As invoker, RLS still
  applies if the function is ever reached by a lower-privileged role.
- **If you do need `SECURITY DEFINER`, pin `SET search_path`.** An unpinned definer function is
  the classic privilege-escalation vector.
- **End the migration with `NOTIFY pgrst, 'reload schema';`** so PostgREST picks up the new RPC.
- **`CREATE INDEX CONCURRENTLY` is not available in migrations** — Supabase wraps each migration
  in a transaction. Plain `CREATE INDEX` takes an `ACCESS EXCLUSIVE` lock while building. If a
  table is already large, build the index manually with `CONCURRENTLY` outside a transaction
  first; the `IF NOT EXISTS` in the migration then no-ops.

### When changing an existing function's signature

Adding a parameter creates a **new** function rather than replacing the old one, so the migration
has to `DROP FUNCTION` the previous signature explicitly — `CREATE OR REPLACE` alone leaves it
behind as an overload that is still granted, still callable, and unaware of whatever the new
parameter controls. Keeping both is not an option either: with named-argument RPC calls that
match more than one signature, Postgres rejects the call as ambiguous. The new signature also
carries a fresh ACL, so repeat the revokes and the `service_role` grant against it.

That leaves a window. Backend code calling the new signature against a database that has not run
the migration yet gets PostgREST's `PGRST202`, which the callers here catch — so the failure is
silent, and the symptom is empty data rather than an error. **Apply the migration before rolling
out the code that depends on it.**

Where an in-between state would be visible to a user, a temporary retry against the old signature
is a reasonable bridge — but only where doing so cannot violate what the caller asked for, and
only if it is removed once the migration is applied everywhere. Left in, it is dead code that
looks live, and it makes any *later* schema mismatch — a bad rollback, an environment built from
an old dump — degrade to a quietly wrong answer instead of an error. `_summary_rpc` in
`Website/AdaptiveLearning/backend/main.py` carried one for `p_include_face`; it was removed in
#48 once `20260801000000` was applied, and the git history is the worked example.

### Do not "fix" the RLS helper functions

`is_member_of_class` and `is_teacher_of_class`
(`supabase/migrations/20260709154104_teacher_read_policies_and_recursion_fix.sql`) are
`SECURITY DEFINER` **and deliberately granted to `anon` and `authenticated`**. That is required:
RLS policies evaluate them as the calling user, so revoking the grants breaks the policies they
exist to serve.

They are safe by construction — both are `auth.uid()`-scoped booleans with no parameter to pivot
on (they answer "am *I* in this class", not "is user X"), and both pin
`SET search_path TO 'public'`.

Audited 2026-08-04 against `pg_proc.proacl` on production and a local stack: five functions in
`public`, and the two above are the only ones granted to an application role. `handle_new_user` was
the last permissive holdout — harmless, since it returns `trigger`, which Postgres refuses to invoke
directly and PostgREST will not expose as RPC — and `20260804000000` revoked it anyway rather than
leave a permissive ACL sitting next to the ones that matter. Re-audit with:

```sql
SELECT p.proname, p.proacl
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public';
```

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
That is why the double-write warning asks `eeg_poller.claim_double_write_warning(session_id)`, which
is the real condition, rather than reading the mode as a proxy for it.

**Every endpoint that probes the sidecar checks the mode first, and there are eight of them.** "EEG
service is not running on port 8001" is true under `push` and entirely misleading — it reads as a
fault when the deployment simply does not work that way. Two shapes:

- **Returns a payload** (`/api/eeg/{health,status,debug,devices}`) — the liveness field is `None`,
  never `False`, and `ingest_mode` rides alongside so the caller can say *why*. `None` because "not
  probed in this deployment" is a different claim from "probed and down", and **a consumer that
  branches on falsiness renders both identically** — which is exactly how the outage string survived
  in the debug panel after the endpoint behind it was fixed. Same three-state rule as the reporting
  helpers. The panel no longer reads this endpoint under push at all — `sidecarDebug` assembles the
  same shape from the sidecar the page can actually reach — so its `available` there is a **real
  boolean, observed rather than proxied**, and the panel tests it with `=== false`. Two-valued and
  three-valued sources under one field name is a trap of its own: `!available` would read the
  backend's "not probed" as an outage again, one layer further out. Deriving it from the payloads
  is the other wrong answer — an idle sidecar answers `data: null` and a headband-less one answers
  an empty muse block, both ordinary, so **a payload that is empty in normal operation cannot stand
  in for reachability**. Hardcoding it `true` made the "not answering" line unreachable and drew a
  panel of blanks for a sidecar that was not running.
- **Raises** (`/api/eeg/{start,muse/refresh,muse/connect,muse/disconnect}`) — call
  `_refuse_under_push(what)` in `main.py`, *before* `eeg_client.is_alive()`, or the misleading 503
  wins the race. Don't write the 409 out by hand; one inline copy already drifted from the helper
  that claimed to have replaced it.

**Those four refusals left push with no pairing path, which is why the browser now has one.** The
backend is remote under push by definition, so refusing is right — but nothing replaced it, and the
sidecar's own start/scan/connect routes were admin-only while the browser holds the *learner* token.
Every push deployment therefore answered 401 to the one channel push exists for. `sidecar.js` now
calls them directly (`deviceStart`, `museRefresh`, `museConnect`, …) and `toggleHeadband` picks the
transport from `headband.pushMode` — one adapter, the same seven steps, because a second copy of the
pairing sequence would drift and that sequence is where the ordering matters.

**`require_local_controller` is what admits it, and it is scoped to the mode on purpose.** Admin in
both modes; the learner token *only* when `PUSH_ENABLED`. Under pull the browser gains nothing,
because the backend is the legitimate controller there. What it grants is bounded by what the
learner token already was — it ships in the bundle and the sidecar is on loopback, so it separates
pages in one browser, not users, and any page that could call `/api/v1/push/start` could already make
the sidecar stream a student's signals. Pinned by `test_under_pull_the_learner_token_may_not_drive_the_hardware`;
removing the mode check fails exactly that.

**`start.ps1 -Camera` selects push, and without it the mode goes back to pull.** Written on both
branches like the `FACE_*` keys and for the same reason: a stale `INGEST_MODE=push` from a camera run
would disable the poller on a later headband-only run, and a headband recording nothing while the page
says "streaming" is what explicit modes exist to prevent. The camera has no choice in this —
`/api/signals/face` is its only writer, so a camera configured under pull captures frames and stores
nothing.

**"Before" means before every sidecar call, not just before the liveness probe.** `/api/eeg/status`
had the check and still 500'd under push, because `get_muse_status()` ran a few lines above it.
`eeg_client._learner_headers()` raises when `EEG_API_TOKEN` is unset — the normal state of a hosted
push deployment — and it raises *outside* the request try, so the endpoint dies before reaching the
check that exists to protect it. Test stubs for `eeg_client` must therefore **raise** from
`get_muse_status`, as `_StubClient` in `test_ingest_mode.py` does: a stub returning `{}` modelled a
deployment that does not exist and hid this for two rounds.

A ninth endpoint needs the same treatment and an entry in `_MODE_AWARE` or `_MODE_AWARE_RAISING` in
`backend/tests/test_ingest_mode.py`, which parametrises both push and pull over every member. This
was found one endpoint at a time across five review rounds because each site was written by hand and
the test listed only the endpoints someone had already remembered.

Both paths share `signal_mapping.py`. The mapping used to live in `eeg_client`, which is the pull
*transport*; the push path would have had to import an HTTP client it never calls to reach a pure
function, or keep a second copy — and a second copy of a unit conversion is how one path ends up
storing percentages while the other stores ratios.

**What may be recorded is part of that shared mapping, not of either caller.** `eeg_quality()`
answers `no_signal` / `contact_poor` / `ok`, and all three mappers return `None` for a channel that
produced nothing:

- **`no_signal`** — a disconnected headband reports *zeroed* scores. Zeros are worse than nulls:
  aggregates average them and exclude nulls, so a headband on the desk read as sustained zero focus
  rather than as no data. That is the can't-tell-no-data-from-zero failure arriving through the
  *write* side, where none of the reporting rules can see it.
- **`contact_poor`** — keep the row, null the eight measurement columns. "Recording but unable to
  measure" is not "no session", and `class_live` derives staleness from the newest row's `ts`.
  Only `signal_quality == "poor"` **with `quality_basis == "contact"`** counts; the legacy heuristic
  says "poor" for any focused student.

These rules lived inline in `eeg_poller` and were absent from the push path, so the same unworn
headband wrote nothing under pull and a zeroed row per tick under push. Anything of this kind
belongs in the mapper: it is the only place both deployments are guaranteed to read.

### Headband heart rate is a held window, not a per-tick reading

The headband is the primary heart source (the camera is emotion-only), and it reaches
`heart_signals` through `optics_processing.build_heart_record`. Four things about it are load-bearing:

- **Nothing arrives unless `MUSE_ENABLE_OPTICS` is on**, and it is still off by default. The flag is
  narrower than its name: the OPTICS/PPG listeners are registered unconditionally, so "emits no
  optics" stays distinguishable from "never asked". What it gates is moving a capable headband off
  `PRESET_21`, and **two separate things argue for leaving that alone**. The bandwidth cliff is the
  known one — 16 CH at 64 Hz drops the BLE link, and the default `1035` rung is the safe side of it.
  The other is the reason not to flip the default now that heart rate is a real feature: changing
  preset at all is an *EEG* risk, since it moves bit depth 12 → 14 and on some rungs the channel
  count, and a silent EEG regression would be blamed on whatever shipped beside it. With the flag
  off a session records no heart rate and every window is refused as `no_samples` — the honest
  answer, not a fault.
  (`connect_named` setting `PRESET_21` unconditionally is not an override: `get_model()` returns
  `MU_02` for anything post-2018 until `CONNECTED`, so the real choice happens on the connection
  callback in `apply_model_preset`.)
- **The window is placed on `seq`, never on `mono_ts_ms`.** The bridge's stamp records BLE *delivery*
  — ~9% of samples share one with their predecessor and the rest arrive in bursts — so `seq` is the
  only real sample index, and the stamps are used solely to measure an average rate across the whole
  window, where the batching averages out. That rate is `seq`-span over elapsed seconds, not
  `len(rows)`: with samples dropped, counting rows reports a rate low by exactly the loss and scales
  every bpm down with it. This is the opposite call to `rgb_window`'s median-of-intervals, and the
  reason is the clock, not preference.
- **Sample *loss* is gated separately from sample *rate*, and only the second is obvious.** `fs`
  comes from `seq`, which counts what the headband **sent**, so it reads a healthy 64 Hz no matter
  how few samples arrived; `window_coverage` is elapsed span, which the survivors still bracket. A
  window can therefore pass both while being almost entirely `np.interp` output — and interpolation
  manufactures the smooth periodicity autocorrelation rewards, so the result is a *confident* wrong
  rate. Measured on the resting fixture (true ~68 bpm): one sample in 32 gave **55.8 bpm at
  confidence 1.00**, one in 64 gave **44.0**. So `received_rate_hz` — real samples per second of
  span — carries the same `MIN_SAMPLE_RATE` Nyquist bar, and `completeness` rides on the row.
  Anything below 10 Hz effective is refused as `effective_rate_too_low`, including windows that
  happen to still be right: nothing available here separates "sparse but above Nyquist" from
  "aliased", and a refusal costs one window where an acceptance costs a number on a parent's chart.
- **25s window, recomputed every 10s, then *held* on the payload.** The 10s step is what
  `MAX_BPM_CHANGE_PER_S` was validated against. Holding is what lets a 1 Hz poller see every reading;
  emitting for one tick would have push record everything and pull record almost nothing.
  **An EEG no-data tick drops the held block but must not restart the cadence**
  (`_drop_held_heart_block`, not `_reset_heart`). `drain_samples` raises whenever no EEG sample
  arrives in its timeout, so flapping contact takes that path repeatedly; restarting the clock there
  re-stamps the same 25 s of optical signal every tick, and since both writers dedupe on `ts` the
  unique key cannot collapse those — up to 4 near-identical rows a second. It leaves the tracker
  alone too: EEG dropping out says nothing about the optical emitters, and the anchor is what
  catches octave errors.
- **A session's first heart reading is withheld until a second window agrees.** The window right
  after motion — putting the headband on, sitting down after exercise — produces a *confident,
  unanimous, wrong* rate, and no in-window test separates it from a real one: agreement, out-of-band
  power and the peak margin were all tried and all fail, and one candidate discriminator rejected
  every genuinely fast rate along with it. So the tracker asks a different question — is the
  periodicity still there a step later — and holds an unanchored candidate until it is. Motion
  settling is not; a heartbeat is. An unusable window in between discards the candidate rather than
  bridging it. **Re-acquisition after a dropped lock goes through the same rule**, and needs it most:
  a lock is dropped because two windows disagreed with it, so whatever re-acquires comes from exactly
  the population this distrusts — adopting it directly was the same bug one path over. Costs one
  usable window of latency and refuses nothing: a fast rate is published a step late.
  `rejected_by="unconfirmed_anchor"` says a rate was *withheld*, which is not `no_signal`.
- **The block carries its own `ts`, and both writers key on it.** Held, one measurement arrives on
  ~40 consecutive ticks. `map_heart_to_heart_signal` prefers `heart["ts"]` over the tick's, the push
  client dedupes per `(device, source)`, and the poller upserts on
  `heart_session_source_ts_key`. The camera's block has no `ts` and still takes the tick's.
- **`EEG_SOURCE=sim` produces no heart block at all** — the simulator does not model an optical
  channel, and a simulated pulse would be a number on a parent's chart with nothing behind it.

**A payload key needs a field on `InterpretedEegData` or `/api/v1/state` deletes it.** `Envelope.data`
is typed `InterpretedEegData | CameraData | None`, so the sidecar's snapshot is serialised through a
declared model and **pydantic drops undeclared keys silently** — the same trap as `main.FaceSample`
on the website side, one layer further out. `heart` was undeclared, so under `INGEST_MODE=pull` (the
default, and `eeg_client.get_state` reads precisely that endpoint) a headband on an optics preset
could never record a heart rate. Every stage upstream worked: window built, anchor confirmed, block
held and stamped, then deleted at the boundary with nothing raised. Measured on hardware 2026-08-15
— 2697 optics packets at 64.3/s, and no `heart` key on 227 consecutive polls.

It hid because **push bypasses the envelope** (`push_client` posts `snapshot()` directly) and every
heart test asserts on `session.latest_payload`, the dict *before* the model — so the channel was well
covered on both sides of the one layer eating it. `tests/test_state_envelope.py` derives the check
from `stream_manager`'s source so the next key cannot go the same way.

### Optics measured against EEG: the two coexist at the 4 CH rung

Run 2026-08-15 on a MuseS-0FFC (model `MS-03`), `./start.ps1 -Muse -Optics`, default `PRESET_1035`:

| | `PRESET_21` (no optics) | `PRESET_1035` (4 CH optics) |
| --- | --- | --- |
| good EEG channels | 63.8% | 60.7% |
| link drops in 3 min | — | 0 |
| optics rate | n/a | 64.3 packets/s |

So **optics is not what degrades EEG contact** — the earlier working hypothesis, formed across
several failed attempts, was wrong. The 16 CH cliff documented above is real and separate; the
bottom rung holds. Residual `is_good` failures with `hsi [1,1,1,1]` are dry electrodes, not
bandwidth.

**Verify the flag reached the bridge by reading the process, not the launcher.** Every earlier
"phase B" measured a bridge that never had the variable — the flag was set in a string the outer
shell expanded first, and the run looked exactly like optics being harmless. The bridge is a C++
process reading `getenv`, so the check is its own environment block (`NtQueryInformationProcess` →
PEB → `ProcessParameters`), or `requested_preset`/`active_preset` on `/api/v1/muse/status`, which
must both read `PRESET_1035`. `active_preset: ""` means the device never applied one.

**`rmssd_ms` is an enrichment, and a null one is the normal case.** `build_heart_record` derives it
through `hrv_processing.estimate_hrv` over the same 25s window and the same rate — sharing them is
required, not incidental, so the two cannot disagree about whether a window is usable. Roughly one
window in five is gated out even seated and at rest, so **nothing may make a heart rate conditional
on RMSSD being present**: `stress_score` is defined on heart rate alone, and a score whose
definition shifted when an input dropped out would be unreadable across a session. The refusal has
its own field (`rmssd_rejected_by`, carried into `raw`) precisely so it is never confused with
`rejected_by`, which says whether there is a reading at all.

Validated against six simultaneous watch ECGs, seated: r = 0.75 at the 30s window it was captured
on. **The production 25s window has its own numbers** — r = 0.78, bias −3.1 ms, RMS 5.2 ms, worst
window 21% against 15% — measured in `test_optics_rmssd.py` rather than carried across, because a
shorter window has fewer beats to average. Don't quote the 30s figures for the shipped path.

**All of that depends on more than one optical channel being alive, and a count-based quorum is how
it silently stops.** RMSSD is only usable because beats are agreed across channels and timed by
averaging the channels that saw each one; with one live channel both steps become the identity and
what is recorded is the raw per-channel detector, which ranged 29–246 ms across four channels
watching the same heart. Run single-channel against the six ECG windows it reports **all six, never
refusing, at up to +75% error**. Nothing downstream catches it — `estimate_window`'s `agreement`
term is 1.00 by construction against one waveform, the same inapplicable-confidence trap as camera
rPPG — so `consensus_beats` refuses below `MIN_POPULATED_CHANNELS` and scales its quorum as
`CONSENSUS_FRACTION` of the channels that produced detections. A fixed count is what to avoid: 3
was tuned on 4 channels and would be 3-of-16 on the wide optics presets.

Beat coverage is bounded **both ways** for the same reason. The lower bound catches missed beats;
without an upper one a double-detected notch or an octave-low rate is indistinguishable from clean,
since every count beneath it looks healthy. Genuine 4-channel windows reach 1.054 — a 25s window at
70 bpm expects 29.2 beats and can honestly hold 30 — so the bound sits at 1.15, above real data and
well below the 1.20–1.26 that single-channel runs produce.

`sqi` and `stress_score` are still **not derived** on either path; those columns stay null, so
`heart_signals.stress_score` has no producer — don't read an empty tile as a broken query.

**The poller's heart write is consent-gated, and that gate is the only one there is.** It writes with
the service-role client, so neither RLS nor `/api/signals/heart`'s per-sample check reaches it.
`eeg_poller.set_heart_consent_check(fn)` is wired from `main` at import; `fn(user_id, source)` is
built from the same `_may_record` + `_permitted_heart_sources` pair the endpoint uses, so the two
paths cannot disagree about one student — and because `_permitted_heart_sources` reads the composed
`record_*` flags, the school year applies without either site mentioning it. Unwired it denies, a
failed read denies, and it is re-read on the same `CONSENT_RECHECK_SECONDS` cadence as EEG, so a
mid-lesson withdrawal lands without waiting for the session to end.

**`set_consent_check` returns a bool, so it cannot say *why*.** A withdrawal, a closed school year
and a failed read of either all arrive as `False`, and the poller's own log used to assert the first
of them. `set_consent_reason_check(fn)` is the optional companion that supplies the sentence for
`start()`'s refusal and the log line; it is wired to `_poller_may_record_eeg_reason`, which reuses
what the bool check just computed rather than re-reading `_may_record` — a second read would cost
another round trip on every refused start and could return a different verdict from the one it is
explaining. Unwired, `start()` falls back to a consent-only message, so a test that stubs
`_consent_check` must stub this too or it reaches a real database. Per *source*, not per channel: a student who allowed the headband and refused the
camera has consented to `muse_optics` and not to `rppg`.

**On the pull path, EEG consent gates the heart channel as well — deliberately, and only there.**
`_record_heart` runs inside the poller loop, and withdrawing `eeg` stops the poller outright, so it
stops headband heart recording with it. `start()` refuses without EEG consent for the same reason, so
a student who allows `headband_optical` and declines `eeg` records no heart rate under `pull` at all.
That is accepted rather than overlooked: it errs the safe way — the path records *less* than consent
allows, never more — and undoing it means a poller that keeps running with only its cognitive write
switched off, which is a session reporting EEG stopped while still holding the device. A feature, not
a fix; raise it as one. **Push is unaffected**, since `/api/signals/heart` checks per source and
never consults EEG consent, so this is a real difference between the two deployments and the one
place they are knowingly allowed to differ. Pinned by
`test_withdrawing_eeg_consent_stops_the_heart_channel_too`.

### The sidecar's push client does no arithmetic

`EEGResearch/src/app/services/push_client.py` is the other half, enabled by `PUSH_ENABLED` with
`BACKEND_URL`. It cannot import `signal_mapping` — different package — so instead of converting, it
sends the sidecar's payload **whole**: `/api/signals/cognitive` accepts a sensor-shaped sample
(`features`/`bands`, 0..100) as well as the flat already-mapped one, and maps the first itself. That
keeps the /100 conversion in one place reached by both paths. Don't add a divide to the sidecar.

Three properties worth not breaking:

- **The student's bearer token arrives from the browser and lives in memory for one session.** It is
  never logged or written to disk — this process runs on a student's laptop. `stop()` clears it, and
  changing session drops the old queue, since those samples belong to a session the new token may
  not own.
- **The queue is bounded and drops oldest, counted.** `deque(maxlen=…)` evicts silently, and an
  uncounted eviction is a signal path losing data with nothing anywhere to say so. That applies to
  *returning* a failed batch too — `extendleft` evicts from the far end, i.e. the newest — which is
  why restoring goes through `_restore` rather than straight onto the deque.
- **A failure in one channel must not cost the others.** Each channel is drained immediately before
  its own POST, not all three up front; the first version re-raised on the first failure and threw
  away two already-popped batches.
- **The sampling hook emits `snapshot()`, not `latest_payload`** — `bands` and `ingestion` are
  assembled in `snapshot()`, so emitting the raw payload gave push-ingested rows null band powers
  while pull-ingested ones had them — and it does so via `to_thread`, because `snapshot()` reaches
  `get_ingestion_meta()`, the one call the sampling loop already offloads for blocking.
- **A rejected window is not a reading.** `build_face_record` and `build_heart_record` always return
  a dict, with `emotion: None` / `bpm: None` and a `rejected_by`. Enqueue on *the reading*, not on
  the block's presence, or a 4 Hz session writes ~14k all-null rows an hour, every one counted as a
  sample by the aggregates. `source` alone does not test it: the heart block sets `rppg`
  unconditionally.
- **Nothing after `raise_for_status()` may raise, and no POST is cancelled mid-flight.** The rows are
  committed by then; a throw — or a `task.cancel()` during the request — restores the batch and the
  re-post duplicates them, and `cognitive_signals` and `face_signals` have no dedupe key. `stop()`
  therefore *asks* the loop to finish and awaits it, cancelling only once `SHUTDOWN_BUDGET` is spent.
  A batch whose fate is unknown is `unaccounted`, which is neither `recorded` nor `dropped_locally`.
- **`stop()` is bounded by the clock.** An attempt cap is not a bound a reader can convert into
  seconds; 12 attempts × 3 channels × a 4 s timeout is ~144 s on a Ctrl-C.

The **browser** side has the matching rule: effect cleanup does not run on a tab close or hard
refresh, so `Adaptive.jsx` also stops the sidecar from a `pagehide` listener via `stopPushOnUnload`,
which uses `fetch(..., {keepalive: true})`. Without it the sidecar keeps the student's token and
keeps recording for up to an hour after they walked away — a consent problem, not untidiness.
`sendBeacon` cannot be used: it cannot set an `Authorization` header.
- **Delivery is counted from the backend's `inserted`, not from what was sent.** The endpoint drops
  samples for a sensor the student declined; counting sent would report a healthy session that
  recorded nothing.

`/api/v1/push/start` refuses with 409 when `PUSH_ENABLED` is false rather than becoming a second
writer alongside a poller — `cognitive_signals` has no dedupe key, so both running means every EEG
sample lands twice with no error.

### The browser calls the sidecar directly, and two tokens are in play

`frontend/src/lib/sidecar.js`. Under push the hosted backend cannot reach a student's laptop, so
lifecycle control comes from the page: it calls `http://127.0.0.1:8001` itself. An HTTPS page may do
that — loopback is exempt from the mixed-content block, measured with a negative control on
Chromium 148; evidence and limits in `EEGResearch/docs/LOOPBACK_FROM_HTTPS.md`.

**Don't conflate the two credentials.** `VITE_EEG_LOCAL_TOKEN` is the sidecar's own `API_TOKEN`, is
in the client bundle, and is *not a secret* — the sidecar binds to loopback, so it separates this
page from other pages in this browser, not one user from another. The student's Supabase access
token is a real secret, is fetched per call, and is handed to the sidecar once so it can post as
them.

**Re-hand the token on refresh.** Supabase access tokens expire roughly hourly and a lesson can run
longer; the sidecar holds one token per session. `Adaptive.jsx` re-calls `startPush` on
`TOKEN_REFRESHED`, which replaces the token in place — same session id, queue untouched. Without it
the pushes 401 partway through and the samples sit in a bounded queue until they are dropped.

**Never call `supabase.auth.getSession()` inside an `onAuthStateChange` callback.** supabase-js v2
holds an internal auth lock while dispatching, and `getSession()` waits on it — awaiting it there
deadlocks. Use the `session` the callback is handed; that is why `startPush` takes an optional token.
The symptom is the worst kind: the refresh handler hangs, the sidecar keeps the expired token, and
every push 401s for the rest of the lesson with nothing raised anywhere.

`ALLOWED_ORIGINS` on the sidecar must name the **frontend** origin, not just the backend's. Getting
it wrong fails every local call on CORS while the sidecar itself looks perfectly healthy.

All three ingest endpoints are rate-limited and length-bounded. `/api/signals/cognitive` was neither
until the push client existed, which was survivable only while its sole writer was the in-process
poller.

## Two columns are called stress and only one measures it

`cognitive_signals.stress` is `1.0 - calm`, written by `signal_mapping.py:97`. There is no `calm`
column, so this *is* the EEG calm score, stored inverted. No independent quantity exists behind it,
and `infer_state` never reads it — it uses `calm_score` directly, the same number the other way up.

`heart_signals.stress_score` is a measurement: autonomic arousal on a 0–100 scale, derived against
the session's own baseline, with its own quality gate and its own `calibrating` state.

So: **never average them, never sum them, and never render both under one "Stress" label.** One is a
cognitive score with a sign flip; the other is a physiological measurement with a baseline. A
dashboard tile fed by whichever happens to be present would change meaning when a headband
disconnects, which is the same class of failure as a reporting surface that cannot tell "no data"
from "zero".

`stress_score` is defined **on heart rate alone**. RMSSD is an enrichment term, added when available
and absent without changing what the score means — a hard requirement, not a preference, because
RMSSD is unavailable whenever the headband is off and one window in five is gated out even when it is
on. A score whose definition shifts when an input drops out is unreadable across a session.

## Fusion is asymmetric on purpose — easing off wins, pushing harder defers

`Website/AdaptiveLearning/backend/signal_fusion.py` decides how hard the next question is, from
whichever of EEG, heart and facial are consented and present. To **raise** difficulty every channel
with an opinion must agree; to **lower** it, any one trusted channel suffices.

Keep it that way. A wrong ease-off costs one easy question; a wrong push costs a struggling student
a harder one, and the signals are least trustworthy exactly when a student is agitated. A
brute-force test asserts the property directly: adding a channel can make sessions gentler and can
never make them harder. If that test fails, the change is wrong, not the test.

Facial is the weakest input by design — it can withhold an increase, and can neither cause one nor
trigger an ease-off alone. FER+ is trained predominantly on adult faces and is least reliable on
this product's users: children, and children with learning disabilities. Its labels deliberately use
a different vocabulary (`negative`, never `stressed`) so no later edit can wire it into the ease-off
branch by matching on a label name. `EMOTION_MIN_CONFIDENCE` is inherited from PR #49 and is a
guess, not a measurement.

**Consent gates the read, not the result.** A revoked channel is never queried, and the tests assert
on which tables were reached — an empty result cannot distinguish "asked and got nothing" from
"never asked". `_consent_flags` fails closed, like `_consent()` and unlike the reporting helpers.

**Difficulty is chosen in the backend, not the sidecar.** `question_policy` was removed in 1.3.0:
the sidecar computed it every tick, it was persisted and displayed, and nothing read it to pick a
question. Don't add it back — the sidecar cannot see correctness, topic history or grade level.

## Learning preferences live on `profiles`, and difficulty is a bias

Three columns (`20260822000000`): `difficulty_bias`, `session_duration_minutes`, `practice_reminders`.
They were `localStorage.al_prefs`, written by the Preferences tab and read by nothing — the backend
picks the difficulty and cannot see a key in one browser's storage.

**`difficulty_bias` is a shift, never an absolute difficulty**, and that is a safety property rather
than a simplification. `_shift_difficulty` applies it on top of what the model chose from the
student's accuracy history, and `LLM_topic_decider` overrides it *downward* whenever the fused
signal says stressed — the same asymmetry `signal_fusion` documents. Storing "always hard" would
store a value the ease-off rule has to contradict, and a setting the system routinely ignores is
worse than one that does not exist. It is why the control offers three options and not four: medium
and adaptive would both mean no shift.

**`start_session` prewarms at the student's bias, not 0.** `QUEUE_SIZE` questions are generated
before the first answer and served first, so a hardcoded default there makes the setting do nothing
for the opening of every session.

Bounds are stated twice on purpose — Pydantic on `UpdateProfileRequest` and a CHECK in the
migration — and they must agree, or a value that passes one and fails the other surfaces as a 500
from the client library instead of a 422 naming the field. The CHECK is a **range**, not the four
durations the UI offers, so a fifth button is not a migration.

**Duration is advisory.** The page asks between questions; nothing ends on a timer. A session closed
mid-question discards an answer a child was part way through giving. `Adaptive.jsx` now has a
`finishSession` — before this it never called `/end` at all, so an adaptive session stayed open until
the stale sweep on the student's *next* start, which is also when its rollup and chart archive were
written.

**`practice_reminders` is a dashboard banner and is named for that.** There is no push
infrastructure — no service worker, no VAPID, no scheduled fan-out — so "Notifications: daily
reminders to practice" described a system that does not exist. The banner needs *both* reads to have
landed before it renders: derived from a failed `/api/sessions`, it tells a child they skipped a day
they did not skip. Its "today" is the **browser's local day**, deliberately not `_school_day` — that
helper buckets recorded data against the school's timezone, and this is a nudge about the student's
own afternoon.

## An answer is recorded by the backend, and the topic comes from the question

`Adaptive.jsx` had no `/api/sessions/{id}/answer` call at all — only `Practice.jsx` did — so every
question answered on the adaptive path was counted in `localStorage` and nowhere else.
`session_answers`, `sessions.questions_answered`, `user_stats` and every report built on them read
zero however long a student practised, while the page's own Topic Accuracy panel showed figures.
Two records of one afternoon, one of them private to a browser.

**The question id is what made it possible.** `add_question_to_supabase` returned a bool, so the
generated question reached the page with no id and there was nothing to put in
`session_answers.question_id`. It now returns the id — **and returns the existing row's id on a
duplicate** rather than False, because answering a question the generator has produced before is
exactly as real as answering a novel one.

**`_record_topic_attempt` derives the topic from the question row, never from the caller.** The
client has to be trusted about correctness; letting it also name the topic would let a page credit
one subject for work done in another, and `user_math_performance` is what the adaptive engine reads
to choose what to serve next. It never raises: it runs after `session_answers` is written, and a
topic lookup failing must not turn a recorded answer into "that answer could not be saved".

**It is one statement in the database** (`record_topic_attempt`, `20260825000000`). It was four
sequential round trips on the hottest path in the product, and the last two were a read-modify-write
with no lock — two answers together both read the same counts and the second overwrote the first,
losing attempts silently. `ON CONFLICT DO UPDATE` incrementing the *stored* value removes that
rather than narrowing it. It returns the topic **name**, which `/answer` hands back to the page so
one figure moves; nothing holds an id-to-name map, so returning the id would cost a second query.
The arithmetic is asserted in `scripts/assert_signal_rls.sql` — the backend suite drives a fake
client and can only check that one call is made with the right three arguments.

**Its PGRST202 is the deploy-ordering trap in its worst form**: the helper swallows exceptions by
design, so code deployed ahead of the migration stops attributing anything with no symptom but the
numbers not moving. It logs that case by name and cites the migration.

**A roster surface reads once for the roster, never once per student.** `_profiles_many`,
`_topic_performance_many`, `_open_sessions_many` and `_stats_including_open_session_many` are the
batch forms; `class_students`, `my_children`, `class_live` and `leaderboard` use them. The stats half
was batched first and the profile lookup was left in the loop beside it, which is the shape to watch
for. One deliberate exception: `my_children` still reads the five most recent sessions **per child**,
because "top N per group" has no PostgREST form — one `in_` query returns the newest five overall,
which is one busy child's five.

**Topic accuracy is read from `user_math_performance`, not from the browser.** It was
`localStorage.accuracyStats_<uid>` — the only panel in the app whose numbers were not the
database's. It disagreed with the dashboard on the same screen, started from zero on a school
computer, and nothing server-side could correct it: a parent erasing a channel left the figures
standing in the child's browser. The client-side `sendAccuracyToBackend` upsert is **deleted, not
merely unused** — the backend owns that table now, and a client upsert would overwrite real counts
with one browser's memory. Its `Number(v) || null` also turned every genuine zero into a null, which
is why the table sat empty while the panel showed numbers.

There is no "Reset stats" button any more. Against localStorage it cleared a browser key; against
`user_math_performance` the same button deletes a student's academic record with one click and no
confirmation. Erasure here is a parent-only, confirmed action.

## Admin is a role, and three migrations are what make that safe — the flags can only ever say no

Admin is `profiles.role = 'admin'` (`20260824020000`), read through the same `_role` every other
role gate uses. Set from the dashboard SQL editor, like `retention_window`'s row.

**It is a role rather than a side table only because the column is server-controlled on both
edges**, and both edges are load-bearing: `20260824010000` revokes UPDATE/INSERT on it from the
client roles, and `20260824020000` whitelists `student|teacher|parent` in `handle_new_user` so
sign-up cannot ask for it. Widening the CHECK without the whitelist would have been a self-service
admin signup — the trigger copies `raw_user_meta_data->>'role'` straight into the column, so
`signUp({data:{role:'admin'}})` from a console would have made an administrator. The
`20260824030000` backfill repeats the whitelist for the same reason: it reads the same
client-supplied metadata, and trusting it would be the escalation in one INSERT.

`AdminGuard` asks `GET /api/admin/me` rather than reading a role client-side; it is a UI
convenience, and every `/api/admin/*` endpoint re-checks. `_can_view_student` gains admin as a
**fourth relationship** rather than each admin path growing its own copy of a report query.

### `profiles` rows come from a trigger, and it was missing from source control

`handle_new_user` was written for an `auth.users` trigger that **no migration created**;
`20260804000000` recorded that drift and left it, correctly, because `profiles` was decoration at
the time. It stopped being decoration when `_role` started gating on it — a missing row means
`_profile` degrades to a student-shaped dict, so a teacher is refused their own classes with nothing
to read. `20260824030000` creates the trigger and backfills the rows, and is safe against a
hand-made survivor: `on conflict (id) do nothing` makes a second firing a no-op. Check for one under
a different name after applying.

It deliberately **does not UPDATE existing rows**. `raw_user_meta_data` still holds whatever was
typed at sign-up, so refreshing from it would silently demote every administrator.

`feature_flags` is key/value, read through `_FEATURE_FLAG_DEFAULTS`, which is the contract: **a key
absent from the table still has a value, and it is the value the system had before the table
existed.** That is what let the flags ship without changing behaviour, and it is why an unreadable
table falls back to the *declared defaults* rather than to off — a database blip is not a
reconfiguration. The map is also the whitelist: an unrecognised row is inert and a write to an
unknown key is a 404, so a typo cannot create a switch that reads back as set and controls nothing.
Cached 30s, same reasoning as `_RETENTION_TTL_SECONDS`; `_feature_flags_cache_clear()` on every
write.

**The three `recording_*` flags are ANDed into `_may_record`, never ORed.** A flag can withhold
recording and can never grant it, so no combination of switches records something a student
declined — the same asymmetry `signal_fusion` documents, and a brute-force-ish test pins it.

### `consent_enforcement_enabled` — the one switch that records without consent

Off, `_may_record` substitutes a fully-consenting answer. It is for prototyping, it is against the
grain of everything else here, and so it is **bounded rather than trusted**:

- **Expiry is evaluated on every read** (`_consent_enforcement_active`), not by a job that flips the
  row back. A scheduled job that fails to run leaves consent unenforced indefinitely, and
  not-indefinitely is the single guarantee this has to make.
- **A bypass with no `bypass_until` has already expired.** An unbounded bypass is the state the
  column exists to prevent, so a hand-edited row resumes enforcement rather than running for ever.
- **Disabling it requires an explicit duration**, capped at `_MAX_BYPASS_MINUTES` (4h). No default —
  a default would be `main.py` choosing how long consent goes unenforced.
- **`_consent()` itself is untouched.** The bypass is a decision about whether to *ask*, not a claim
  that anyone agreed, so the consent screen, the reporting surfaces and the poller status keep
  showing what the family actually decided. `consent_bypassed` rides on the `_may_record` payload so
  a caller reporting *why* something is recorded does not say the student agreed.
- **It does not override the school year.** The window is a separate gate and stays closed.

Every write lands in `feature_flag_changes`, append-only, written by the backend rather than by a
trigger — the backend already resolved the admin's identity to admit the request, so a trigger would
be a second and worse answer to that question. A failed audit insert never undoes the flag: it is
already written, and raising would invite a retry that changes nothing and audits nothing.

### The admin read surfaces send counts and timestamps, never readings

`/api/admin/live-signals` answers "is data arriving" for every open session. **It selects `ts`
alone**, so the readings never leave the database rather than being fetched and dropped on the way
out — no band powers, no emotion label, no bpm. An admin has no relationship to those students
entitling them to the values, and asking for less is a stronger version of that property than
filtering afterwards: the test asserts on the *select*, which is the only place the difference shows.

It shares `_LIVE_WINDOW_SEC`/`_STALE_AFTER_SEC` with `class_live` — two sets of numbers would let one
page call a session live while the other called it stale — but **not `_latest_session_signals`, and
not its pool.** That helper fetches whole rows and blocks on four futures it submits to
`_live_signals_pool`; this endpoint is platform-wide where `class_live` is one class, so sharing four
workers with every teacher's live monitor would starve the page a lesson is actually watched on. The
sharper reason is that fanning this endpoint's outer loop into that pool **deadlocks**: the waiters
and the work they wait on end up in one four-slot queue, so four sessions occupy every worker while
their own reads sit behind them. `_admin_live_pool` is separate, and nothing submitted to it waits on
anything else in it. A test asserts the two pools are not the same object, because consolidating them
looks like tidying.

Five states per channel, and they are not a scale: flowing, quiet, stale, **never-reported**, and
**unreadable** (`seen: null`). The last two are the ones to keep apart — a session that never had
that sensor is a different fact from one whose sensor stopped, and both are different from a read
that failed. Reporting a failed read as never-reported is a claim about the deployment that a
database blip has not earned.

`/api/admin/health` reports `ok` / `degraded` / `unknown`, and **a check that could not run is
`unknown`, never `ok`.** `/api/admin/consent-summary` is counts only. `/api/admin/env-flags` lists
the env-var switches read-only, from a **named list** — `os.environ` also holds the service-role
key, and a dashboard that enumerated the environment would eventually render a secret.

Tests: `backend/tests/test_admin.py`. `conftest`'s `_feature_flags_are_default` pins the defaults for
every other test file, and **deliberately does not take `monkeypatch`** — requesting it from an
autouse fixture pytest orders early hoists `monkeypatch`'s setup ahead of `_join_poller_threads` and
inverts their teardown, which failed three unrelated tests in teardown for a reason nothing in their
bodies could explain. `pytest --setup-plan` shows the ordering directly.

## Access control — check the relationship, not the role name

Endpoints serving student data read through the **service-role Supabase client, which bypasses
RLS**, so the checks in `Website/AdaptiveLearning/backend/main.py` are the only thing standing
between a caller and another student's data.

Use the existing helpers rather than writing a new check inline — re-deriving the rule per
endpoint is how the original `class_live` guard drifted into `owner != user AND role != "teacher"`,
which let any teacher read any class:

- `_verify_class_owner(class_id, user_id)` — only the owning teacher.
- `_verify_can_view_student(viewer, student_id)` — the student themselves, a teacher of a class
  they are enrolled in, or a linked parent.
- `_session_or_403(session_id, user_id, columns)` — a session is **one student's**, so this is
  ownership and nothing weaker; no teacher or parent is admitted. It returns the row, which is the
  point: `record_answer` and `end_session` need the session anyway, and paying for a second query
  is why they were written with no check at all. `_verify_session_owner` is this with the row
  thrown away. **Check before you write** — `record_answer` inserted the answer row *first* and
  read the session afterwards, and `/end` stopped the poller before looking, so any student could
  forge answers into another child's session or end one mid-lesson.

Access is a **relationship**, not a path segment or a role claim. Don't namespace an endpoint
under `/api/teacher/` when parents legitimately read it too, and don't gate on
`user_metadata.role`.

### Where a role gate must read it from, and why one column is not enough

**`user_metadata.role` is attacker-controlled.** The client sets it at sign-up and can rewrite it
whenever it likes with `supabase.auth.updateUser({data: {role: 'teacher'}})`, which talks to GoTrue
and never passes through this backend. `create_class`, `my_classes` and `link_child` gated on it, so
any student could self-elevate and create classes. They now call **`_role(uid)`**, which reads
`profiles.role`; `test_no_endpoint_gates_on_user_metadata` greps the module so a fourth site cannot
appear. It fails closed to `student`, since `_profile` degrades to a student-shaped dict on a failed
read and a database blip must not be a way past a role check.

**Switching to `profiles.role` is only half of it, and it is the half that looks like the whole
fix.** `profiles` carries a `FOR ALL` own-row policy and `authenticated` holds UPDATE, so that
column was equally client-writable — a student could PATCH their own row through PostgREST. RLS
narrows *which rows*, never *which columns*, and a CHECK cannot express "not by you". Only the grant
can, and grants are per-column for UPDATE and INSERT: `20260824010000` revokes both on `role` from
`anon` and `authenticated`, leaving the rest of the row (display name, grade, the three preferences)
writable as before. INSERT matters as much as UPDATE — with INSERT alone a student could delete
their profile and re-insert it as a teacher.

Self-service teacher sign-up is unaffected and still intentional: `handle_new_user` is
`SECURITY DEFINER` owned by `postgres`, so it bypasses column grants and still writes the role the
registration form chose. What changed is that the value cannot be edited afterwards by the account
it describes.

**The frontend reads the same column, through `GET /api/profile/me`.** `AuthContext` used to derive
its role from `user_metadata.role` on the grounds that it only picks which dashboard to render — true
while every role was chosen at sign-up, and wrong the moment one was not. An account promoted to
`admin` in the SQL editor has no `role` in its metadata at all, so it rendered as a student: student
nav, a badge reading "Student", and no link to the console it administers. `/admin` itself worked,
because `AdminGuard` asks the backend — which is the shape of the bug. The authoritative check was
right and every surface around it was reading a different source.

Three things about that read are load-bearing:

- **It is not in the `onAuthStateChange` callback.** `apiFetch` calls `getSession()` for the token,
  supabase-js holds an auth lock while dispatching, and awaiting it there deadlocks — the app hangs
  on a loader for ever. It lives in an effect the callback merely schedules.
- **Keyed on the user *id*, not the user object**, so a token refresh mid-lesson does not re-fetch
  and put the whole app back through a loading state.
- **A failed read falls back to the claim, not to `student`.** A blip is not a demotion; defaulting
  to the least-privileged role here would drop every teacher into the wrong application whenever the
  API was down. That is the opposite direction to `_role` on the backend, deliberately: that one
  decides *access*, this one decides which nav to draw.

`loading` stays true until the role resolves, or the guards see `role === null` for a frame and
render the "this account isn't set up" screen on every page load.

**That makes this the one request in the app that may not hang, so it is the one that passes
`timeoutMs`.** A request that *fails* is caught and falls back to the claim; a request that never
settles has nothing waiting for it, and leaves `role` null and `loading` true for ever — an infinite
loader over the whole application for every signed-in user. A `.catch` is not a bound. `apiFetch`'s
`timeoutMs` is **opt-in with no default**, because a blanket one would abort
`/api/students/{id}/learning-strategies`, which is bounded server-side at `STRATEGY_LLM_TIMEOUT` and
can queue behind other waiters first — and `sidecar.js` already documents what a client timeout
shorter than the work does: it does not cancel anything, it just stops you finding out what happened.
The bound covers the **whole call**, not the `fetch`: `getAccessToken` awaits
`supabase.auth.getSession()`, which goes to the network when the token needs refreshing, so wrapping
`fetch` alone leaves exactly the hang it was added to stop.

Login and Register navigate to `/` and let `HomeRedirect` choose, rather than computing a home from
the claim. They each carried a second copy of the role-to-home map that `homeRoute.js` exists to be
the only one of, and both were keyed on the value that does not know about `admin`.

The claim survives only as that fallback, and nothing that matters may be gated on it — same
reasoning as `AdminGuard` being a UI convenience over a backend check.

Tests: `backend/tests/test_role_gates.py`, which asserts both halves — that the code reads the right
column, and that a migration takes the write away.

Access-control tests live in `Website/AdaptiveLearning/backend/tests/test_access_control.py` and
run in CI.

## Recording needs consent **and** an open school year

`retention_window` is a single-row table (`enforced`, `starts_on`, `ends_on`, `timezone`) holding
the school year. Outside it nothing is recorded whatever consent says, and on `ends_on` the
per-sample rows are deleted — that job is a later change; this is the gate.

**`enforced = false` turns the year off without turning the gate off** (`20260821000000`). It is for
prototyping and for deployments that do not run on a term, and it exists because the alternative was
inventing a pair of term dates — which produces a row indistinguishable from a real school year on
the one table whose job is to say when recording is permitted. The dates are nullable so an
unenforced row need not carry fake ones. Three states to keep apart: **no row** — nobody decided,
records nothing; **`enforced = true`** — a real year, needs both dates; **`enforced = false`** —
deliberately not gating on a term, records. Consent is unaffected and still required.

Two fail-closed edges hold that apart from an accident. It is read as `is False`, never falsiness, so
a row predating the column — or one PostgREST returns without it, or with an explicit null — keeps
the gate on rather than being opened by the migration that added it. And `enforced = true` with no
dates is `unconfigured`, not unbounded: a half-finished edit must not be the most permissive state in
the system, which is the whole reason an absent row denies.

**It fails closed in five different ways, and they are named separately.** `_retention_window()`
answers `open`, `not_enforced`, `before_year`, `after_year`, `unconfigured` or `unreadable`, and only
the first two record — kept distinct because "inside the configured year" and "not gating on a year
at all" look identical from the recording side and are very different facts about a deployment. An unset window is not an open-ended licence — same default as consent — and a typo'd
timezone denies rather than falling back to UTC, because a fallback moves every boundary by hours
while looking like it worked, on a value edited by hand twice a year. "The year hasn't started" and
"the year is over" reach a parent as different sentences; `_not_recording_reason` puts the window
reason ahead of the consent one, or a closed year sends someone to the consent screen to fix a
setting that is fine. `_poller_status` follows the same order, with its own machine-readable
`stopped_reason` vocabulary (`school_year_ended`, …) — a poller that is not running with consent
intact and nothing saying why is the silent quiet week arriving through the status endpoint.

**Never read the raw `*_enabled` flags to decide whether to record.** `_permitted_heart_sources`
takes a `_may_record` result and reads its composed `record_*` flags; hand it a bare `_consent`
dict and it returns no sources at all, which is the safe direction for that mistake.
`test_every_recording_site_gates_on_the_window` lists the six sites and fails if one calls
`_consent(` directly — the same exhaustiveness pattern as `_MODE_AWARE`, and for the same reason.

**The window gates recording only. Don't put it in `_consent()`.** That helper is read by the
reporting surfaces, the consent screen and the poller status, none of which should change answer
because term ended: gating there would report every channel off on the last day of school, so a
parent could not read the history that survives until the delete job runs — and it would read as a
withdrawal, a claim about a decision nobody made. `_may_record()` composes the two and is what the
six recording sites call (the poller's two checks, the three `/api/signals/*` endpoints, and
`/api/eeg/start`); `_consent()` stays pure and its raw flags ride along beside the `record_*` ones
so a caller can still tell "they agreed but the year is over" from "they said no".

**The timezone is the school's, not UTC**, for both the window boundaries and the weekly report's
day buckets. The last day of school ends at local midnight; against a UTC clock it ends
mid-afternoon or runs into the next day depending which side of the meridian the school is on.

Bucketing goes through `_school_day(ts, tz)`, never `str(ts)[:10]` — PostgREST returns UTC, so
slicing put a 4pm Californian lesson on the next day of a parent's chart. `_weekly_signal_report`
resolves `since` to midnight of the earliest *school* day too: `now - 7 days` in UTC starts after
that day begins wherever the school is behind UTC, so the oldest column silently averaged only part
of itself.

**`_school_timezone()` defaults to UTC where `_retention_window()` denies, and that asymmetry is
deliberate.** A wrong boundary while recording collects data nobody agreed to; a wrong boundary
while reporting moves a chart column by a few hours. Refusing to report over a config typo is the
larger harm, so the gate fails closed and the report degrades.

There is **no admin role** (`profiles.role` is CHECKed to `student|teacher|parent`), so the row is
edited through the dashboard SQL editor. RLS is on with **no policies** and `anon`/`authenticated`
are revoked outright, so only `service_role` and the dashboard reach it. Both are needed: RLS never
filters `TRUNCATE`.

Tests: `backend/tests/test_retention_window.py`. Every other test file gets an open year from the
autouse `_school_year_is_open` fixture in `conftest.py` — without it they would pass by recording
nothing, for a reason unrelated to what they assert.

### The end-of-year delete refuses days nothing summarised

`expire_signal_rows()` removes per-sample rows from `cognitive_signals`,
`face_signals` and `heart_signals`; `sessions`, `session_answers`, `user_stats` and
`user_math_performance` stay, because academic history is not signal data.

**It skips any student-day with no `signal_daily_rollup` row**, per channel, and reports the count
it skipped. That check is the whole safety property: without it a bug in the rollup writer becomes
silent permanent loss on a fixed date, since the rows it takes are the only copy. With it, a broken
writer degrades to data that does not expire — visible and fixable. Asserted in
`scripts/assert_signal_rls.sql`, which runs against a real stack in CI — on all three tables, because
"the loop body is generated identically" is an argument about the code and the channel mapping
(`face_signals` → `emotion`) is the one pair whose names do not match.

The return value carries `hit_batch_cap` beside the two counts. Rows that were eligible but not
reached before `p_max_batches` appear in neither `deleted` nor `skipped_days_without_rollup`, so
`skipped = 0` on its own does not mean everything eligible was handled — `hit_batch_cap` is the half
that says so. Harmless either way, since the next run finishes the work, but not something a reader
should have to infer from a missing number.

**The cutoff is derived, never "today's date".** Days before `starts_on` always expire; once today
in the school's timezone reaches `ends_on`, everything up to and including it expires too. So the
job is idempotent and self-healing: a missed run completes on the next one, and a repeat deletes
nothing new. That is what makes a same-day delete with no grace period acceptable. No window
configured means no cutoff and nothing deleted — the same fail-closed direction as recording.

Scheduled daily at 03:30 UTC via `pg_cron` rather than on one date, because scheduling a single day
would turn a missed run into a year of silence. `cron.schedule` upserts on the job name, so
re-running the migration re-points the job instead of creating a second one that would delete twice.

Storage does **not** cascade. `sessions.chart_paths` is what would tell the job which objects to
remove and is Phase 8; until that lands there are no archived charts to orphan.

### The daily rollup is written as sessions close, never at expiry

`signal_daily_rollup` holds one row per student per school day per channel
(`cognitive|heart|emotion`), written by `_rollup_session_days` at the end of `end_session`. Writing
it continuously is what keeps it from being a race against the end-of-year delete — generating it at
expiry would make the one job that destroys data also the first to read it. The delete job (not yet
written) refuses to delete a day with no rollup row, so a broken writer cannot become silent
permanent loss on a fixed date.

**The aggregation is a Postgres function (`rollup_signal_day`), not backend code**, because a day
holds thousands of samples and the reporting path caps its reads — averaging a capped subset in
Python would be quietly wrong, and this is the copy that survives the delete. It **recomputes**
rather than accumulates, so closing two sessions on one day, or replaying a close, converges;
an incremental writer would have to be exactly-once, which nothing here can promise.

`_rollup_session_days` **never raises**: it runs last in `end_session`, after the writes that matter,
because a failed summary must not cost a student their session record and stats update. It rolls up
every school day the session touched (two if it crossed local midnight), bounded so a corrupt
`started_at` cannot spin.

Averages are over **trusted rows only** for heart and emotion, matching what the weekly report
publishes — an untrusted reading is one the quality gate rejected, and averaging it here would
smuggle it past that gate permanently. `heart_sources` deliberately includes untrusted sources: its
job is to explain a change in the numbers, and a sensor whose readings were all rejected is exactly
such an explanation. `trusted_sample_count` is defined per channel (cognitive has no trust flag, so
it counts rows that produced a measurement rather than the nulled ones a poor-contact headband
writes).

Its access rules differ from `retention_window`'s above: the rollup carries a **read-your-own
`SELECT` policy** and `authenticated` keeps `SELECT`, matching the per-sample tables it summarises.
There is no insert/update/delete policy for anyone, so with RLS on, PostgREST cannot write it
whatever JWT it carries — the only correct writer is `rollup_signal_day`.

### Archived charts are the other thing that survives the delete

At every session close, `chart_archive.schedule()` renders the session's four charts to standalone
SVG (`chart_render.py`) and uploads them to the private `session-charts` bucket. With the rollup,
these are what is left of a school year once `expire_signal_rows` has run.

**Off the request path, and it never raises.** A storage failure must not cost a student their
session close — the session row, their stats and the rollup are all written by then. So the work
goes to a two-worker pool and `schedule()` swallows even a submit failure. That makes the log the
only place a failure can surface, and it *has* to surface: the window in which an archive can still
be rebuilt closes on `ends_on`.

**Three close sites** — `/end`, the stale-session sweep in `start_session`, and `class_live` — and
they all go through **`_close_session()`**. Don't hand-write a fourth: the sequence was copied into
each site and every copy drifted separately, none of them raising anything. The sweep credited a
`correct_answers` it had never selected (absent column → `None` → `or 0` → an honest-looking zero),
so every session of a student who shut the tab added its questions and *no* correct answers to their
record; `class_live` never ran the empty-session discard, so a failed pairing it closed stayed in
History for ever; and the credit, the rollup and the archive each shipped at different times as "the
third close site to be missed".

Order is load-bearing: **discard first**, because a rollup of nothing and an archive of four empty
charts are work done for a session about to stop existing.

**`_close_session` stamps `ended_at` itself, and the stamp is a claim.** It used to sit at each call
site above the call, which left two things to get wrong per site and both were. `class_live` stamped
and closed *before* stopping its poller, so a tick could insert a signal row after the discard check
had looked. And no site made the stamp conditional, so two closes racing — a delayed `/end` against
the sweep — both ran the whole sequence and both credited the session's *cumulative* counts, landing
every answer twice in the lifetime totals. `/end`'s read of `ended_at` is not the guard; that read
and the write are two statements. `_claim_session_close` is: `is_("ended_at","null")` matches at most
one row. **An empty update result is ambiguous** — it is also what a client not asking PostgREST for
the updated row returns — so it is confirmed by reading the row back, and only a *different*
`ended_at` counts as a loss. Guessing "lost" would skip the credit, rollup and archive for every
close.

Stopping the poller stays at the call sites — it takes different ids at each — and **before the
call** is now the whole of the ordering rule, pinned by
`test_every_close_site_stops_the_poller_first`.

**The credit recounts from `session_answers`.** `questions_answered` is a denormalised cache written
in a separate statement from the answer row, and `_discard_if_nothing_recorded` already distrusts it.
The credit did not, so a session correctly *saved* from deletion by that re-check was then credited
zero and the student's work never reached the lifetime totals — permanently, since no later close
revisits a stamped session. `_answer_counts` only ever revises **upward**: rows fewer than the
counter means a short read, and crediting less than a previous reading loses work.

The exhaustiveness tests share one discovery helper, `tests/conftest.py:close_sites()` — a closer is
a function that calls `_close_session(` **or** writes an `"ended_at":` of its own. Both halves
matter: the first catches a site drifting away from the helper, the second catches a new site that
hand-rolls a stamp and never reaches it. A second test pins the helper's own contents; the
indirection is only safe while both halves exist. They catch a step being **removed**, not neutered.

**`chart_paths` has four states and no column default.** A path, `null` for a channel that produced
nothing, an absent key for a chart never attempted, and column-NULL for a session the archive never
ran on. `'{}'::jsonb` would claim every pre-Phase-8 session was archived and found nothing — the
absence-as-data failure again, and `scripts/assert_signal_rls.sql` fails if a default appears.

**Nothing has a policy on `storage.objects`, deliberately.** RLS is on and no policy grants any
role anything, so only `service_role` — the backend — reads or writes. Not even the student the
chart is *about*: an object is fetched by URL, not filtered by a query, so the access decision
belongs in the backend where the relationship checks are, handed out as a short-lived signed URL.
The bucket is private for the reason no policy can fix later: a public object URL, once pasted
anywhere, cannot be un-shared. All of that is asserted against a real stack in CI.

Two smaller traps: the archive draws **untrusted rows too**, unlike the rollup, because it is a
picture of what the reviewer was shown rather than a number outliving its evidence; and `upsert`
in `file_options` must be the **string** `"true"` — storage-py passes those through as HTTP
headers, so a bool arrives as `True` and a replayed close 409s instead of overwriting.

**Reading them back is `GET /api/signals/session/{id}/charts`**, which resolves whose session it is,
applies `_verify_can_view_student`, and issues a signed URL per recorded chart with a 300s TTL.
Three states stay apart in the payload, and a surface saying "no charts" has to consult all three:
`archived: false` (the archive never ran), `charts[name]: null` (that channel drew nothing), and
`name in unavailable` (a path was recorded and the object could not be read). It has deliberately
**no `retrieved` flag** — unlike the reporting helpers it raises rather than degrading, so a flag
that is never false would be a state that does not exist.

**The object path is derived there, never read out of `chart_paths`.** That column is ordinary
jsonb on `sessions`, which carries a `FOR ALL` own-row policy — so before `20260817000000` a student
could PATCH their own session row through PostgREST and point it at another child's object, and the
endpoint would sign it, having just correctly confirmed they own *this* session. The stored value
records **which** charts exist; it is not an address. That migration revokes the write as well, but
the endpoint must hold without it — a grant is one migration away from being widened back. The
consequence is that changing `object_path`'s scheme means migrating the objects, which was already
true.

**A signed URL cannot be revoked.** It stays valid until it expires whatever happens to consent in
between, so the TTL is the only bound on a leaked one — that is the argument for keeping it short,
not convenience. A *public* bucket would be worse in kind rather than degree: a URL that has been
shared cannot be un-shared by any policy added later.

**Storage does not cascade, so a deleted session orphans its SVGs — `sweep_orphan_charts` is what
collects them.** There is still no delete endpoint in `main.py`, which is exactly why a sweep rather
than a hook: those deletes come from the dashboard or a direct connection, where the backend never
runs. `python sweep_orphan_charts.py` reports, `--apply` deletes.

**It deletes on *absence*, which is the dangerous kind of job**, and the guards are the point rather
than the sweeping. One failed read of `sessions` makes every object in the bucket look orphaned, so:
the read failing **refuses** instead of proceeding, more than `max_orphan_fraction` (default 0.5)
looking orphaned refuses, a path that is not `{uuid}/{uuid}/…` is left alone rather than deleted, and
`dry_run` is the default. **The bucket is listed *before* `sessions` is read**, and that order is a
guard too — read the table first and a session created in between has objects whose id is missing
from the snapshot, deleted as an orphan while its row sits there. Listing first can only be stale in
the safe direction. Each guard has a test and each test was checked by breaking the guard; the first
version of the read-failure test passed with the guard removed, because the fraction guard caught it
and its message also mentioned sessions.

An orphan is not a leak — `/charts` resolves the session row before signing, and the bucket has no
policies — so this is storage that should not exist rather than data anyone can reach. It stops
being fine when account deletion becomes a feature (#75), because then "delete my account" leaves
charts of the child behind. **Objects for a session that still exists are out of scope on purpose**:
`expire_signal_rows` leaves the archive standing deliberately, and a sweep that "corrected" that
would remove the thing that makes a same-day delete defensible.

**The archived charts deliberately survive `expire_signal_rows`.** The plan says in one place that
the expiry job removes them; that is wrong and the plan contradicts itself two paragraphs later.
Deleting per-sample rows on `ends_on` with no grace period is only defensible *because* the rollup
and these SVGs survive — they are the human-readable record of the year. A job that took both would
remove the thing that makes its own schedule safe.

## Consent — `signal_consent` decides what may be recorded

Three channels, named for the **sensor** rather than the signal derived from it: `eeg`,
`headband_optical` (heart rate today; the Athena's `OPTICS` packet also carries fNIRS, so a
`_ppg_` name would go stale), and `camera` — which covers expression **and** the rPPG heart-rate
fallback. One device, one decision: a heart-rate failover must never open a webcam the student
declined.

**Everything defaults to false.** An absent row means the same as a row of falses, so there is no
backfill and an unconfigured student records nothing. `_consent()` fails **closed** on a read error
and carries `retrieved` so callers can tell "nobody consented" from "we couldn't find out" — the
opposite of the reporting helpers below, deliberately: a dashboard degrading to empty is fine, a
consent check degrading to *enabled* records data against a refusal.

**Withdrawal stops future recording and keeps what is already stored.** Decided 2026-08-10: a
revoked channel records nothing further until consent is given again, and no past row is deleted or
hidden. Withdrawal is not erasure — that is `POST /api/consent/{id}/erase`, below.

### Erasure is the other request, and nothing triggers it by side effect

`erase_signals(user, channel, by, tz)` destroys one channel's stored signals for one student;
`signal_erasure` records that it happened. It runs **only** when a parent asks by name —
`test_changing_consent_never_erases` pins that, because wiring a revocation to the delete would turn
the reversible control into the irreversible one by a side effect nobody asked for.

**A linked parent only**, so *not* `_consent_actor`: a student may withdraw precisely because a
parent can undo it, and nothing undoes this. The request carries its own `confirm: true` — a dialog
is not auditable.

**Per channel, with the heart deletes keyed on `source`.** `camera` takes `face_signals` and the
`rppg` heart rows; `headband_optical` takes the `muse_optics` ones. Keyed on the table instead, a
parent erasing the webcam would destroy headband data they said nothing about.

**Derived data goes too, and the rollup is deleted before it is rebuilt.** `rollup_signal_day` has
`HAVING count(*) > 0` on every channel, so with the raw rows gone it inserts nothing and *leaves the
existing row standing* — averages of erased data outliving the erasure. Deleting first is what makes
the rebuild a recomputation. The rebuild is not optional either: `expire_signal_rows` refuses a day
with no rollup row, so a day left without one keeps its raw rows past `ends_on`.

**Archived charts go if they draw on the channel at all**, so `camera` takes `heart_rate` and
`stress_pie` with it — those mix both sensors into one picture and no pixel says which is which.
Over-deletion, preferred to serving a chart that still contains what was erased. Object paths are
**derived** in the function, never read from `chart_paths`, where they would be a delete list of the
writer's choosing.

The database half is one transaction; storage removal runs after it commits and is **counted, not
awaited** (`charts_failed`, plus a log line). Once `chart_paths` is nulled the objects are
unreachable through the product either way, which is why that is safe to report rather than roll
back.

**The control** (`ConsentChannels.jsx`) is per channel and parent-only; a student sees that an
erasure happened but is not offered an action the backend would refuse. It is gated behind an "I
understand this cannot be undone" checkbox, cleared whenever a panel opens so an acknowledgement
cannot carry between channels, and sends the **channel** name (`camera`), not the switch key
(`camera_enabled`), which 422s. The confirmation states what goes *and* what stays, and that the
setting is unchanged — erasing the past while leaving the sensor on is the mistake most available to
a parent. That scope belongs in the confirmation, not as standing copy: a permanent disclaimer means
the control's name overpromised, which is what retired `FacialRecognitionToggle`.

**The tombstone is the fourth reporting state.** `erased_at` rides on each channel of the consent
payload, independent of `enabled` and `revoked_at` — a parent who erased and re-consented has a
channel that is on and a past that is gone. `_erasures()` fails **open** to `{}`, unlike `_consent()`:
it decides only whether a tile says "erased" or "no sensor", never whether anything may be recorded.

That rule has to hold on **both ingestion paths**, and for a while it did not. `/api/signals/*` has
called `_consent()` per request since it existed; the poller writes `cognitive_signals` directly with
the **service-role** client, so neither RLS nor the ingest endpoint applies to it, and under
`INGEST_MODE=pull` a withdrawal stopped nothing. Now: `/api/eeg/start` refuses **403** (not the 409
push uses — one says this student said no, the other says this deployment does not work that way),
and a running poller re-reads consent every `CONSENT_RECHECK_SECONDS` so a mid-lesson withdrawal
lands without waiting for the session to end. `eeg_poller.set_consent_check()` is wired from `main`
at import and has **no default**: unwired, `start()` raises rather than assuming yes, because an
unwired deployment that assumes yes is indistinguishable from a wired one.

**Writes only through the backend.** The table has no insert/update/delete policy for anyone, so
with RLS on, PostgREST cannot write it whatever JWT it carries — including the anon key in the
frontend bundle. `main.py` is the enforcement:

- a student may only move a flag **true → false**; only a linked parent may move it back
- a **teacher may read but not write** — they need to see a channel is off, or a blank tile reads as
  a broken query, but consent is not theirs to change. Use `_consent_actor`, not
  `_verify_can_view_student`, which admits teachers
- `revoked_by` is surfaced as a **role, never an identity**, and is stored **per channel**. The row
  has one `updated_by` and the channels are revoked independently, so deriving the role from it
  would report a parent's later unrelated write as having made the student's earlier revocation

RLS `WITH CHECK` cannot see the previous row, so "off-direction only" is not expressible as a
policy — which is why the student gets no update policy at all rather than a narrowed one.

Writes are **conditional on the state they were decided against** (`.eq()` on each flag being
changed) and answer 409 if it moved. Read-then-write is not atomic, and the pair that races here is
a student's withdrawal against a parent's re-enable on the same channel — losing that silently
means recording against a refusal.

A parent turning a channel **back on** sets `parent_enabled_at` and raises `needs_student_ack`,
cleared by `POST /api/consent/ack`. A parent turning one *off* raises nothing. Discovering a
resumed sensor by noticing data reappear is not consent.

Tests: `backend/tests/test_consent.py`.

`frontend/src/lib/facePref.js` still exists and is unrelated — a viewer-side localStorage read
filter over the reporting surfaces, not consent. It is replaced by this table in a later change;
until then the two coexist and mean different things.

## Reporting — a failed read must not look like a quiet week

Every reporting helper swallows its exception so one broken query doesn't blank a dashboard, and
answers 200 with a default payload. Zero samples and a null average are then indistinguishable
from a student who genuinely recorded nothing — which is how surfaces came to report an absence in
data that never loaded. Three states, and the payload has to separate all three:

- **nothing recorded** — the read succeeded and found no rows.
- **not requested** — `face_included: false`, the facial opt-out was on.
- **not retrieved** — `retrieved: false`, the query itself failed.

`_shape_summary` carries both flags on every payload, so a consumer never treats "field absent" as
a fourth state. `_signal_summaries` returns `None` for a failed batch read versus `{}` for one that
succeeded with nothing to return. **Any surface that renders "no data" must consult these before
saying so**, and a new aggregate helper has to carry them the same way.

## The facial opt-out means the data is not read

`include_face=False` skips the query outright — `_weekly_signal_report` never touches
`face_signals`, and the summary RPCs take `p_include_face` so the aggregate reads no facial row
either. Nulling values on the way out is not an implementation of this. If there is ever no way to
tell the database to skip the rows, the correct answer is a blank tile, not a read — never fall
back to a query that reads what the caller opted out of.

**That rule now belongs to consent, which is server-side and genuinely skips the read.** The
viewer-side switch it was written for is gone: `facePref.js` was a read filter wearing the
vocabulary of consent, and it needed a disclaimer in its own UI copy — *"this does not switch a
camera on or off"* — to stop being read as one. Needing that sentence was the signal the control was
wrong.

**The teacher's replacement deliberately breaks the rule, and says so.** `frontend/src/lib/viewPrefs.js`
(*"Hide sensor data"*, on `/teacher/students` and `/teacher/students/:id/report`) is **client-side
only: it fetches the data and does not draw it.** That is acceptable there and nowhere else — the
teacher is already authorised for the data by relationship, so it is decluttering, not a privacy
boundary. Keeping it client-side also avoids a second `include_face`-style axis through every
reporting endpoint. A future reader will otherwise find a filter that fetches what it hides and
assume it is a bug.

It hides **all** sensor data, not just facial: heart rate now comes from the headband as often as
the camera, so a facial-only filter would leave HR and HRV on screen and satisfy nobody. Live class
monitoring and session review stay outside it and deliberately don't render the switch — a control
that silently changes a page it is absent from is worse than one with a stated edge. And it never
manufactures a reason: a channel off for consent reasons still reads "not recorded — turned off on
<date>" when the filter is off.

`_reportable_channels`' `want_heart`/`want_emotion` parameters survive but no client sends them.
They default to True and are not a privacy boundary; don't build one on them.

### Every chart goes through `AccessibleChart`, and a test enforces it

Recharts emits bare `<svg>` with no accessible name and nothing a screen reader
can walk, so a chart rendered directly announces as nothing at all.
`components/charts/AccessibleChart.jsx` is the only place that may render one.

**The `sr-only` data table is a *sibling* of the `role="img"` wrapper, never a
child.** WAI-ARIA's presentational-children rule prunes every descendant role
from an `img`, so a nested table is invisible to real assistive technology — while
being **perfectly visible to a jsdom test**, because Testing Library reads DOM
attributes rather than modelling the accessibility tree. That is the trap, and
it is the opposite way round from how it first reads: the table is not what the
test cannot see, it is the *pruning*. `getByRole('table')` finds the element
whether or not a real reader ever would, so a hand-assembled call site has
nothing to fail against, in the browser or in CI. That is why this is a
component rather than a documented recipe, and why
`AccessibleChart.test.jsx` walks the source and fails on any chart component
rendered outside it. It cannot see a hand-written `<svg>`; that is the honest
limit of a source check.

**One `columns` spec drives the sentence and the table.** They were separate
literals for one PR and disagreed twice in it: a key named `bpm` where the rows
carry `heart_rate_bpm`, so a visibly-plotted line announced "not recorded"; and
raw 0..1 ratios described with a `%` unit, announcing a session ranging 42–78%
as "Focus 0% to 1%". Neither is visible on screen and no test could catch them,
because both surfaces were wrong in the same way at once.

**Scaling belongs in the spec, per page, because the pages differ.**
`SessionReview` and `Live` plot raw ratios against `domain={[0, 1]}` and need
`scale: asPercent` on those columns. `SignalPanel` scales **the fields it names**
into its chart data — `toPct(d.focus)`, `toPct(d.stress)` — and spreads every
other field across untouched, so a column for one of those others needs a
`scale` like anywhere else. "This page scales on the way in" is the wrong unit
of thought and it has already cost one bug: an `engagement` column was added
without a `scale` on exactly that reasoning and announced "0% to 1%".

**And a column must name a series the chart actually draws** — including when
the `<Line>` is conditional, in which case the column is too. A screen-reader
user given a series no sighted reader can see has a different report, not an
equivalent one.

This one has now been found **twice**, which is why it is a rule rather than an
anecdote. `SignalPanel`'s `engagement` column had no `<Line>` at all, so nothing
on screen could contradict its wrong scaling; `SessionReview` then kept
`heart_rate_bpm`/`rmssd_ms` columns whose lines are gated on `hasHeart`, so a
session with no headband emitted "Heart rate: not recorded" on every row. The
first fix was applied where it was found rather than swept for siblings. Check
what the chart plots, and under what condition, before copying a spec across.

**A categorical chart is `sliceSpec(label, rows, noun, {nameKey, valueKey,
rowLabel})`, spread into the component.** It returns the sentence, the rows and
the columns together so the noun is written once — it names what the values
count in the sentence and heads the table column. As two literals they drifted:
`Analytics` built its sentence from a remapped `topicData.map(d => ({name, value}))`
while its table read `topicData` with key `count`.

The table is **sampled to 60 rows** and says so in its caption. A 4Hz channel
over an hour is ~14,000 rows, built on every render for a table nobody sighted
sees — and unusable for those who do. A silently shortened one would claim the
session was shorter than it was.

`sample()` returns the rows alone; "was it sampled" is
`tableRows.length < (rows?.length ?? 0)` at the one place that asks. Two return
values could disagree with each other — but note the `?? 0`: `sample()` guards a
nullish `rows` internally, so deriving the flag *outside* it moved that check
away from the guard and crashed on a comparison. Moving a derivation out of a
function moves it out of that function's guards.

### Muted text is `text-gray-600 dark:text-gray-400`, and a test does the arithmetic

Contrast is one of the few accessibility properties a source check can settle outright, so
`src/test/contrast.test.js` computes it rather than trusting a convention. Measured against the
surfaces this app paints (Tailwind 3.4 stock `gray`):

| | best surface | worst surface | AA 4.5 |
| --- | --- | --- | --- |
| light `gray-400` | 2.54 on white | 1.72 on gray-300 | fails everywhere |
| light `gray-500` | 4.83 on white | 3.28 on gray-300 | fails from gray-100 down |
| light `gray-600` | 7.56 on white | 5.13 on gray-300 | passes |
| dark `gray-500` | 4.16 on gray-950 | 2.13 on gray-700 | fails everywhere |
| dark `gray-400` | 7.93 on gray-950 | 4.06 on gray-700 | passes except on gray-700 |

**The dark half has to be added, not just the light half darkened.** 138 of the 146 sites named no
`dark:` variant at all, so they rendered gray-400 in *both* modes — where it already passes. A
straight `gray-400 → gray-600` substitution would have fixed light mode by breaking dark mode, which
is why the fix is a pair.

`text-gray-500` on white is fine at 4.83 and is left alone; it is only wrong on a `bg-gray-100` card
(4.39), so those are fixed where the two are named on one element. **Where the background comes from
a parent, no source check can see it** — the test judges what fails on every surface, plus
same-element pairs, and says so.

**`Adaptive.jsx`'s debug readout is exempt and is the only exemption.** It paints `bg-gray-950` with
no `dark:` prefix, so it is dark in both modes and every rule above reverses inside it: gray-400
passes at 7.93 and gray-600 would be unreadable. Its `gray-500` was raised *to* gray-400 — the
opposite direction to the rest of the app. Check for an unprefixed dark background before assuming a
grey is too light.

`text-[10px]` (31 uses) is **not** a contrast failure — WCAG sets no minimum font size — so it was
left alone. What mattered was the combination, and the tiny badges that were also sub-AA are fixed.

### The three consent notices share `NoticeBanner`, and a tone is a whole class name

`ChildWithdrewBanner`, `ParentRestoredBanner` and `ParentLinkedBanner` were one component wearing
three colours. What they each restated was the part worth having in one place: **a failed
acknowledgement leaves the banner standing**, because the person has not been told yet and a notice
that dismisses itself on a failed write is one nobody sees again. Stated three times is two more
places for the fourth notice's author to drop it.

`onAcknowledge` clears whatever made the banner render; the shell owns the pending flag and
swallows the rejection. `busy` is cleared in a `finally`, not only on the failure path — a caller
that acknowledges without unmounting would otherwise be left with a permanently dead button.

**Tone classes are full strings in a map, never interpolated.** Tailwind decides what CSS to ship by
scanning source text for complete class names, so `bg-${tone}-50` renders markup pointing at a rule
that was never generated — a banner with no background at all, **in production only**, since the dev
server is not what does the scan. No test can catch it either: the rendered class string is identical
either way and jsdom has no stylesheet. Source review is the only check, which is why the map exists.

### A tile never says "no data" for something that was not recorded

`SignalPanel`'s `offLabel` picks between four states, and every tile goes through
`valueOrReason` rather than branching on the channel flag itself:

| State | Shown | Because |
| --- | --- | --- |
| consent withdrawn | `Off since <date>` | the date comes from `*_revoked_at` on the payload |
| consent unreadable | `Unavailable` | "the student turned this off" is a claim a failed read has not earned |
| read, samples arrived, none usable | `Calibrating` | a rejected window or a baseline still forming |
| read, no samples at all | `No sensor` | consented, but nothing produced anything |

The single `FACE_OFF = 'Off'` this replaces meant "the viewer switched facial
reporting off" — true when there was one viewer-side switch, and now wrong in
three ways at once. Branching on the flag alone is the trap: it leaves `pct()`'s
own `'N/A'` standing whenever a *consented* channel produced nothing usable,
which is the exact string the rule exists to stop showing, surviving in the case
least likely to be tested.

**EEG carries `eeg_enabled` + `eeg_revoked_at`, not an `eeg_included`.** The name is the point: the
summary RPCs have no `p_include_cognitive`, so that channel is *always* read, and withdrawal keeps
what is already stored — a student who switched the headband off last week still has true averages
from before then. Calling it `eeg_included` would claim a read was skipped that was not. Until it
existed, `eegReason` hardcoded `on: true` and the three cognitive tiles were the only ones that
could not say `Off since <date>`; a parent who switched the headband off read `No sensor`, which is
what a fault looks like. Absent reads as **on** — defaulting to off would tell every reader of an
older payload about a decision nobody made. The batch RPC cannot carry it, so `my_children` stamps
it per child, like `emotion_revoked_at` beside it.

A channel that is off keeps its tile. Dropping the row tells a parent who
switched a sensor off nothing at all — the same failure wearing a different
shape. The one exception is a payload predating the channel (`heart_included`
absent rather than `false`): there is nothing true to say about a channel the
payload does not know about, so the row is omitted.

## The strategies model pass is optional and bounded

`/api/students/{id}/learning-strategies` always has a deterministic rule-based answer; the
`strategy_llm_enabled` **feature flag** (default off) only decides whether a model gets a chance to
replace it. Off, the endpoint never opens a socket — which is what CI and any deployment without a
local Ollama should do. Every failure path degrades to the rules rather than erroring.

It was the `STRATEGY_LLM_ENABLED` env var until the admin dashboard landed, and is now read per
request rather than at import: the reason to reach for this switch is a model behaving badly in
front of students, which is not a moment to be waiting on a deploy.

The bounds exist because this is a sync endpoint, so each waiting request holds one of anyio's ~40
threadpool slots: `STRATEGY_LLM_TIMEOUT` enforced by waiting on a future (an httpx timeout is
per-operation, not per-call), a 2-worker pool, `STRATEGY_LLM_MAX_WAITERS` on how many callers may
block at once, and `STRATEGY_RATE_LIMIT`/`STRATEGY_RATE_WINDOW` per user id. An abandoned wait
cancels its future, or a stalled server turns every timeout into work that still runs later. If you
add another model-backed endpoint, it needs the same four bounds — the per-user rate limit alone
does not protect the threadpool. Question generation is now the second such caller and has its own
four; see *Every model call goes through `llm_client`*.

`_llm_strategies` reaches its model through `llm_client` like everything else, so `LLM_PROVIDER`
switches this pass too and it shares the process-wide concurrency ceiling with the thirteen
generation calls. It keeps its own `STRATEGY_LLM_*` bounds on top: those are about a *sync endpoint*
holding an anyio threadpool slot, which is a different problem from a background prefetch thread.

Model output is untrusted text: it's parsed, length-bounded, stripped of markdown emphasis and list
markers, and run through a clinical-term filter, and anything failing validation falls back to the
rules. Extend `_validated_strategies` rather than rendering raw output.

## Every model call goes through `llm_client`, and the provider is a setting

`backend/llm_client.py` is the only place either model provider is reached. Fourteen call sites used
to import `ollama` directly — the ten `LLM_*_generation.py` topic files, **three** in
`LLM_topic_decider.py` (not one: `parallel_topic_and_difficulty_calculation` makes two, on
`llama3.2:3b` rather than the 8b everything else uses), and `main._llm_strategies`. So a provider
switch was fourteen edits, and the bounds below had nowhere to live at all.

**`LLM_PROVIDER` defaults to `ollama`.** A fresh checkout running `start.ps1` must not begin billing
an Anthropic account; a deployment opts in with `LLM_PROVIDER=claude` and `ANTHROPIC_API_KEY`. Both
packages are pinned in `requirements.txt` and neither is imported until its branch is taken.

**The sampling parameters do not carry across, and getting that wrong fails everything rather than
degrading it.** Every generation call site passes `temperature=1.1`, which is outside Anthropic's
0.0–1.0 range — passed through it returns `400 invalid_request_error` on every question, on every
topic, from the first call. So the Claude branch takes its own `CLAUDE_TEMPERATURE` (default 1.0,
clamped) and sends **no `top_p`/`top_k`**, since on Claude 4.x and later at most one of
`temperature`/`top_p` should be set. `claude_temperature=` is the per-caller override; the strategies
pass uses it to keep its 0.4, because advice a parent reads is not the place for "keep it varied".
`test_llm_client.py` pins the request that is *built* — nothing else does, and a test that only
checks a question came back passes against a fixture while saying nothing about what was sent.

**Four bounds, because CLAUDE.md already required them of the one model-backed endpoint that existed
before this** (see *The strategies model pass is optional and bounded*). Question generation is on
the hottest path in the product and had none:

| Bound | Setting | Why the existing one was not it |
| --- | --- | --- |
| Per-call deadline | `GENERATION_LLM_TIMEOUT` (30s) | The SDK's own default is **ten minutes**; a prefetch worker blocked that long never refills the queue |
| Process-wide concurrency | `GENERATION_MAX_CONCURRENCY` (8) | `_prefetch_active` bounds *per user*, so the peak was however many children pressed start at once |
| Per-student volume | `GENERATION_RATE_LIMIT` / `_WINDOW` (60/min) | The queue bounds calls *in flight*, not calls *over time* |
| Spend | `GENERATION_DAILY_CALL_LIMIT` (5000/24h, Claude only) | Nothing bounded it; free against a local model |

**The budget covers the whole call, so time spent queueing for a slot comes out of it** — the model
call is charged the *remainder*, and a caller that queues its budget away is refused rather than
started with no deadline left. Charged twice, one caller blocks for nearly double what it asked for;
`_llm_strategies` had that bug against its own pool and this is the same fix one layer down. It
belongs here rather than at a call site, because this is where the queueing happens. Tests on both
must therefore assert `<=` the budget, never `==` it.

`_ensure_queue` submits to a pool sized to `GENERATION_MAX_CONCURRENCY` instead of spawning a bare
daemon thread per question. **A submit that fails must roll the in-flight count back**, because
`_prefetch_worker` owns that decrement in its `finally` and a worker that never starts never runs
one — the student's count would stay raised for the life of the process, `needed` would be ≤ 0 from
then on, and their queue would never refill again. It is swallowed rather than raised for a separate
reason: `_ensure_queue` runs *after* the response is assembled, so letting a failed refill out turns
a served question into a 500. The daily ceiling is **Claude-only** on purpose: Ollama is local and
free, so a call ceiling there would refuse a child a question to protect nothing.

**On breach the answer is to refuse — `GenerationUnavailable`, surfaced as 503, never a fallback.**
Serving a question from the bank or from a cheaper model instead would change what a child is asked
with nothing on any surface saying so, which is the same class of failure as a dashboard that cannot
tell "no data" from "zero". 503 rather than 500 because a ceiling is a decision this deployment made,
not something that broke. The prefetch worker is the one place a refusal is *silent*, and that is
safe because it is invisible by construction: the queue stays short and the next question is
generated inline.

`CLAUDE_MAX_RETRIES` defaults to **0**, against the SDK's 2. Every call site already sits in its own
`for attempt in range(3)`, so the defaults multiply to nine billed attempts per failed generation —
and the loop the call sites own is the one worth keeping, since it also rejects a *well-formed*
response for being bad JSON or the wrong shape, which no transport retry can.

**Switching provider does not invalidate the checks below it, but it does invalidate every measured
rate.** `grade_appropriateness` and `question_consistency` are code and are provider-agnostic — that
is why those rules were moved out of prompts in the first place. Every "measured on llama3.1:8b"
figure in the sections that follow describes the **Ollama** path and nothing else. Before a
deployment runs on Claude, redo the sampling: **count how often each fail-open check *engages*, not
just how often it fires.** A check whose input it can no longer locate — a differently formatted
dataset, a separator that moved — reports a perfect record while doing nothing, and that has already
happened once here (fractions in `ordering`). Label the new figures with the model and keep the
Ollama ones labelled as Ollama's; the reasoning survives the model change even where the rate does
not.

## Question generation can be grounded in a lesson plan, but nothing seeds one

`lesson_plans` (`20260827010000`) holds curriculum text keyed on `(topic_name, grade_band)`, at the
same `early`/`middle`/`upper`/`advanced` granularity `LLM_*_generation.py`'s own `_grade_band()`
already uses — not per exact grade, since one lesson plan already covers a band there. Public read,
like `math_topics`/`questions`; written only via the dashboard, since it's reference content the
backend never mutates.

`lesson_plan_context.append_lesson_context(prompt, topic_name, grade_band)` is the one-line call
site wired into all ten `LLM_*_generation.py` topic files, right after the grade-magnitude block.
It returns `None` on a missing row, a blank row, a failed read, or missing Supabase credentials --
same fail-open direction as the reporting helpers: this is prompt grounding, not a consent or
access gate, so any failure should degrade to the existing difficulty/grade heuristics rather than
block generation. Cached with the same 30s TTL and `time.monotonic()` pattern as `main.py`'s
`_feature_flags()` (a lesson-plan edit lands within the TTL, not on the next restart), and clamped
to 2000 chars before it reaches a prompt -- dashboard-authored text is still bounded like every
other prompt input here, even though only the dashboard/SQL editor can write it.

**The Supabase client is created lazily, on first lookup, not at import.** `main.py` imports
`LLM_topic_decider`, which imports the ten generation modules, which import this one -- eager
`create_client()` at that point ran ahead of `main.py`'s own `RuntimeError` for a missing
`SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`, so a misconfigured deployment saw a bare `KeyError`
three imports away instead of the clear error `main.py` exists to give it.

The table has no rows yet, so none of this changes generated output until it's seeded per
`(topic_name, grade_band)`.

**Don't scrape third-party worksheet sites into this table.** Vendors like K5 Learning gate real
content behind membership and hold copyright on what isn't; their topic taxonomy for a grade also
doesn't line up with this product's topics (algebra/geometry/angle-relationships/mean/median/mode/
probability/rationals/ordering are mostly older-than-grade-1 concepts). Write original objectives
per topic/grade_band instead.

## Grade appropriateness is code-enforced twice: which topic, and what that topic asks

Both layers used to be soft prompt hints only, and both leaked. `LLM_topic_decider.py`'s topic
prompt has always said "grades 1-3 should primarily see ordering, geometry, and expressions... 
algebra and probability should only appear after grade 6" -- but nothing enforced it, and
`randomize_selection()` -- the fallback whenever an LLM call fails to parse, which fires often
enough to matter -- picked uniformly across all 10 topics with **no grade parameter at all**. That
was the most direct way a 1st grader landed on "algebra".

`LLM_topic_decider._allowed_topics(grade)` is now the single source of truth for that rule, keyed
on the grade *number* rather than a `_grade_band()`-style band -- the rule's own line falls
between grade 5 and grade 6, finer than any four-band split.

**A grade is read numerically, through `grade_levels`, and an unreadable one counts as the
youngest.** `profiles.grade_level` is free text; only the frontend dropdown keeps it to "1st grade"
form, and nothing in the schema enforces that. Every grade rule here used to match those exact
strings and fall through to its *most permissive* branch for anything else — so `"Grade 1"` missed
every branch of `_allowed_topics` and made a 1st grader eligible for algebra, and missed every
branch of `_grade_band` and gave them `advanced` content. Both halves matter: fixing the topic gate
alone still leaves advanced material reaching a child. `grade_levels.grade_number` parses a digit or
a named label (`Kindergarten`, `Highschool`, `College`), rejects a number outside 0-13 so
`"2026 cohort"` cannot become grade 2026, and answers `None` when it genuinely cannot tell — which
every caller treats as the youngest, the same withholding-is-cheap asymmetry `signal_fusion`
documents. `_grade_band` in all ten generation files now delegates to it rather than carrying a
tenth copy of the string match. `_safe_topic(topic, grade)` checks
the LLM's own selection against it (the prompt asks for the right thing but an 8B model doesn't
reliably comply, same reasoning as the deterministic EEG-bias clamp in
`LLM_single_prompt_topic_and_difficulty_decider`), and `randomize_selection()` now draws from it
directly instead of an unconditional 10-way `match`. Both success paths and both fallback paths go
through one of these two functions -- there is no third way a topic reaches `question_generation()`.

**Below that, each `LLM_*_generation.py` used to scale difficulty and grade independently** --
`DIFFICULTY_COMPLEXITY[difficulty]` described the question's *structure* ("one-step equation with
x") and `GRADE_COMPLEXITY[grade_band]` only ever scaled a *number's magnitude* on top of it. That
meant "easy" always meant "one-step equation with x" for every grade, algebra notation included --
grade only changed how big the constants in that equation were. Replaced with
`COMPLEXITY_BY_GRADE[grade_band][difficulty]`, one self-contained instruction per band per tier.
"early" band content is grounded in grades 1-3 arithmetic specifically (whole numbers,
addition/subtraction primary, no algebraic notation), not just smaller versions of the same
structure every other grade gets.

**Seven of the ten topics use that table; `geometry`, `angle_relationships` and `probability`
deliberately do not, and the difference is not an unfinished migration.** In those three, difficulty
already selects a *scenario* (`DIFFICULTY_SCENARIOS` → a circle-area question, a triangle-sum
question), so the question's structure is chosen by picking which question to ask rather than by
describing it in prose. They keep `GRADE_COMPLEXITY[band]` for magnitude alone. Giving them a
`COMPLEXITY_BY_GRADE` too would state the difficulty rule twice, in two places that can disagree —
which is the failure the single table exists to prevent. Grade still gates their scenarios, through
`_pick_scenario(difficulty, grade_band)`.

Two scenario-level leaks were the concrete bugs behind this, both now gated by `grade_band` rather
than `difficulty` alone (a "hard"-difficulty 1st grader — a real state, since difficulty and grade
are independent inputs — could reach either regardless of the topic-selection gate above, once the
topic itself was already reachable):

- **`expressions`' "simplify" scenario** (`2x + 3x`, algebraic notation) was one of three scenarios
  picked by unconditional `random.randint(1,3)` at every grade. Withheld from "early"/"middle"
  bands now (`_pick_scenario(grade_band)`); only "upper"/"advanced" (grades 7+) see it. This means
  grade 6 -- pre-algebra-ready per the topic-selection rule above -- won't get "simplify" from this
  particular topic either, a deliberate simplification rather than adding a fifth grade bucket for
  one scenario.
- **`angle_relationships`' scenario 5** (`algebra_complementary`, solves an equation for x) was
  gated to "hard" *difficulty* only, with no grade check -- so a struggling-topic or randomized
  "hard" pick could still reach it at any grade. Withheld from "early"/"middle" bands the same way.

`geometry` already gated scenarios by difficulty (`DIFFICULTY_SCENARIOS`); added an orthogonal
`EARLY_BAND_SCENARIOS` filter on top, since circle/volume/pythagorean-theorem scenarios assume
formulas grades 1-3 haven't reached regardless of which difficulty tier picked them.

**Most of the ten topics are still defense-in-depth for "early" band, not primary content**, since
`_allowed_topics()` above keeps `algebra`/`probability`/`rationals`/`mean`/`median`/`mode`/
`angle_relationships` from ever reaching a grade 1-3 session in the first place. Their "early"
tables exist only to fail safely if that gate is ever bypassed -- write real curriculum depth into
`ordering`, `geometry`, and `expressions` first if extending this further, since those three are
what grades 1-3 actually see. `supabase/seeds/lesson_plans_priority_topics.sql` seeds exactly those
three across all four bands, and `lesson_plans_remaining_topics.sql` seeds the other seven at
`upper`/`advanced` only — 26 rows in total, with `early`/`middle` left unseeded for those seven
because `_allowed_topics` already keeps them out of grade 1-5 and an unseeded cell fails open to the
heuristics. Both are dashboard-run scripts rather than migrations, because a migration would
re-apply their text over any later dashboard edit on every rebuild.

**A lesson plan must describe question shapes the generator can actually emit, and the limits are
tighter than the grade band.** Objectives are prompt text, so anything they invite, the model will
attempt — and the solver then scores it, correctly or not. Read off the code, then confirmed by
generating: `algebra` takes `solve(...)[0]` and splits on a single `=`, so one linear equation with
one solution — a quadratic would present one root as the answer and mark the other correct choice
wrong. `probability` has three scenarios (one named category, its complement, a die condition) and
no compound or conditional events. `rationals` is `a/b` fractions with mixed numbers forbidden by
the prompt. `mean`/`median`/`mode` are a listed dataset and one statistic — no box plots, no MAD, no
comparing distributions. `angle_relationships` is two angles in one stated relationship, with no
diagram to refer to. So at these bands **`advanced` means harder numbers and one more reasoning step
inside the same question shape, not different mathematics.**

**Three wrong-answer bugs came from seed text alone, all found by reading generated output and none
catchable by `grade_appropriateness`** (2026-08-18, llama3.1:8b). "Counted from a described
condition" produced *"either blue or yellow"* — a compound event — scored **1 against a true 10/21**.
"Recognise that a dataset may have no mode at all" is true of the subject and wrong as an
instruction: nine distinct rainfall readings, **no mode, answer 0**. And a percentage framing
("80% of 15 brands") also scored **1**, because percentages give the solver no counts to divide.
Each is now forbidden in the objectives *and* in the row's `notes`, which is where a future editor
will look. The general rule: **an objective that is pedagogically true can still be an instruction
the solver cannot score** — check what a cell generates before trusting it, not just what it says.

### Angle answers are whole numbers through 5th grade, and decimals after

Scenario 5's coefficients are unconstrained, so `algebra_complementary` returns things like 11.875
(displayed `11.88`). The lesson-plan text asking for whole numbers did not stop it — prompt, not
enforcement, as usual — so `LLM_angle_relationship_generation` now checks the **solved value** and
regenerates when it is fractional for a young student. A decimal answer is not a defect in itself:
from 6th grade it is ordinary mathematics, and the rule is scoped to the grades where it is not.

**Keyed on the raw grade string, not `_grade_band()`.** The line falls between grade 5 and grade 6
while `middle` spans 4, 5 **and** 6 — so no band boundary is in the right place, and using one would
either impose whole numbers on a 6th grader or allow decimals for a 4th. Same reason
`LLM_topic_decider._allowed_topics` is grade-keyed; `test_the_cutoff_splits_the_middle_band_which_is_why_it_is_grade_keyed`
pins it. An unrecognised grade falls through to "decimals allowed", matching `_grade_band()`'s own
`advanced` default: the constraint is a scaffold for younger students, so the safe direction when
the grade is unknown is to leave the mathematics alone.

**The solve moved inside the retry loop to make this possible** (`_solve_scenario`). Whether the
answer is a whole number is a property of the *solved value*, not of the question text, so it cannot
be checked until the scenario has been evaluated — and a question that fails has to be regenerated,
not patched. An unrecognised scenario now returns `None` and retries rather than falling through
with `solution` unbound. Measured after: grades 4-5 whole on 6 of 6, grades 6+ free to return
`14.29`/`16.67` as before.

### `grade_appropriateness` checks the output, because everything else only checks the prompt

`COMPLEXITY_BY_GRADE` and the lesson-plan text are both **prompt-level** — they ask the model for
something and nothing verifies it complied. That is the same shape as every rule this codebase has
already had to move into code, so `find_violation(question_text, topic, grade_band)` runs inside
each generation retry loop: a violation retries, and exhausting the retries raises, which
`_prefetch_worker` already catches. Nine topics are wired in.

**It tests one thing — algebraic variable notation reaching a band that must not see it** — and the
narrowness is the design. A check with a real false-positive rate is worse than no check: it burns
retries, and a question rejected for a bad reason looks exactly like a model that cannot follow
instructions. `x` as a multiplication sign is the false positive that actually occurs here, so the
pattern is `\d+[xyn]\b` — anchored so `2x` matches while **`6 x 4` and `6x4` do not** (the trailing
`4` kills the word boundary). `test_ordinary_questions_are_not_refused` is the load-bearing half of
its test file; a naive `/[xyn]/` fails exactly those cases.

**`algebra` is deliberately exempt at every band.** Its own early-band content is one-step equations
with x, so a blanket rule would reject every question the topic exists to ask — the gate protecting
grades 1-5 from algebra is `_allowed_topics`, not this. `geometry` is early-only, since `upper`
legitimately labels triangle sides `a`, `b`, `c`.

What it deliberately does **not** check: magnitude/decimal/negative rules (`-` is also a hyphen and
a range separator, so detection would be guesswork), and whether the question reflects the
lesson-plan text's *content* — that needs a model to judge, which puts an unbounded LLM call on the
hot generation path. **So this bounds the damage a bad lesson plan can do; it does not confirm a
good one was followed.** Seeding still needs its effect checked by reading generated output.

**Forbidden operators in early-band `expressions` are the one exception, and they are checked
because reading the output found them.** Measured on llama3.1:8b with the lesson plans seeded
(2026-08-18, grade 1 / easy): **2 of 8 questions came back with parentheses** — `Solve 5 + (2 - 1).`
— and a separate run produced `Evaluate (3+2)*4-1.`, both while the *same prompt* said "ADDITION AND
SUBTRACTION ONLY. Do NOT use multiplication, division, or parentheses." **A few-shot example beats a
textual constraint**: every scenario example in `expr_prompt` is written for older students
(scenario 1's is `36/3+(8*2)-(15-7)+4`), and the model followed their shape over the rule. Three
changes, all needed: scenario 2 (`order_of_operations`) is withheld from `early` — it is CCSS 5.OA.1
and is *defined* by mixing precedence, so it cannot be expressed within the band's rule at all —
`EARLY_BAND_EXAMPLE` gives the band a worked example in the shape it is allowed, and the operator
check rejects what still slips through. Re-measured after: **10 of 10 compliant**. Unlike negatives
and decimals these characters have exactly one reading inside a generated expression, and the prompt
already constrains the topic to `+ - * / ( )`, so there is no third interpretation available.

**That is the general lesson, not a fact about one topic: seeding a lesson plan does not make the
model follow it, and neither does an unambiguous instruction sitting next to a contradicting
example.** Anything added to these prompts needs its effect read off generated output before it is
believed — which is what `scripts/` has no home for yet and was done by hand here.

### The question shown and the data scored are two fields, and they must agree

Every generator returns a `question_text` the student reads and a separate structured field the
solver computes from — `variables` for the dataset topics, `values` for `ordering`, `items` +
`scenario` for `probability`. **Nothing checked that they described the same thing**, and
`LLM_mode_generation.py` carried a stale note contemplating exactly that ("*POSSIBLY: manually
generate solution using numbers from question_text*").

Measured 2026-08-19 on llama3.1:8b, **2 wrong answers in 12 generated**:

- **mode** — shown `8, 4, 12, 16, 4, 14, 8, 10, 20, 4`, answered `[8, 4]`. 4 occurs three times and
  8 twice, so 4 is the only mode; the scored `variables` were not the numbers on screen.
- **probability** — shown *"what is the probability of selecting an EDM band?"* over 17+23+14+15 = 69
  bands, answered `18/23`. That is 54/69, the **complement**: the text asked a positive question
  while the JSON said `scenario: not_probability_of`.

**This is the worst failure shape available here** — the question is well-formed and answerable, the
student answers it correctly, and is marked wrong against data they never saw. Worse than a refused
question, which costs one retry and nothing else.

`question_consistency.dataset_mismatch` compares the numbers **after the last colon** against the
scored list (these prompts all put the dataset there, which is what makes locating it reliable);
`negation_mismatch` requires a negated wording and `not_probability_of` to imply each other, **in
both directions**, since a negated question scored as `probability_of` is wrong by the same amount.
Wired into `mean`/`median`/`mode`/`ordering`/`probability` inside the existing retry loops.

**Both fail open**, and that is what makes them safe to run on every question: order is ignored (the
solvers sort anyway), non-numeric `variables` are skipped, and a question with no colon-delimited
list is left alone rather than compared against stray numbers in the sentence ("during a school
year", "for a week"). A false rejection burns retries and looks exactly like a model that cannot
follow instructions — the same reasoning `grade_appropriateness` is built on. They catch a clear
contradiction; they are not a proof of agreement. `algebra`, `expressions`, `geometry` and
`angle_relationships` are **not** covered: their scored fields mix operators and labels with numbers,
so there is no comparable multiset.

**In every generator's retry loop, the `if not raw:` guard comes before anything that touches
`raw`.** `extract_json` answers `None` for a response with no JSON in it — prose, a refusal, an
empty completion — which is the exact case the three attempts exist to absorb, so a `.replace` or a
`.strip()` above the guard raises `AttributeError` straight out of the loop and the retry never
happens. `LLM_median_generation` had those two lines the other way round;
`tests/test_generation_retry_loop.py` is parametrised over all ten topics so the next copy cannot be
a one-off.

**Measure how often a fail-open check *engages*, never just how often it fires.** A check that never
finds anything to compare reports a perfect false-positive rate while doing nothing, and reads as
evidence that it works. Live sampling of 32 generations found exactly that: the dataset check was
inert on **half** the `ordering` questions, because `_as_floats` rejected fractions as "not
comparable" — while `solve_ordering` sorts on `float(sympify(v))` and handles them fine. Fractions
are now one token and both sides are compared **by value**, so `4/5` shown against `0.8` scored
agrees, and so does `32/40`. A **mixed number** anywhere in the text fails open: `1 1/2` is one value
to a reader and two tokens to the regex, which truncates the list at it and would report the
tokenisation as a dataset disagreement.

### A lesson-plan cell has four ways to contribute nothing, and they are named

`lesson_plan_context` returns `None` for an unseeded cell, a blank row, a failed read, and missing
credentials — all four degrade identically to the difficulty/grade heuristics, which is right, but
they were also indistinguishable in the log. They are very different problems: a content gap
somebody has to write, a half-finished edit, an outage, and a misconfigured process. `lookup_reason()`
names them (`NO_ROW` / `BLANK_ROW` / `READ_FAILED` / `NO_CREDENTIALS` / `FOUND`) and each logs its
own line. Nothing in generation branches on it — every non-`FOUND` reason degrades the same way —
so this is diagnostics, deliberately.

**`READ_FAILED` and `NO_CREDENTIALS` are not cached**, unlike the other three: caching an outage
would keep answering `None` for the full TTL after the database came back, and credentials can be
loaded later in a process's life. The cached reasons keep the log to one line per cell per TTL.
