# Handoff

Written 2026-09-02. A snapshot, not a standing document — **`CLAUDE.md` is the
thing that is kept current**, and anything here that turns out to be durable
belongs there instead. Read `CLAUDE.md` first; this file only covers what is in
flight and what is not written down anywhere else.

---

## 1. Where things stand

Branch **`adaptive-question-goal`**, open as **PR #165**, green on all six CI
jobs, **not merged**. Nine commits:

| commit | what |
| --- | --- |
| `78b670f` | a student chooses how many questions this sitting (5/10/15/20/no limit) |
| `c49d8c1` | one `<Toaster>`, not two — duplicate banners on login and on error |
| `9352563` | end the session when a student leaves; teacher badge stops claiming a dead session is live |
| `8e7227a` | clear the check-in dismissal with the session |
| `e9d9913` | Question Bank student filter: ask by id, not email |
| `3d0b9f3` | `LoadError` says "you don't have access" for a 403 |
| `59d74c7` | lesson plans for the four grade 1-3 topics |
| `b43add8` | correct an overstated claim in that seed's comments |
| `6ca7f99` | guard: a new `LoadError` call site must declare which kind it is |

Merged just before this branch, for context: **#159–#164** — scenario grade
gating, structured outputs, four new grade 1-3 topics, and the figure pipeline.

### What each of the last five commits actually fixed

**The Question Bank 403.** `/api/classes/{id}/students` returns `user_id` and
`name`. The picker read `s.id` and `s.display_name`, which do not exist on that
row. **An `<option>` with an undefined `value` falls back to its own text
content**, and the text was the email — so it sent `student@gmail.com` to an
endpoint that resolves a uuid through `_verify_can_view_student`. The test
fixture carried the same wrong shape, so five tests passed over a filter that
could not work.

**The error copy.** `LoadError` blamed the backend for everything, including
that 403. It now picks its sentence from `error.status`: 403 says access was
refused and **withholds Try again**, 401 says the session expired and keeps it,
everything else — including an error with no `status`, which is what a dropped
connection looks like — keeps the original wording.

**The lesson plans.** Five rows in
`supabase/seeds/lesson_plans_young_topics.sql`, not sixteen: `TOPIC_MAX_GRADE`
makes most of the band grid unreachable. Verified by generating through each
cell, which found a real failure — see §3.

---

## 2. Do these next

### 2a. Done (2026-09-02)

Both one-off production tasks from this section have been run by hand in the
Supabase dashboard SQL editor: the lesson-plan seed
(`supabase/seeds/lesson_plans_young_topics.sql`) and the empty-session
cleanup. Neither is a migration, so nothing re-applies them automatically —
if production is ever rebuilt from scratch, both need running again.

### 2b. Open work, roughly in order of value

| # | item | why it is not done |
| --- | --- | --- |
| 1 | **Grades 9-12 have no content of their own.** 81% of grade-9 questions measured ≥3 grades below grade. | Needs *solvers* — quadratics, systems, functions, spread — not prompt text. Each must be able to **score** what it asks, which rules out most of high-school maths here. See CLAUDE.md, "Grades 9+ have no content of their own". |
| 2 | **The spend posture has never been exercised end to end.** `GENERATION_MAX_WAITERS`, the daily ceiling, the concurrency cap. | Needs a class of ~30 starting together. Everything is unit-tested; nothing has met real simultaneous load. |
| 3 | **`attention` still has no producer** (Phase 11 step 3). | Blocked on a *labelled reference*, not on code. Every surface that rendered it was removed in #86; put the UI back in the same change that fills the column, not before. |
| 4 | **Camera rPPG accuracy.** The ONNX export works and the cost objection is gone (22 MB, 1.5 s load vs ~34 s). | Still needs the video + ECG capture. `scripts/capture_face_video_ecg.py` is that capture. The POS rejection stands. |
| 5 | **`_solve_worker` pays sympy startup inside `SOLVE_TIMEOUT`.** At 2× CPU oversubscription the retry rescues nothing. | The real fix is a readiness signal from the child so the budget covers the arithmetic rather than the import. That is a rewrite of `_run`'s process handling. |
| 6 | **Database efficiency** — composite indexes on `cognitive_signals(user_id, ts DESC)` and `session_answers(session_id, answered_at DESC)`; drop two redundant indexes; dedupe keys for `cognitive_signals` / `face_signals`; partitioning the three signal tables. | Scoped and never started. Detail in the plan file named at the end of §5. Check `pg_stat_user_indexes.idx_scan` before dropping anything. |

---

## 3. Issues hit, and what they cost

These are the ones a new agent would otherwise rediscover. The durable ones
are already in `CLAUDE.md`; they are repeated in one line each so this file
reads on its own.

