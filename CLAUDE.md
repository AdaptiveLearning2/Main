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

Backend tests need `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` set to anything non-empty; they
never reach a real database. EEGResearch tests need `EEG_SOURCE=sim`, `API_TOKEN`, `ADMIN_TOKEN`.
The native bridge is compile-checked on `windows-latest` with `ENABLE_LIBMUSE=OFF`, which covers
syntax and signatures but *not* the packet handling inside the guards — that still needs a manual
Windows build with the SDK before release.

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
`EEG_API_TOKEN`, `EEG_ADMIN_TOKEN`, `EEG_POLL_HZ`, and the `STRATEGY_LLM_*` / `STRATEGY_RATE_*`
group below. Frontend: `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, `VITE_API_URL`,
`VITE_EEG_DEBUG`. EEGResearch reads `.env` through `src/app/config.py` — `API_TOKEN` and
`ADMIN_TOKEN` are required, `EEG_SOURCE` picks sim vs muse, and `EEG_DEVICES`
(`station1:muse@8765,...`) drives the multi-headband registry.

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
