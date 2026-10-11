# Project conventions

AdaptiveLearning is an EEG- and camera-assisted adaptive maths platform: students answer
LLM-generated questions while a Muse headband and a webcam feed cognitive and facial signals into
per-session records; teachers and parents read those back as live views and weekly reports.

## What is here, and what is one file away

This file is loaded in full into every session, so its size is a standing cost — about 40% of the
window when it held everything. The most self-contained areas are docs, read on demand:

| Read | When you are touching |
| --- | --- |
| **`docs/signals.md`** | the EEG sidecar or native bridge, the Muse simulator, ingestion in either mode (`eeg_poller`, `push_client`, `/api/signals/*`, `signal_mapping`), the heart or optics path, the camera, gaze or FER+, or anything writing `cognitive_signals`, `heart_signals` or `face_signals` |
| **`docs/question-generation.md`** | a `LLM_*_generation.py` generator or its prompt, `LLM_topic_decider`, `llm_client` or either provider, a schema in `question_schemas.py`, grade or topic gating, a lesson plan seed, question figures or CCSS codes, or the difficulty bias on `profiles` |
| **`docs/solvers.md`** | a solver (`safe_solve`, `geometry_solvers`, `angle_solvers`, `hs_solvers`), a distractor generator, the bounded sympy worker, or a `SOLVE_*` setting |
| **`docs/frontend.md`** | anything under `Website/AdaptiveLearning/frontend`: a page, component or hook, `lib/api.js` or `AuthContext`, a chart, a test or the doubles in `src/test/`, the lint configs, or a Tailwind colour |
| **`docs/reporting.md`** | a reporting endpoint or the function behind it (weekly report, signal summary, term trend, cohort panels, teacher analytics), `signal_daily_rollup` or `rollup_signal_day`, a session close or either sweep, session alerts, archived charts, how an answer is recorded, or the strategies and chart-summary panels |
| **`docs/admin-and-security.md`** | an `/api/admin/*` endpoint or admin page, the security log, the operational counters (`ops_metrics`), the 403 a new endpoint answers, or a rate limiter or a test of one |
| **`docs/student-kit.md`** | the student kit (`EEGResearch/src/kit`), its installer, `kit_build.py` or `kit_release.py`, the update gate Worker, `build_student_kit.ps1` or `publish_kit_update.ps1`, or anything the kit's self-updater or its scheduled task does |
| **`docs/environments.md`** | `start.ps1` / `start.sh` or a launcher flag, `EEG_DEVICES`, a venv, a dependency pin or `requirements*.lock`, the `Dependency scan` or `Secret scan` job, `dependabot.yml`, or a setting's name or default |

**Those are trigger conditions, not a table of contents.** Match them against what you are about to
do, before the first edit — the same move `_MODE_AWARE` and `close_sites()` make, and for the same
reason: a rule nobody re-checked is how every stale claim in this file got there. What stays here is
everything that binds regardless of area — the canary, the four numbered rules, `stress` is
`1 − calm`, fusion asymmetry — plus running the suites, Database, the core of Privacy, and the
reporting rules every surface obeys.

## How to edit this file

It reached 5,000 lines once by accumulating incident reports; these rules are what stop that
happening again, and they apply to the docs as much as to this file.

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
| `Website/AdaptiveLearning/backend` | FastAPI app (`main.py`, ~8k lines) — the product API on port 8000. Also the `LLM_*_generation.py` question generators and `LLM_topic_decider.py`, which reach a model through `llm_client.py` — a local Ollama by default, the Claude API when `LLM_PROVIDER` says so. |
| `Website/AdaptiveLearning/frontend` | React 19 + Vite + Tailwind SPA on port 5173. Routed by role: `src/pages/{student,teacher,parent,auth}`, one layout each. `src/lib/api.js` wraps the backend; `src/lib/supabase.js` holds an auth-only client (`@supabase/auth-js`, no PostgREST). |
| `EEGResearch` | Separate FastAPI sidecar on port 8001 (`src/app`), packaged as `eeg-learning-platform`. Owns headband access and signal derivation; the website backend talks to it over HTTP only, via `backend/eeg_client.py`. |
| `EEGResearch/native_bridge` | C++ bridge to the libMuse SDK, TCP on 8765. Windows-only (`winsock2`), and the interesting half is behind `ENABLE_LIBMUSE`. |
| `EEGResearch/scripts` | The sidecar's capture, replay and run scripts. **Not** the root `scripts/`, which also exists and holds the database and load-test tooling — cite both by full path. |
| `FacialRecg` | Vendored rPPG / facial-recognition reference code. |
| `supabase/migrations` | The schema. Timestamp-prefixed, applied in order. |

