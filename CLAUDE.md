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
| `Website/AdaptiveLearning/backend` | FastAPI app (`main.py`, ~2.3k lines) — the product API on port 8000. Also the `LLM_*_generation.py` question generators and `LLM_topic_decider.py`, which run against a local Ollama. |
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
**Gaze needs `pip install -e ".[face,gaze]"`** — MediaPipe is its own extra, deliberately, since it
is ~50 MB and a second ML runtime for a channel that is off by default. The scripts check for it
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

`npm run lint` is non-blocking in CI against a backlog of ~48 pre-existing errors. Don't add to it,
and don't make it blocking until the backlog is gone.

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
`INGEST_RATE_WINDOW`, and the `STRATEGY_LLM_*` / `STRATEGY_RATE_*` group below. The ingest bounds
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

**Scope that precisely.** It is not "the pulse is absent from the video" — it is that the pulse is
not recoverable *from the mean RGB of our three ROI boxes by POS*. Three spatial averages per frame
is a small fraction of a frame, and a learned model over per-pixel multi-region input has far more to
work with; the reference implementation reports ~95% accuracy with RhythmMamba over full video, which
is a different method on different information. Raw R/G/B of those means show the same, so POS is not
at fault.

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

**Gaze is wired** (Phase 11 step 2). `FaceCaptureAdapter` runs the face-mesh landmarker on its own
`GAZE_INTERVAL_S` cadence — 5 Hz, not the frame rate, because it is a *second* detector doing its own
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
One adult is not a validation set for a construct whose failure mode is population-specific. The
teacher's Live attention gauge, `SessionReview`'s attention ribbon, the parent/teacher
`face_attention` tiles and one line of the LLM strategy prompt all still read a value nothing
computes — the three-state tile logic renders `No sensor` / `Calibrating` rather than a number, so
nothing lies. Don't drop the column or the UI, and don't fill it without the measurement.

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
relation — so it needs the manual camera check. **Passed 2026-08-12** (one adult, laptop
webcam): square on `yaw 5.97 / pitch -13.76 / roll -4.40` with no refusals, eyes left
`gaze.x +0.442`, head left `yaw +32.97`. That confirms the table's left/right, both sign
conventions, and that the model handedness now matches a real frame — the same three steps refused
every frame an hour earlier. It says nothing about pitch/roll *accuracy* against a reference, and
nothing about children. The `-13.8` pitch at square on is the predicted bias, not a fault: a laptop
camera sits below eye level and `CANONICAL_FACE` is an adult mean face, so a systematic offset of
this size is expected and is why the step's tolerance is 20°. Re-run it after any change to the
index table or the canonical model:

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
python scripts/verify_landmarks.py
```

Three prompted steps with automatic verdicts — square on, eyes left, head left — because a check
that costs twenty minutes of assembling a camera loop is a check nobody runs. Records no video.
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
  helpers.
- **Raises** (`/api/eeg/{start,muse/refresh,muse/connect,muse/disconnect}`) — call
  `_refuse_under_push(what)` in `main.py`, *before* `eeg_client.is_alive()`, or the misleading 503
  wins the race. Don't write the 409 out by hand; one inline copy already drifted from the helper
  that claimed to have replaced it.

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

Access is a **relationship**, not a path segment or a role claim. Don't namespace an endpoint
under `/api/teacher/` when parents legitimately read it too, and don't gate on
`user_metadata.role`.

Access-control tests live in `Website/AdaptiveLearning/backend/tests/test_access_control.py` and
run in CI.

## Recording needs consent **and** an open school year

`retention_window` is a single-row table (`starts_on`, `ends_on`, `timezone`) holding the school
year. Outside it nothing is recorded whatever consent says, and on `ends_on` the per-sample rows
are deleted — that job is a later change; this is the gate.

**It fails closed in four different ways, and they are named separately.** `_retention_window()`
answers `open`, `before_year`, `after_year`, `unconfigured` or `unreadable`, and only the first
records. An unset window is not an open-ended licence — same default as consent — and a typo'd
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

**Three close sites** — `/end`, the stale-session sweep in `start_session`, and `class_live`. Each
one also writes the rollup, and `test_every_session_close_schedules_an_archive` derives the list
from that rather than keeping its own: a fourth site added later would otherwise leave sessions
whose raw rows expire with no picture behind them, and nothing would say so.

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

**Storage does not cascade, and nothing deletes an object today.** Deleting a session or a profile
leaves its SVGs in the bucket. There is no delete endpoint in `main.py` at all, so there is nothing
to hook — erasure is #75, and `chart_paths` is what will tell it which objects to remove.

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

A channel that is off keeps its tile. Dropping the row tells a parent who
switched a sensor off nothing at all — the same failure wearing a different
shape. The one exception is a payload predating the channel (`heart_included`
absent rather than `false`): there is nothing true to say about a channel the
payload does not know about, so the row is omitted.

## The strategies model pass is optional and bounded

`/api/students/{id}/learning-strategies` always has a deterministic rule-based answer;
`STRATEGY_LLM_ENABLED` (default off) only decides whether a model gets a chance to replace it. Off,
the endpoint never opens a socket — which is what CI and any deployment without a local Ollama
should do. Every failure path degrades to the rules rather than erroring.

The bounds exist because this is a sync endpoint, so each waiting request holds one of anyio's ~40
threadpool slots: `STRATEGY_LLM_TIMEOUT` enforced by waiting on a future (an httpx timeout is
per-operation, not per-call), a 2-worker pool, `STRATEGY_LLM_MAX_WAITERS` on how many callers may
block at once, and `STRATEGY_RATE_LIMIT`/`STRATEGY_RATE_WINDOW` per user id. An abandoned wait
cancels its future, or a stalled server turns every timeout into work that still runs later. If you
add another model-backed endpoint, it needs the same four bounds — the per-user rate limit alone
does not protect the threadpool.

Model output is untrusted text: it's parsed, length-bounded, stripped of markdown emphasis and list
markers, and run through a clinical-term filter, and anything failing validation falls back to the
rules. Extend `_validated_strategies` rather than rendering raw output.
