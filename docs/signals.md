# Signals

Part of the AdaptiveLearning conventions; `CLAUDE.md` is the root and holds what binds here as much
as anywhere — the canary, the four numbered rules this file cites, `stress` is `1 − calm`, and
fusion asymmetry. Read those first.

**Read this file when you are touching** the EEG sidecar or the native bridge, the Muse simulator,
ingestion in either mode (`eeg_poller`, `push_client`, `/api/signals/*`, `signal_mapping`), the
heart or optics path, the camera, gaze or FER+, or anything that writes `cognitive_signals`,
`heart_signals` or `face_signals`.

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

**That is one adult, and a second does not show it.** Captured twice on a different adult under the same setup: no
channel separates (tp9 0.37, tp10 0.43, af7 0.37, af8 0.504 at 4 s — chance, against 0.90/0.91/0.27/0.82 on the first),
with a background slope of −1.34 confirming the recording itself is sound. Alpha-band power sits above the fit in
both conditions, so the electrodes see something; it does not react to eye closure. **So `EEG_SPECTRUM_SOURCE` stays
`sdk`, and not pending more data** — a measurement that reads nothing on one of the two adults it has been tried on
cannot become what every stored calm value means, and on that wearer eyes-closed rest replayed as `stressed` on 154
ticks of 480. Two people is no basis for a rate, a cause, or any claim about children: `EEG_REFERENCE.md`, *the
separation does not generalise*, lists what these runs cannot establish, and the first attempt on that wearer was
lost to mains interference that the headband's own contact grade read as good contact — which is why
`capture_eeg_reference.py` prints the 1/f slope live on the bridge source and warns below −1.0.

**The two decisions that capture was for are retired as decisions.** `EEG_SPECTRUM_POISON_SECONDS` and
`EEG_CALM_CENTRE_ON_ARM` are parameters of a measurement that produced nothing on the second wearer; both settings
stay, defaulted to the shipped behaviour, and choosing between them on the first wearer's numbers alone would be
fitting settings to the only person they worked on.

`EEG_SPECTRUM_SOURCE=local` scores calm from it on its own population scale (`CALM_ALPHA_RESIDUAL_*`, midpoint 0);
the local figure rides on every payload as
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

**Its label and its surfaces are held as they are — don't relabel it in passing.** The second wearer's capture has
now happened and did not change this: it weakened the calm side rather than the focus side, so the two still stand
or fall together.
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
**0.25**, and it is one adult's number from a table derived before the artifact poison, which does not reproduce
under it. The second wearer could not re-set it: the measurement it is a line on produced nothing there
(`EEG_REFERENCE.md`). It binds only while `EEG_SPECTRUM_SOURCE=local`, which is not the default.

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