Two backends, deliberately: the website backend never reads a headband directly, and every caller
gates on `eeg_client.is_alive()` first, so the whole EEG stack is optional at runtime. Don't add a
hard dependency on port 8001 to a path that must work without hardware.

## Comments are short and say only what the code can't

A comment carries an invariant, a unit, a trap, or the reason for a surprising choice — one line,
two at most. Docstrings are a one-line summary plus at most three lines of contract. No history
("used to", dates, "before this fix" — git has it), no rejected alternatives, no restating this
file or `docs/`: point at the section instead. Measurements go in the fixture docs, not in
comments. Reasoning that needs a paragraph belongs in `docs/` beside the code.

`scripts/check_comment_length.py` enforces the sizes in the blocking `Comment length` CI job: runs
of more than three comment lines, docstrings over four, file headers over five, and multi-line JSX
comments fail. Licence headers and PowerShell `.SYNOPSIS` help are exempt; there is no opt-out
marker, because one would become the new home for essays.

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
worst form. On screen and in report prose they are **Calm** (`1 − stress`) and **Body arousal (heart rate)**,
named only in `frontend/src/lib/signalGlossary.js`. Only archived SVGs still draw stress; fusion's internal
`stressed` label is shown as "low calm or a raised heart rate", and model-written strategies saying "stress" are rejected.

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

### The suites

CI (`.github/workflows/ci.yml`) runs **nine** jobs on PRs and pushes to `main`: `EEGResearch tests`,
`Native bridge build`, `Website backend tests`, `Database grants`, `Comment length`, `Database migrations`,
`Frontend tests, build & lint`, `Dependency scan`, `Secret scan`. Counted by name, so a tenth on the
PR page is new or undocumented rather than a stale number. (Two more come from integrations, not CI:
`Supabase Preview`, always skipped, and `Cloudflare Pages`, a preview deploy of the frontend.) `Native bridge
build` runs every Windows-only test file too, its list held whole by `test_ci_windows_tests.py`.

**`EEGResearch/.github/workflows/ci.yml` exists and has never run.** GitHub reads workflows only
from the repository root's `.github/workflows/`, so a nested one is an ordinary file. No CI job installs
the `requirements*.lock` files (only the kit build does, the gaze lock, off CI), so a stale one turns
nothing red. **Moving it to the root is not a one-line fix**: those locks are Windows-resolved, so it
would install them on ubuntu — see *Three venvs* in `docs/environments.md` for what that costs and what to do first.

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

`npm run lint` is **blocking and clean** — `--max-warnings 0`, so a warning fails it too — and any hit is new: fix it
rather than tolerate it. **App source takes no `eslint-disable`** (`noInlineConfig` outside tests): a `react-hooks`
one makes every compiler-backed rule (`refs`, `todo`, `set-state-in-effect`, …) skip the whole component. Fix the cause;
the `set-state-in-effect` section of `docs/frontend.md` has the shapes. A test's disable says why after ` -- `.
`coverage/` is ignored by the config: linted, the result depended on whether coverage had ever been run on that checkout.

Dependencies are pinned: `backend/requirements.txt` (runtime, direct deps only, cross-platform by
design — no `pip freeze`), `requirements-dev.txt` adds pytest. EEGResearch uses `pyproject.toml`
plus `requirements*.lock`; a module-level import in `src/app` must be a runtime dependency, not a
`dev` one, since CI installs `.[dev]` and cannot see the gap (`test_packaging.py` checks pyproject
and every lock). A venv keeps the dependency list it was installed with, so both launchers' venv
checks install what a pulled sidecar `pyproject` adds or raises, or a package a pulled backend
`requirements.txt` adds (`EEGResearch/scripts/missing_runtime_deps.py`, under each venv's own python).
An existing backend venv gets its missing packages by name, never `-r`, and a pin held at another
version only warns: it may be a bump on trial, and reinstalling would revert it or, offline, stop the launch.

**There must be exactly one vite, and `npm ls vite` is the check.** A second, older vite under
`node_modules/vitest/` leaves `@vitejs/plugin-react` off the test transform, so every JSX file
compiles to the classic runtime and hundreds of tests fail with `ReferenceError: React is not
defined` while the app still builds. vitest 5 takes vite as a peer, so no override is needed; one
comes back only if a vitest bump drops vite 8 from that range. vitest and `@vitest/*` pin each
other exactly, so they move as one (the `vitest` Dependabot group). **Resolve with npm 11** (Node
24): npm 10's arborist crashes on vitest 5's peer set with `reading 'edgesOut'`; `npm ci` is fine.

**A worktree borrows from the main checkout in two ways that bite.** `git worktree remove` deletes
*through* a `node_modules` junction, emptying the main checkout's copy (drop the link with
`cmd /c rmdir <link>` first; `npm ci` restores it). And `EEGResearch/.venv` is an editable install
of the main checkout, so sidecar tests run from a worktree import *its* `src` unless prefixed with
`PYTHONPATH=<worktree>/EEGResearch`; `src.app.config.__file__` shows which. A worktree with its own
`npm ci` overruns Windows' path limit on removal: `git -c core.longpaths=true worktree remove --force`.

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
nothing else anywhere. Every other backend-written table gets `SELECT` for `authenticated` and nothing more,
except the three per-sample signal tables, which no client reads: RLS cannot apply consent per channel.