**A test fixture built from the same misreading as the code cannot fail against
it.** The roster fixture said `id`/`display_name` because the picker did. Five
tests passed over a broken filter. Build a fixture from what the endpoint
returns, and assert on the **request path** — both the option's value and its
label come from one row, so a wrong key still displays the right name.

**`lesson_plans.notes` is prompt text.** `_lookup` appends it to `objectives`
and sends the pair; the 2000-char clamp covers both. I wrote the notes as
documentation for the next editor — module names, what would have to change to
lift each limit — which is noise inside a prompt. Constraints go in `notes`;
reasoning aimed at a person goes in the seed file's `--` comments, which are
sent nowhere.

**`shape_fractions` at grade 1 / easy failed outright.** 1.G.3 holds it to 2 or
4 parts, and lowest terms then leaves exactly three legal pictures — `1/2`,
`1/4`, `3/4` — while half-of-four is what a model reaches for first. Measured
on llama3.1:8b: `2/4` on all three attempts, request failed. Fixed by naming
the three fractions in the objectives (true to 1.G.3, not a workaround): 4 of 4
generate now, 2 still spending one retry. **Check a narrow cell's retry rate,
not just that it can succeed.**

**The flaky frontend test is identified and left alone.**
`src/layout/layoutAccessibility.test.jsx`, the four `keeps Tab inside it`
focus-trap tests. It is **"Test timed out in 5000ms"**, not an assertion
failure: `userEvent.tab()` is slow and exceeds vitest's per-test budget under
CPU contention. It does not reproduce in 17 unloaded runs and never in CI
(Linux); it reproduced on the 5th run with 12 CPU hogs running. The one-line
fix if it becomes annoying is `userEvent.setup({ delay: null })` in that file.

**`missing_number`'s operator gate was prompt-level only — now fixed.** A
review reproduced it: `3 * ? = 12` served to a grade-1 request, cleared every
existing gate (`solve_missing` accepts `*`, `grade_appropriateness` only looks
for variable notation). `_forbidden_operator` in
`LLM_missing_number_generation.py` now checks the operator on the structured
token list directly, keyed off `GRADE_OVERRIDES` so the two cannot drift
apart — the same class of fix as the `expressions` parenthesis leak.

**`git checkout --` discards uncommitted work.** Used it to revert a mutation
test and lost an uncommitted guard with it. Restore from a `cp` backup or
commit before mutating.

---

## 4. Conventions this session leaned on

Beyond `CLAUDE.md`, which covers most of it:

- **Mutation-check every guard.** Three guards written this session were
  checked by breaking them; the codebase has a documented history of guards
  that could not fail (`includes('QuestionFigure')` satisfied by a dangling
  import; a peak-based concurrency test that passed against the mutation).
- **The user's standing constraints**, which have not changed:
  - **Ollama for development.** Do not bill the Claude API to test something.
    `LLM_PROVIDER` defaults to `ollama`; a local `llama3.1:8b` is installed and
    was used for every generation check in this session.
  - **`QUESTION_QUEUE_SIZE` stays 0.** A queued question is billed when
    generated and only earns its cost when answered.
  - Triple-check for wasted API calls before shipping anything on the
    generation path.
- **Reporting style the user asked for:** plain, short, state not feelings; no
  recapping what was already agreed.

---

## 5. Things that are true and easy to get wrong

- **`start.ps1` / the sidecar / the headband** are all optional at runtime.
  Never add a hard dependency on port 8001 to a path that must work without
  hardware.
- **Three venvs exist.** `backend/.venv` runs the app, `EEGResearch/.venv` runs
  the sidecar, and the **root** `.venv` is what `pytest` uses. A package in one
  says nothing about the others.
- **Run `pytest` from the repo root**, not from `EEGResearch` — `Settings`
  loads `.env` relative to cwd.
- **The Supabase CLI is a repo-local npm install** and is not on `PATH`. Use
  `npx supabase ...` from the repo root.
- **Merging to `main` applies migrations to production** via the Supabase
  GitHub integration, a few minutes later. The "Supabase Preview" check
  verifies nothing. CI is advisory — nothing blocks a red merge.
- **`supabase/seed.sql` is gitignored** and per-developer. Local currently has
  27 sessions where production has ~215; that divergence was seen and
  deliberately left.

A long planning document from an earlier session lives at
`C:\Users\akash\.claude\plans\create-the-plan-to-delegated-prism.md`. Most of
its design sections describe work already merged (PR #145, the Claude
migration); the **database efficiency** and **database security** sections are
the parts still worth reading, and §6 of it is the origin of item 6 above.
