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
`start.sh` is the mac equivalent; per-machine setup lives in `DEVELOPER_SETUP_{MAC,WINDOWS}.md`.

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
`INGEST_RATE_WINDOW` (the signal-ingest bounds — the sidecar posts with the *student's* token, so
that endpoint is a trust boundary and neither the session check nor the consent check bounds
volume), and the `STRATEGY_LLM_*` / `STRATEGY_RATE_*` group below. Frontend: `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, `VITE_API_URL`,
`VITE_EEG_DEBUG`. EEGResearch reads `.env` through `src/app/config.py` — `API_TOKEN` and
`ADMIN_TOKEN` are required, `EEG_SOURCE` picks sim vs muse, and `EEG_DEVICES`
(`station1:muse@8765,...`) drives the multi-headband registry.

The native bridge reads its own env directly, not through `config.py`: `MUSE_BRIDGE_PORT`
(default 8765), `MUSE_ENABLE_OPTICS` (default off) and `MUSE_OPTICS_PRESET` (default `1035`).

**`MUSE_ENABLE_OPTICS` stays off unless you are testing the heart channel.** On it, a 2025 Athena
is moved off `PRESET_21` onto an optics-carrying preset — which is a bandwidth trade with a sharp
edge. Measured on hardware: 4 CH EEG at 256Hz alongside **16** CH optics at 64Hz drops the BLE link
within ~20s *and* collapses electrode contact from `[1,1,1,1]` to `[4,4,4,4]`; 8 CH and 4 CH both
hold for minutes with good contact and ~63 packets/s. `MUSE_OPTICS_PRESET` picks the rung —
`1031`/`1032` are 16 CH, `1033`/`1034` are 8 CH, `1035`/`1036` are 4 CH (odd = low power). The
default sits at the bottom deliberately: the 16-channel failure took EEG down with it, and that is
not a cliff to park next to. An unrecognised value warns once and falls back.

**Headband BPM is accurate seated and fails only under gait. The two are different regimes and
the rule is scoped to the right one.**

Seated, validated against a simultaneous watch ECG 2026-08-09: **14 of 16 windows accepted, max
error 2.1 bpm** against a true 70–72. That is the operating condition for a student at a desk, and
it is good enough to record and to act on.

Deliberate desk fidgeting — shifting, leg-bouncing, head turns, typing — degrades into **refusal,
not into confident error**: 12 of 16 windows rejected at confidence 0.00–0.50, and the 4 accepted
were within 7.5 bpm. Corroborating how vigorous that was, the *watch's own ECG* failed one of its
three attempts with `Poor recording`. A medical-grade contact sensor could not cope with movement
the headband survived while correctly reporting when it could not.

**Gait is the failure, and it is a different mechanism.** Through exercise, `ppg_processing`
reported 162–167 bpm at **confidence 1.00** for six consecutive windows against a watch-verified
104 — the wearer's step cadence. 166/104 = 1.60, no harmonic relation, so no periodicity test sees
it and four have been tried. Running supplies a *sustained clean rival oscillator* for the
autocorrelation to lock onto; fidgeting merely destroys the pulse, leaving no peak and therefore no
confidence. That is why confidence discriminates in one case and not the other.

So: **seated use is cleared.** An earlier version of this rule said not to record derived BPM until
the accelerometer landed. That was over-scoped — it generalised an exercise result to a product
whose users sit at a screen. The accelerometer is still the only signal independent of the
periodicity being confused, and it is still what a walking-around deployment would need; it is not
a prerequisite for a maths lesson at a desk.

Two limits worth keeping in view: the seated validation is one adult over three minutes, not a
child over a lesson; and 7.5 bpm at high confidence is harmless for fusion (which can only ease
difficulty) while being a real if modest error on a parent-facing chart. Evidence and the failed
discriminators are in `EEGResearch/tests/fixtures/README.md`.

**Camera rPPG is validated-and-rejected. `FACE_HEART_ENABLED` stays off.** Measured
against a simultaneous watch ECG on 2026-08-08: 47.7 bpm reported at **confidence 0.74**
against a true 88, on five minutes with the face found in 8988 of 8988 frames. Not a
derivation bug: the pulse is not recoverable **from the mean RGB of our three ROI boxes by
POS**, and the raw R/G/B channels of those means show the same, so POS is not at fault. The
autocorrelation peak was 0.02 where a real pulse gives 0.3-0.7.

Scope that claim carefully. It is not "the pulse is absent from the video" -- an earlier version
of this rule said that and it overreached. Three spatial averages per frame is a small fraction of
what a frame contains, and a learned model over per-pixel, multi-region input has far more to work
with. The reference implementation this project started from reports ~95% accuracy on its best
tests using RhythmMamba over full video, which is not in conflict with the result above because it
is a different method on different information. What blocks it here is the licence -- those
weights are behind a per-requester Data Usage Agreement -- not physics.

The part that generalises past this webcam: **`ppg_processing`'s confidence does not apply
to a single-channel source.** Its three terms were built for the headband's four contact
channels -- `agreement` is 1.00 by construction against one waveform, `margin` is highest
exactly when there is no rival structure to beat, and noise scored an snr of 0.314, inside
the range the code documents as a clear pulse. So the gate in front of camera heart rate is
not weak, it is inapplicable, and better hardware alone would not fix that.

The camera ships **emotion-only**. POS is kept because it is correct and is the front half
of any future attempt, but do not read its passing tests as evidence it measures a heart
rate. Evidence and the full spectral analysis: `EEGResearch/tests/fixtures/FACE_RPPG_ECG.md`.

Read numeric settings through `_env_number(name, default, cast, minimum=...)`, not `int(os.getenv(…))`.
These are read at import, so a typo would otherwise take every endpoint down over a tuning knob for
one optional feature. It falls back on unparseable and non-finite values (`inf` passes a `minimum`
check, `nan` fails every comparison, and both break call sites in ways that look like the feature
being off) and clamps below the floor. Give every one a floor: a number is not automatically a
usable setting.

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

A ninth endpoint needs the same treatment and an entry in `_MODE_AWARE` or `_MODE_AWARE_RAISING` in
`backend/tests/test_ingest_mode.py`, which parametrises both push and pull over every member. This
was found one endpoint at a time across five review rounds because each site was written by hand and
the test listed only the endpoints someone had already remembered.

Both paths share `signal_mapping.py`. The mapping used to live in `eeg_client`, which is the pull
*transport*; the push path would have had to import an HTTP client it never calls to reach a pure
function, or keep a second copy — and a second copy of a unit conversion is how one path ends up
storing percentages while the other stores ratios.

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
  uncounted eviction is a signal path losing data with nothing anywhere to say so.
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
RMSSD is unavailable whenever the headband is off and one window in six is gated out even when it is
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

Scope is documented at the top of `frontend/src/lib/facePref.js` and is narrower than the name
suggests: a **viewer-side read control over the reporting surfaces**, not stored consent over a
child's biometrics. It doesn't stop recording and doesn't travel with the student. Live class
monitoring and session review are deliberately outside it and deliberately don't render the switch.
Both call sites point back at that file — keep them in sync, and if you extend the control, render
the switch there too. A control that silently changes a page it's absent from is worse than one
with a stated edge.

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
