# Handoff

Rewritten 2026-09-16 at `18f5a2e`, after the classroom simulation's Phase 0 merged. **`CLAUDE.md`
is what is kept current** — every durable rule from this work lives there, and this file is only
what is *in flight*: what is done, what is next, and how to do it. When a step here lands, move its
rule into CLAUDE.md and delete the step.

**Scope: this session's work only.** Two things shipped — the Common Core standard on questions
(#180) and Phase 0 of the classroom simulation (#187, #190) — and the simulation's Phases 1–6 are
open. Other sessions' threads against this repo are not tracked here; anything of theirs that this
work depends on is in CLAUDE.md, which is where a reader should go for it.

The plan is `~/.claude/plans/we-will-be-doing-encapsulated-magpie.md`, with its Phase 0 superseded
by `~/.claude/plans/nested-singing-scroll.md`.

## Suite counts at this head

Run in this session at `18f5a2e`. **Record totals, not pass counts** (CLAUDE.md's canary rule).

```
CANARY baseline: main 18f5a2e | tree: clean | last suites: sidecar 826 / backend 1854 (1850 + 4 skipped) / frontend 714 (62 files)
```

After Phase 1 (1a #191, 1b #192, 1c open), measured on the 1c branch:

```
CANARY: chart-summary-endpoint | tree: clean | last suites: sidecar not run / backend 1897 (1896 + 1 skipped) / frontend 760 (62 files)
```

**The sidecar was not run and its count is carried, not measured** -- nothing in Phase 1 touched
`EEGResearch`. Re-measure it rather than quoting 826 as if this session produced it. The backend's
skip count fell from 4 to 1 and the frontend gained 45 tests over three PRs; neither is a deletion.

---

# Common Core standards on questions. DONE.

Merged as **#180** (`fc1018e`). A question now carries the CCSS code it follows, and five surfaces
render it. It is the first stored field derived from the *student's grade*, which is what makes the
dedupe rule at the end of this list a real decision rather than a detail. Fully documented in
CLAUDE.md under *A question carries its Common Core code*; in outline:

- `backend/ccss_standards.py` — `ccss_for(topic, grade, scenario)` over grade-keyed ladders.
  Resolved **by grade, not band** (a band spans three grades and the standard changes inside it) and
  **by scenario first** (`triangle_sum` is 8.G.5 inside a grade-7 topic).
- All 17 `LLM_*_generation.py` files attach it to their return dict;
  `20260916000000_question_ccss_standard.sql` adds the nullable, no-default column.
- `CCSSBadge.jsx` on the same five question-rendering surfaces `QuestionFigure` reaches, with the
  same source-scan exhaustiveness test.
- **`add_question_to_supabase` dedupes on text *and* standard**, so one text generated at grade 6
  and again at grade 8 is two rows rather than one whose badge contradicts what the second student
  saw. The accepted cost is a visible duplicate in the teacher's bank.

Nothing here is open. It is listed because it shipped in this session and its migration is on
remote; Phase 2 below carries the one operational consequence (a local stack that has not run
`npx supabase migration up` 500s on every generated question).

---

# The classroom simulation

The goal: 1 teacher, 1 class (6th grade), 30 students, 30 parents linked 1:1, parent-enabled EEG +
`headband_optical` consent (camera off), each student doing 20 practice questions (two sessions of
10, topics and difficulty independently randomised) and 20 adaptive questions on difficulty bias
"Auto" — **driven entirely through the real UI**, no API or database shortcuts — then a verification
pass over every student, teacher and parent tab, then a bug-and-metrics report.

## Phase 0 — simulator realism. DONE.

Merged as **#187** (`62eb794`) and **#190** (`18f5a2e`). The simulator now models the *device* around
the signal, so a classroom-scale run on `EEG_SOURCE=sim` exercises the same paths a headband does.
All of it is documented in CLAUDE.md under *The simulator pairs like a headband*; the constants below
are the knobs a later phase might want to turn.

| Step | What shipped | Where |
| --- | --- | --- |
| 0.1 Pairing | `send_bridge_command` runs the bridge's `refresh` / `connect` / `disconnect` state machine against `SIM_DEVICE_NAME = "MuseS-SIM0"`; the pairing fields follow it. `eeg_age_ms` is a packet clock for which the sidecar's sample stream stands in: null for `PAIR_SETTLE_SECONDS` (5 s) after every connect, then time since the last read — so a *stopped* stream is the drop, and both `linkSettling` and `linkAlive` are reachable. | `EEGResearch/src/app/services/eeg_ingestion.py` |
| 0.2 Battery | Null for `BATTERY_FIRST_REPORT_SECONDS` (50 s) after every connect, then a level drawn once from `BATTERY_START_RANGE` (55–100) draining at `BATTERY_DRAIN_PCT_PER_HOUR` (10) on the clock, floored at a reported `0.0`. Survives a disconnect; the *report* goes null with the link. | same |
| 0.3 Contact | Two layers on the clock: a strap alternating seated/loose episodes (`STRAP_PHASE_SECONDS`) over per-electrode HSI streaks (`CONTACT_WEIGHTS`, `CONTACT_STREAK_SECONDS`). Measured through `SignalProcessor._contact_ratio` over two simulated hours: good ~30%, degraded ~55%, poor ~14%. | same |
| 0.4 Task response | `record_answer` → `eeg_poller.notify_answer` (pull only, live poller only, one-worker pool, `NOTIFY_MAX_PENDING` 8) → `eeg_client.report_answer` → `POST /api/v1/session/answer` → `stream_manager.report_answer`. Hardware answers `applied: false`. The sim nudges hidden focus/calm into a bounded (`TASK_BIAS_BOUND` 0.25) decaying (`TASK_BIAS_DECAY_SECONDS` 90 s) offset applied *before* the bands are solved. | `main.py`, `eeg_poller.py`, `eeg_client.py`, `stream_manager.py`, `EEGResearch/src/app/main.py` |
| 0.5 Pulse | `optics_window` synthesises a 4-channel 64 Hz pulse (`HEART_REST_BPM_RANGE` 62–84, raised by misses via `HEART_TASK_NUDGE`) fed through the **unmodified** `build_heart_record`. **Opt-in via `EEG_SIM_OPTICS`, off by default**, and every window is marked `synthetic`, which both ingestion paths write into the row's `raw`. | `eeg_ingestion.py`, `optics_processing.py`, `push_client.py`, `signal_mapping.py`, `main.py` |
| 0.6 Docs | Class docstring and the CLAUDE.md simulator subsection; the heart-block and battery paragraphs that said `sim` reports nothing are corrected. | `CLAUDE.md` |

**Three review rounds shaped this and the reasoning is in CLAUDE.md, not here.** The short version, so
a later change does not undo them: the age clock must not be stamped by the *consumer's* reads (two
earlier shapes each lost a page state); a synthesised heart rate must be opt-in *and* marked, on
**both** ingestion paths through the shared mapper; and the answer notification must not run on the
request thread, because `record_answer` is a sync endpoint on anyio's ~40-slot pool and `requests`
applies its timeout to connect and read separately.

## Phase 1 — product features. DONE.

Three independent pieces, shipped as three PRs. Every durable rule is in CLAUDE.md; the outline is
here only because these landed in this session.

| Step | Shipped as | What it is |
| --- | --- | --- |
| 1a Practice question-count picker | **#191** | `PracticeSetup` offers 5/10/15/20 and hands the number down through `onStart(session, count)` to `PracticeTest`'s `questionCount` prop. Frontend only; nothing is sent to the backend, matching Adaptive's question goal. No "No limit", and the picker is hidden in flashcard mode. CLAUDE.md: *A practice test's length is a prop*. |
| 1b Strategies panel on the teacher report | **#192** | `viewerRole` frames the same advice for a teacher; the endpoint was already gated on relationship, not role. The heading stays "At-Home" because the prompt and the rule-based fallback both are. Behind *"Hide sensor data"*, because the advice is sensor data in prose. |
| 1c Chart-explaining summary | this branch | `POST /api/students/{id}/chart-summary`, the deterministic sentences plus an optional model rephrasing under `chart_summary_llm_enabled` and the four bounds. `ChartSummaryPanel` on both report routes, on demand. CLAUDE.md: *The chart summary states numbers*. |

**Three things about 1c a later change should not undo.** The model is handed the finished sentences
rather than the aggregates, which is the only reason numeric fidelity is checkable at all; the
allowed figures are read *out of those sentences*, never enumerated from the basis fields (enumerating
rejected correct replies over a rounded heart rate and over a revocation date); and the three reads
behind one response each report their own `retrieved`, because `sessions` comes from the signal
aggregate and an empty trend is indistinguishable from a student's first week.

**The residual limitation Phase 6 has to report**: the numeric check is a containment check. A reply
that swaps the focus and stress figures uses only allowed numbers and passes. Closing that means
parsing the reply back into measurements, which is a second implementation of the sentences being
parsed, so it is stated rather than solved.

## Phase 2 — operational setup. NOT STARTED.

Three things, and **two of them are traps this session found**:

1. **`GENERATION_DAILY_CALL_LIMIT` is 200 in `backend/.env`, not the 2500 default.** Someone lowered
   it. The run is ~1,800–2,000 model calls (two per question served), so at 200 it will refuse within
   the first two students. Raise it to ~5000 for headroom **and write down the original value**;
   Phase 6 reverts it.
2. **`EEG_SIM_OPTICS` is absent from `EEGResearch/.env` and defaults to `false`**, so the simulated
   pulse is off and the run records **no heart rate at all** — every window refused as `no_samples`,
   exactly like a headband with `MUSE_ENABLE_OPTICS` off. That is the correct default and was a review
   finding, not an oversight. **Decide deliberately**: setting it gives the run a heart channel whose
   rows carry `raw.synthetic: true`; leaving it unset gives a cognitive-only run. Whichever is chosen,
   say so in the Phase 6 report, because it changes what the parent-facing heart tiles show.
3. Confirm `LLM_PROVIDER=claude` and a working `ANTHROPIC_API_KEY` (both already true). Start the
   stack with `./start.ps1` — no `-Muse`, no `-Camera`, so `EEG_SOURCE=sim` and `INGEST_MODE=pull`.
   Confirm backend (8000), sidecar (8001) and frontend (5173) are healthy before going on.

**Also check before starting**, both from the previous handoff and still live:

- `start.ps1 -Preview` writes `FACE_DEBUG_PREVIEW_ENABLED` to `EEGResearch/.env`; a checkout without
  the camera-preview branch refuses to boot on it (pydantic `extra_forbidden`). Delete the line if
  present.
- `add_question_to_supabase`'s dedupe lookup raises on a missing `ccss_standard` column and turns
  every generated question into a 500 until `npx supabase migration up` has been run locally.

## Phase 3 — scripted account creation. NOT STARTED.

**Playwright is not installed anywhere in this repo** (no `node_modules/.bin/playwright`, nothing in
`package.json`). Installing it is step zero of this phase, and it is a real decision: it adds a dev
dependency and a browser download to a repo that has neither.

Every step is a real form submission against the running app — no API calls, no database writes. The
local Supabase stack auto-confirms signups (`enable_confirmations = false`), so there is no email step.

1. **Teacher**: register, create one class (6th grade), note the join code.
2. **30 students**: register each (role=student), set `profile.grade_level` to "6th Grade" via
   `Profile.jsx`, join the class with the teacher's code via `JoinClass.jsx`.
3. **30 parents**: register each (role=parent), read the corresponding student's user id from
   Profile → Overview, link via `LinkChild.jsx`. One parent per student, 1:1.
4. **Consent**: as each parent, Parent → Settings, turn on `eeg_enabled` and
   `headband_optical_enabled` for their linked child. **Camera stays off.**
5. Assign each student a persisted **ability profile** — roughly 5 struggling, 20 average, 5
   high-performing, plus mild per-topic variance. Persist it to a file: it drives Phase 4's
   correctness and the Phase 6 report has to be able to explain each student's numbers.

## Phase 4 — scripted sessions. NOT STARTED.

Per student, in order:

1. **Two practice sessions** (Practice → Test mode), topics and difficulty independently randomised
   per session, question count **10** via the new picker. Answers driven by the ability profile —
   weighted probability of a correct pick, never always-right or a coin flip.
2. **One adaptive session, 20 questions**: connect the headband first and confirm the UI reaches
   "Connected" (this is what Phase 0.1 made possible), leave difficulty bias on **Auto**, set the
   question goal to 20, answer 20, click "Finish session" when the goal banner appears, disconnect.

**Expect difficulty to rise only through the correct-answer streak** (`_decide_bias`): `focused`
needs focus ≥ 0.624 **and** calm ≥ 0.5 and is unreachable by design until a marker exists;
`stressed` (calm < 0.377 on `sdk`) still eases. Design the ability tiers against that, and say it
plainly in the report — an EEG-driven difficulty *rise* is not something this run can produce.

Use moderate scripted concurrency rather than strictly serial, respecting
`GENERATION_MAX_CONCURRENCY` (8, in `llm_client.py`) and `GENERATION_MAX_WAITERS` (30, at
`main.py:2326` — the plan says 12, which was the value before the load test raised it). CLAUDE.md's
load-test table is the reason to stagger starts at all: at the old cap, 30 students starting
simultaneously were served 40%, against 87% over 10 s.

## Phase 5a — full-coverage pass on the new code. NOT STARTED.

The 30-student run will not exercise everything Phases 0–1 built: it always uses the picker's
default, and contact/battery variability is probabilistic. Four **QA-only accounts**, created and
joined the same way, excluded from Phase 4's bulk run and from the report's class-wide metrics
(called out explicitly as test accounts):

- **QA-1** — registered, parent linked, **no consent ever granted, no sessions**. Check the
  chart-summary and strategies panels on both teacher and parent sides render the correct empty/off
  state, never a fabricated summary over data that does not exist.
- **QA-2** — consent on, a handful of practice questions at **15 or 20** (not the default), then
  `headband_optical` turned back off by the student. Checks the three-state reporting logic for a
  channel that was on and is now off.
- **QA-3 / QA-4** — one short adaptive session each (~10–15 questions, using the **5** and **15**
  options), connecting the headband explicitly and running connect → verify → disconnect → reconnect
  to exercise that cycle beyond the happy path. All-wrong on QA-3, all-correct on QA-4, watching the
  teacher's Live monitor and the student's own signal readout to confirm Phase 0.4's drift produces a
  visible directional shift rather than plausible noise.
  **The streak probe asserts on calm and the `stressed` label, and on confidence as signal quality**
  (moved by contact, never by calm). It must not expect `focused`.

Between them these four cover all four picker values, the full connect/disconnect/reconnect cycle,
both streak directions, and every chart-summary and strategies data state.

## Phase 5b — human verification pass. NOT STARTED.

Driven by hand in a browser, not scripted — scripting defeats the point.

- **Student side**: for a representative subset (~8–10 across ability tiers, plus any student that
  hit an edge case in Phase 4 such as a headband retry or a generation retry), walk every tab —
  Dashboard, Adaptive, Practice and Practice History, Session Review, Flags and analytics tiles,
  Profile, Preferences. Spot-check the rest for broken renders.
- **Teacher side**: every class-wide page in full (Dashboard, Classes → class detail, roster, topic
  heatmap, class accuracy trend, time-of-day heatmap, cohort signal trend and roster,
  focus-vs-accuracy, alerts feed, Question Bank) — these already reflect all 30 — plus full
  per-student report and analytics pages for the subset, including the now-visible strategies panel
  and the new chart-summary panel.
- **Parent side**: the parent Dashboard with all 30 children's tiles, plus full per-child report pages
  for the subset including both panels, and the consent and erasure screens.
- Note every bug, inconsistency, confusing copy, dead-looking tile or state that contradicts
  CLAUDE.md. **Fix nothing in flight** — the run has to stay a consistent, unmodified target
  throughout. Everything goes in the report for a follow-up pass.

## Phase 6 — report and cleanup. NOT STARTED.

- **Revert `GENERATION_DAILY_CALL_LIMIT`** to the value recorded in Phase 2.
- Publish an HTML artifact and a matching `.docx` covering: bugs ranked by severity with repro steps
  and screenshots; UI/UX issues; per-student and class-wide success metrics (**the official 30 only**,
  QA-1..4 called out separately); adaptive-difficulty progression, stating that the EEG never pushed
  difficulty up and why; consent and EEG coverage; simulator before/after notes; Phase 5a's coverage
  results; the chart-summary feature's hallucination-risk limitation; and open questions.
- The Playwright run log (accounts created, sessions completed, failed steps) feeds the metrics
  section directly.

---

# Standing notes for a fresh session

Things that cost time in this session and are not obvious from the code.

**Branching and worktrees.** `main` is usually checked out in another worktree, so `git checkout main`
fails. Branch from `origin/main`:
`git worktree add -b <branch> "$SCRATCH/wt-x" origin/main`. Documentation-only changes go straight to
`main` by ref, no PR; code always goes through a PR.

**The sidecar's editable install points at the main checkout.** Running `pytest EEGResearch/tests` from
a worktree without `PYTHONPATH` set **silently tests the main checkout's copy of the code** — a green
run that exercised none of your changes. Always:

```bash
PYTHONPATH="$PWD/EEGResearch" EEG_SOURCE=sim API_TOKEN=t ADMIN_TOKEN=a EEGResearch/.venv/Scripts/python.exe -m pytest EEGResearch/tests -q
```

**The frontend suite cannot run in a fresh worktree** — no `node_modules`, and installing them is a
multi-minute download. Run it in the main checkout, and confirm that checkout's branch carries no
frontend changes of its own before quoting the number.

**Mutation checks: restore from a copy, never `git checkout --`.** A `git checkout` restore inside a
mutation helper has wiped uncommitted edits here before. The working shape is `cp file /tmp/orig`,
`sed -i`, run, `cp /tmp/orig file`, and assert the mutation was actually applied (a `sed` that matched
nothing reports a green suite that proves nothing).

**Editing Python from bash heredocs is fragile.** Quoting inside a `python3 - <<'EOF'` block inside a
compound bash command has failed with `unexpected EOF` more than once. Write the edit script to the
scratchpad with the Write tool and run it by path.

**`test_eeg_poller.py`'s first test fails when that file runs alone**, at `main` as well as on a
branch: `main` is first imported by the school-year fixture, which rewires the consent check after
the consent fixture set it. It passes in the full suite, where `main` is imported at collection. Not
a regression; do not chase it.

**Three venvs.** `EEGResearch/.venv` runs the sidecar, `Website/AdaptiveLearning/backend/.venv` runs
the backend app, and the repo-root `.venv` is what `pytest` uses for the backend suite. A package in
one says nothing about the others.

**The working directory shifts between tool calls.** `cd` inside a compound command moves it for
later calls; use absolute paths.