`sessions` was one that had been missed: it kept `authenticated=arwd` next to a `FOR ALL` own
policy, so a student could rewrite any column of their own sessions through PostgREST —
`started_at`/`ended_at` drive the rollup's day bucketing and the expiry cutoff, and a DELETE there
cascades all three signal tables. **RLS narrows which rows a command touches, never which commands
exist**, so an own-row policy is not a substitute for withholding the grant.

`class_memberships`, `classes`, `profiles`, `parent_child_links`, `user_math_performance`,
`user_stats` and `session_answers` keep `SELECT` only, for the same reason. The frontend's Supabase client is
auth-only, so every write in this app, `profiles` included, goes through the backend. **A stale "the
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
  transaction, and plain `CREATE INDEX` takes a `SHARE` lock while building: reads continue, every
  write waits, so on a signal table ingest stalls. On a large table, build it manually with
  `CONCURRENTLY` first; the `IF NOT EXISTS` then no-ops.

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
that depends on it.** Recognise it with `_missing_rpc(e, function, migration, consequence)`, never
a new copy: it logs the migration to apply and what that site loses until then, and the caller keeps
its own failed-read answer.

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
answer "am *I* in this class", not "is user X"), and both pin `SET search_path TO ''` with every
table schema-qualified, so a caller's temp table cannot stand in for `class_memberships`.

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

**`scripts/assert_answer_lock.sql` is the one check that needs two connections**, so it cannot roll back: it commits
marked fixtures through `dblink` and deletes them, also at the start of the next run. Run it as `supabase_admin`
(`docker exec -i supabase_db_AdaptiveLearning psql -U supabase_admin …`), since dblink needs a superuser to connect
without a password.

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

## Get the Supabase client from `supabase_client.get_client`

supabase-py's sync clients default to HTTP/2, and one HTTP/2 connection shared by threads fails
with `[Errno 11] Resource temporarily unavailable`. `get_client` returns one cached client per
process, every sub-client on one HTTP/1.1 pool; one-shot scripts build theirs with `make_client`.
**Local runs never show the bug**: HTTP/2 is negotiated only over TLS, and the local stack is plain
`http://`. `tests/test_supabase_client.py` fails on any other way of building a client.
---

# Privacy

## The network edge: who may read a response, what rides on it, how much may be sent

All of it is one block above the helpers in `main.py`, mirroring `EEGResearch/src/app/main.py`. Middleware is reachable
only through `TestClient`, so anything added to this block needs a test in `backend/tests/test_network_edge.py` or it
is covered by nothing.

**CORS is an allowlist, and `allow_credentials` has to be false for it to mean anything**: with it on, Starlette serves
`allow_origins=["*"]` by *reflecting* the asking Origin. Credentials here would mean cookies and there are none — the
bearer token goes in a header. `ALLOWED_ORIGINS` defaults to the local frontend; methods are the four the API serves
plus OPTIONS, headers the two `lib/api.js` and the push client send. A deploy that forgets the variable is refused at the
edge on the first page load — loud, and the safe direction. **Preflights are cached for 2 h (`max_age=7200`)**, one per
URL rather than one per request. **There is no app-level gzip**: Render's edge already compresses, and gzip on a
0.1-CPU instance would only add work.

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
which is the **opposite** fallback direction from `env_number`: there the safe side is the feature's own default,
here it is publishing less. Unset stays development, silently, since that is the ordinary local state and a warning on
every boot is one nobody reads.

**Read a blank env list as unset, not as a list of one empty string.** `_env_list` is `env_number`'s shape for text.
An empty allowed origin matches nothing, so the symptom is the whole frontend refused by a setting that looks
configured.

**The CSP says this server is not a document, and that is the honest policy rather than a weak one.** `main.py` serves
no HTML — no `StaticFiles`, no template, no `HTMLResponse` — so
`default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'` is exactly right for it. A backend
test asserts `main.py`'s three HTML sinks stay absent, since the policy stops being honest the moment one appears —
**and it walks the AST, because a source scan cannot tell a use from a mention**: read as text it failed on the comment
beside the CSP, which names all three. **The page's policy is the frontend's `pagesHeaders.js`**, built into Cloudflare
Pages' `_headers` from the build env (which must name the API and Supabase) and served as built by `vite preview`. It
never sets `upgrade-insecure-requests`, which would rewrite the loopback sidecar call to https.

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

**Four GET routes resolve no caller, and every other limiter here keys on the id `get_user` returns** —
so on the question bank, its count, the topic list and the sidecar health probe, none of them runs. A
middleware inside `security_headers` and CORS budgets those four by **address**, the only identity an
unauthenticated caller cannot choose; `test_network_edge.py` derives the set, so a fifth fails until budgeted.
**A route that names a student resolves its caller**, never a `user_id` query parameter. `/api/generate-question`
also keeps an address budget, in `_AUTHENTICATED_ADDRESS_LIMITER` (apart, so `_PUBLIC_LIMITER` means exactly "no
caller"): sign-up is self-service, so a per-student limit alone is a new allowance per account.
**`/api/classes/join` keeps its address budget in the handler instead** (`_JOIN_MISS_LIMITER`), because it counts
only *wrong* codes from signed-in students: charged before login, a school's first morning of correct codes, or one
student's script, would lock the whole network out of joining. Like every address budget it needs
`TRUSTED_PROXY_HOPS` set behind a proxy — at 0 every school is one bucket, and once it is spent even right codes
are refused, since a limit checked after the lookup would tell an attacker which guesses were codes. The first
request carrying `X-Forwarded-For` at 0 logs a `[config]` line. Old 6-character codes are the weak case, and
`POST /api/classes/{id}/join-code` lets the owning teacher replace one; members stay enrolled.

**An address is a school, not a student**, and that sets the numbers. A class leaves through one NAT and
`Adaptive.jsx` probes the health route every 5 s per open lesson under pull (30 s under push), so sixty students
behind one address is 720/min before anyone answers a question; the defaults sit above that. These refuse a runaway
client and are not a way to police a class. **The probe has its own bucket**: it is the largest consumer of any
budget it shares and the first thing an unrelated burst would starve. **A refused
probe is a third state** — it answers neither reachable nor unreachable, so the page goes on *acting* on
the last answer (discovery keeps running, Connect stays offered) while saying *status unavailable* rather
than *offline*. Every surface reading it has to follow, badge and sentence alike: keeping `available` stale
and leaving one of them asserting the old value puts both claims on screen at once, in the one state the
whole thing exists for. **The sentence still names what to do when the stale value is a known outage** —
withdrawing it leaves a disabled Connect with no stated reason. That needs `available` to start at `null`,
or "nobody has checked" and "checked, and it is down" are one state.

**`available` has two writers, and only one of them can be refused.** The `/api/eeg/status` poll resolves a
caller, so it is not in `_PUBLIC_LIMITER` and the address budget never touches it: mid-lesson it knows what
the refused health probe could not, and clears `probeRefused` with the value it writes.

**A status tick that did not land writes nothing at all**, and that guard belongs above the updater rather
than per field. `eegStatus` swallows its own failure into a *shaped* object — `service: false`,
`poller: {running: false}`, no `ingest_mode` — so every field reads like an answer: the sidecar is down, the
poller stopped, there is no charge, no samples were sent. All are invented in the browser from a request
that never reached a backend the failure says nothing about. Field-by-field guards miss the neighbours: an
undefined `ingest_mode` flips push to pull, lifting the exemptions that exist *because under push this poll is not
the writer* of `connected` and `battery`, so one failed tick can drop a streaming session to *Connect Headband*.
**A drop belongs to the telemetry poll in both modes** — only the bridge's own `muse_connected` says the
headband went away, which is the whole subject of `AdaptiveReconnectPull.test.jsx`.

**A hook on the event loop must not write.** `_record_security_event` does a synchronous Supabase insert,
and every other call site is in a `def` handler that FastAPI already runs in a worker thread. Middleware
is not: measured at **0.95 s** of starvation for every other request against a 1 s insert, with httpx's 5 s
timeout as the ceiling, so this one goes through `run_in_threadpool`. The cooldown makes it rare, which is
the wrong comfort — it fires under exactly the load that made it fire. **`X-Forwarded-For` is read only as far right as `TRUSTED_PROXY_HOPS`
says a proxy wrote it**, default 0 (with nothing in front, trusting it is the query-parameter hole). It is the count of
appending proxies: 1 with Cloudflare straight to the app, +1 per load balancer between, or every school behind the
edge shares one budget; and only while the origin admits Cloudflare's ranges alone. **The 429 records the
limiter and never the address**, so these events cool per endpoint rather than per caller: the log says
the public path is being hammered, not by whom, and whoever holds addresses is whatever sits in front.

Not here, deliberately: **no `TrustedHostMiddleware`** until the production host names are chosen (an allowlist with no
known host breaks everything or is a no-op), and **no HSTS or HTTPS redirect**: both are Cloudflare zone settings,
which cover the Pages frontend and the proxied API at once.

## The archived SVGs are an HTML sink, so every interpolation is escaped

`chart_render.py` renders to SVG that is uploaded to storage and later handed to a browser through a
signed URL, so every interpolation is an injection site — the six `fill=`/`stroke=` colour attributes
included, though the palettes are this module's own constants: they are a *parameter*, and "everything
is escaped except colour" is an exception nobody would carry.

One input is genuinely database-sourced: `_counts(face, "emotion")` takes pie labels from
`face_signals.emotion`. Titles and units are hardcoded at the call sites, which is a property of the
callers rather than of this module, so they are tested too.

The tests **assert on the rendered output, not the source** — a scan for `html.escape` cannot tell a
call from a mention, nor see a new interpolation that needed one. `ElementTree.fromstring` is the
check: it fails on a structural break, and `.itertext()` proves the payload landed as text rather
than being silently dropped. Two interpolations stay unescaped on purpose: `tip` is escaped at its
sink, and `{total}`/`{value}` are counts the pie divides, so a non-numeric raises first.

## PostgREST is a second grammar, and `or_`/`filter`/`select`/`order` parse their argument as it

There is no raw SQL anywhere — every read and write goes through the Supabase client or an RPC, both
parameterized — so the classic injection is absent. What remains is that those methods take *strings
that are parsed as query syntax*, and `rpc` takes a function name: a value interpolated into one is
structure, not a bound parameter. `admin_student_search` is the live case, where a bare comma in a
teacher's search term would end one `ilike` condition and begin another; it strips `, ( )` and escapes
`\` and `%` first, and that stripping is the control.

`backend/tests/test_query_construction.py` walks the AST of every backend module and requires **every
non-literal argument** to those methods to carry a recorded reason — bare names included, since
flagging only f-strings leaves the evasion of assigning to a variable first. Ten today. A second test
deletes-by-failing any entry whose call site is gone, or the list only grows and a stale justification
reads as evidence the current code was reviewed. **Its stated limit**: it reads one call at a time and
cannot see where a name came from, so it catches a new site appearing, not an existing one being fed
something new.

## A read or a write names its columns; a caller's number has a floor and a ceiling

**The service-role client bypasses column grants as well as RLS, so a migration that revokes a column's
UPDATE does not reach any statement in `main.py`.** So **build a database update from named attributes,
never from the payload as a dict**, wherever the table holds a column the caller must not set:
`profiles.role`, `classes.teacher_id`, `classes.join_code` (`update_my_profile`, `update_class`). A
`payload.dict()` write leaves it to the model not declaring the field — one edit from a self-promotion.

**A test of that has to hand the handler more than the model declares** — against today's model both
spellings write identical keys, so it has to simulate the future the guard exists for.

**Every request model inherits `StrictModel`'s `extra="forbid"`**, as defence in depth (Pydantic v2
already drops an unknown key); `test_input_bounds.py` pins the list of models that do *not*.

**The six ingest models are exempt, by name.** A sidecar runs on a student's laptop and updates on its
own schedule, so a field it gained before this backend did is ordinary version skew — and under
`forbid` that skew 422s the **whole batch**, losing every valid sample travelling with it. The cost of
staying lenient, a column reading "not measured" for ever, is what
`test_every_column_the_mapper_writes_can_be_supplied_by_the_endpoint` covers. So is `EegStartedReport`,
for the same reason: its body rides with the headband-start stamp, so its handler checks the version itself.

**Don't add `ge`/`le` to `days` or `weeks`.** All three are clamped in their handlers
(`max(1, min(payload.days, 30))`), which is this codebase's convention for a caller-supplied range. A
field bound turns that clamp into a 422 for the same input — two bounds over one number, the stricter
winning silently. **The decision rests on the clamp existing, so all three are pinned**:
`test_learning_strategies_clamps_the_day_range` and `test_the_chart_summary_clamps_both_of_its_ranges`.

**A cap on a free-text field is that field's only bound, not a nicer error.** The columns they guard
(`display_name`, class `name`, session `title`) are unbounded `text` — Postgres would store a megabyte.

**A row that reaches a browser names its columns.** `select("*")` ships whatever the table gains next
with nobody deciding; `_SESSION_CLIENT_COLUMNS` leaves out `chart_paths`, a path nothing renders.

**A caller's number needs a floor as well as a ceiling**: unfloored, `?limit=-5` is `LIMIT -5` and a 500.
`backend/tests/test_response_shaping.py` classifies every number a caller can send outside a list (the
ingest samples' readings are the exception, and say why), and a new one fails until classified.

**No read is uncapped: PostgREST cuts it at `db-max-rows` (1000), silently**, so a count is never a
list's length. `/api/sessions` sends a page of rows beside the real `total` (`count="exact"`; `None` when
none came, drawn as a dash), lifetime sums come from `/api/stats/me`, and `lib/session.js` reads it.
A read that must be whole pages on a unique key (`.gt(id)`), and takes `count="exact"` from the **first** page
only: a later page's count covers just the rows past the cursor.

## Access control — check the relationship, not the role name

Endpoints serving student data read through the **service-role Supabase client, which bypasses RLS**, so the checks in
`main.py` are the only thing between a caller and another student's data. Use the existing helpers rather than writing
a new check inline — re-deriving the rule per endpoint is how the original `class_live` guard drifted into
`owner != user AND role != "teacher"`, which let any teacher read any class:

- `_verify_class_owner(class_id, user_id)` — only the owning teacher.
- `_verify_can_view_student(viewer, student_id)` — the student themselves, a teacher of a class they are enrolled in,
  a linked parent, or an admin (a **fourth relationship** rather than letting each admin path grow its own copy of a
  report query). In every helper here **a read that fails is a 503**: a logged 403 reads as someone refused.
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
narrows *which rows*, never *which columns*, and a CHECK cannot express "not by you".** Only the grant can — and **a
column `REVOKE` does nothing while a table-level grant stands**, so the protection is that clients hold no UPDATE or
INSERT on `profiles` at all; a future edit grant must be a column list without `role`. Sign-up is unaffected:
`handle_new_user` is `SECURITY DEFINER` owned by `postgres`. `test_role_gates.py` checks the code reads the column;
`scripts/assert_signal_rls.sql` checks, with `has_column_privilege`, that no client role can write it.

Access-control tests live in `backend/tests/test_access_control.py` and run in CI.

### A parent links with a code the child made; a user id is not a secret

A student's id is on every roster a teacher reads, in report URLs and in the admin search, so holding one proves nothing.
`link_child` takes a code from `POST /api/student/link-code`: students only, no body, `secrets`, eight characters with no
O/0/I/1, 30 minutes, one per student. `parent_link_codes` has RLS on, no policies and both client roles revoked —
reading a code is enough to become that child's parent.

**The conditional delete is the claim**: the unexpired code is deleted (`returning` named), and the link written only
if a row came back. It goes back (by insert) only if the write was never tried: one that raised may still commit. The
child's role is re-read (not via `_role`, which answers `student` on a failed read). Unknown, expired and spent are one
404 and write `authz_denied`; a failed read is 503. Still "notify, not block", and **a student cannot remove a
link** — a safeguarding decision, not an omission. Tests: `test_parent_link_codes.py`.
**A child can hold a second parent account**, so every other linked parent's notice feed reports a new link, a parent
turn-on (`consent_enablements`) and an erasure, naming no account, all or nothing (`test_parent_links.py`).

## Consent — `signal_consent` decides what may be recorded

Three channels, named for the **sensor** rather than the signal derived from it: `eeg`,
`headband_optical` (heart rate today; the Athena's `OPTICS` packet also carries fNIRS, so a `_ppg_`
name would go stale), and `camera` — which covers expression **and** the rPPG heart-rate fallback. One
device, one decision: a heart-rate failover must never open a webcam the student declined.

**Everything defaults to false.** An absent row means the same as a row of falses, so there is no
backfill and an unconfigured student records nothing. `_consent()` fails **closed** on a read error and
carries `retrieved` so callers can tell "nobody consented" from "we couldn't find out".

**Withdrawal stops future recording and keeps what is already stored.** A revoked channel records
nothing further until consent is given again, and no past row is deleted. Withdrawal is not
erasure. **But every surface stops reading a withdrawn face or heart channel** — reports, session review
and its archived charts alike — and says it is off, with the date; re-enabling shows the history again.
EEG is the exception and is always read (see *A tile never says "no data"*).

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

A parent turning a channel **on** — a first opt-in as much as a re-enable — sets `parent_enabled_at` and
raises `needs_student_ack`, cleared by `POST /api/consent/ack`. A parent turning one *off* raises nothing.
Discovering a sensor by noticing data appear is not consent. The banner's copy claims no earlier
withdrawal, because a re-enable nulls `revoked_at` and nothing stored can tell the two apart.

**That rule has to hold on both ingestion paths, and for a while it did not.** `/api/signals/*` reads
consent on every request, with its session in one `ingest_gate` call (a failed read is a 503 the push
client retries, never a batch dropped as unconsented); the poller writes `cognitive_signals` directly with
the **service-role** client, so under `pull` a withdrawal stopped nothing. Now: `/api/eeg/start` refuses
**403** (not the 409 push uses — one says this student said no, the other says this deployment does not
work that way), and a running poller re-reads consent every `CONSENT_RECHECK_SECONDS`.
`eeg_poller.set_consent_check()` is wired from `main` at import and has **no default**: unwired,
`start()` raises rather than assuming yes, because an unwired deployment that assumes yes is
indistinguishable from a wired one.

**Under push the page opens the sensors, so it asks first**: `GET /api/recording/me` before switching one on, and a
fresh refusal switches it off. The sidecar's push client asks too while it holds a token, withholding and stopping what
is refused. The backend's discard stays the last line of defence; details in `docs/signals.md`.

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
different sentences; `_not_recording_reason` puts the window reason first, then an admin recording
switch (`switches=`, required of every caller), then consent, or a closed year or a switched-off sensor
sends someone to the consent screen to fix a setting that is fine. `_poller_status` follows
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
along beside the `record_*` ones. Consent already in hand reaches `_may_record` only as
`_stored_consent(row)` over the ingest gate's row — a bare dict is a `TypeError`, and the recording-sites
test traces every `consent=` back to that call.

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
school's timezone is *past* `ends_on`, everything up to and including it expires too — never on `ends_on` itself,
which is still a recorded school day. So the job is idempotent and self-healing: a missed run completes on the
next one, and a repeat deletes nothing new. That is what makes a delete with no grace period acceptable. No
window configured, or `enforced = false`, means no cutoff and nothing deleted, whatever dates linger in the row.
**So an unenforced year keeps per-sample data about children indefinitely** — a retention decision, stated on the
admin School Year page where the switch is; no rolling bound exists, and choosing one is a policy call, not a
default to invent. `main._expiry_cutoff` is the Python copy the form and the weekly report use; change both together.

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
return shape and the callers reading it. `expire_adaptive_decisions()` (03:55) is the same shape for the
same reason: adaptive decisions are signal-derived and nothing summarises them.

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
rollup row, so a day left without one keeps its raw rows past `ends_on`. **Heart is one row for two sensors**, so
erasing one source deletes only heart days it can rebuild (raw rows left) or that `heart_sources` says drew on it
(or cannot say). An expired day from the other sensor alone is the last copy and holds nothing erased.

**Archived charts go if they draw on the channel at all**, so `camera` takes `heart_rate` and
`stress_pie` with it — those mix both sensors into one picture and no pixel says which is which.
Over-deletion, preferred to serving a chart that still contains what was erased. Object paths are
**derived** in the function, never read from `chart_paths`, where they would be a delete list of the
writer's choosing. **Adaptive decisions go with an erasure of any channel**, by a trigger on `signal_erasure`, since
a fused label mixes channels: the same over-deletion, in `erase_signals`' own transaction.

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

Admin is `profiles.role = 'admin'`, read by `_role_or_raise` (a failed read is a 503, never a refusal), and
set from the dashboard SQL editor.

**It is a role rather than a side table only because the column is server-controlled on both edges**, and
both are load-bearing: one migration revokes UPDATE/INSERT on it from the client roles, another whitelists
`student|teacher|parent` in `handle_new_user` so sign-up cannot ask for it. Widening the CHECK without the
whitelist would have been a self-service admin signup — the trigger copies `raw_user_meta_data->>'role'`
straight into the column, so `signUp({data:{role:'admin'}})` from a console would have made an
administrator. The backfill migration repeats the whitelist for the same reason: it reads the same
client-supplied metadata. **A student's sign-up grade is whitelisted the same way** — only the picker's labels
(`lib/grades.js`, held equal by `test_role_gates.py`), else no grade — because the backend's `validated_grade`
never sees a value the trigger writes.

`AdminGuard` asks `GET /api/admin/me` rather than reading a role client-side; it is a UI convenience, and
every `/api/admin/*` endpoint re-checks.

### `profiles` rows come from a trigger a migration creates

`handle_new_user` runs from an `auth.users` trigger that a migration creates, with a backfill: `_role` gates on
`profiles`, and a missing row degrades `_profile` to a student-shaped dict, so a teacher is refused their own classes
with nothing to read. It is safe against a hand-made survivor — `on conflict (id) do nothing` makes a second firing a
no-op — but check for one under a different name after applying.

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
the summary RPCs' `p_include_emotion`/`p_include_heart` gate rollup rows as well as raw ones. **Nulling values on the
way out is not an implementation of this.** If there is ever no way to tell the database to skip the rows,
the correct answer is a blank tile, not a read — never fall back to a query that reads what the caller opted
out of. Assert on the **filter**, not the payload (rule 4).

That rule now belongs to consent, which is server-side and genuinely skips the read. **A viewer-side control that
needs a disclaimer saying it switches no sensor off is wearing consent's vocabulary, and is the wrong control.**

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

`SignalPanel`'s `offLabel` picks between six states, and every tile goes through `valueOrReason` rather than
branching on the channel flag itself:

| State | Shown | Because |
| --- | --- | --- |
| no samples, and an administrator switched the sensor off | `Paused by the school` | `paused_channels`, from the admin recording switches, only for a channel the student has on (heart: both sources off). It replaces "No sensor" alone: a state of now, so it never hides `Off since`, `Erased`, `Unavailable` or `Calibrating`, and changes no consent |
| consent withdrawn | `Off since <date>` | the date comes from `*_revoked_at` on the payload |
| consent unreadable | `Unavailable` | "the student turned this off" is a claim a failed read has not earned |
| read, samples arrived, none usable | `Calibrating` | a rejected window or a baseline still forming |
| read, no samples, past erased | `Erased <date>` | `*_erased_at`, only inside the tile's window (heart: every consented sensor erased); consent is often still on |
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

`components/ui/LoadError.jsx` picks its sentence from the `error.status` that `apiFetch` attaches, because naming a
layer sends someone to inspect it: a teacher whose Question Bank filter was refused went and checked a server that
had answered perfectly well. **403** ("you don't have access to X") and **404** ("couldn't find X") get **no Try
again button**, since asking again cannot change the answer — so the backend must never answer 404 for a read that
failed (`_row_or_404` answers 503). **401** (session expired), **429** (too many requests: a school behind one
address hits the address budget) and **503** (a read behind the backend failed just now) keep the button. **Anything
else, including no `status` at all**, keeps *"make sure the backend is running"*: a dropped connection genuinely is
an unreachable backend. Callers pass nothing; a page wires it by holding the error in the state it already had
(`setFailed(e)` — every read of that flag was a truthiness check).

**Name what was actually refused, not what the page is about.** `questions` is public-read, so a 403 on the
Question Bank can only ever concern the student filter — *"you don't have access to the question bank"* would
deny access to something the teacher can see behind the message.

## A roster row has `user_id` and `name` — not `id`, not `display_name`

`/api/classes/{id}/students`, `/api/classes/{id}/sessions` and `/api/teacher/students` (which `Students.jsx` reads)
return roster rows as `{user_id, name, email, ...}`, with `name` and `email` null when unset: a page draws the email,
else "No name set", never a made-up name. No roster comes from `profiles` (the frontend's Supabase client only signs
in), so no roster row anywhere has `id` or `display_name`.

Getting this wrong in a `<select>` does **not** render a blank option. **An `<option>` with an undefined
`value` falls back to its own text content**, so `value={s.id}` over a label of `{s.display_name || s.email}`
sent the student's *email* to `/api/students/{id}/questions`, which resolves a uuid through
`_verify_can_view_student` — 403 on every pick, from a picker that looked right and named the right student.
Five tests passed over that filter because the fixture also said `id`/`display_name` (rule 4). Build a roster
fixture from what the endpoint returns, `email` included — without that field the failure is not even
representable — and assert on the **request path**, since both the option's value and its label come from one
row and a wrong key still displays the right name.

---
