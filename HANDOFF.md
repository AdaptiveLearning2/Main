# Handoff — the classroom simulation

Rewritten 2026-09-21 at `cce61383`; revised 2026-09-23 against `origin/main` at `f4b0e407`.
**`CLAUDE.md` and the two docs are what is kept current** —
every durable rule from this work lives there, and this file is only what is *in flight*: what is
done, what is next, and how to do it. When a step here lands, move its rule into `CLAUDE.md` (or
`docs/signals.md` / `docs/question-generation.md`, per their trigger tables) and delete the step.

**Scope: the classroom simulation only.** Other threads against this repo — the security hardening
effort among them — are not tracked here; anything of theirs this work depends on is stated below as
a constraint, with the rule itself in `CLAUDE.md`.

Plan: `~/.claude/plans/we-will-be-doing-encapsulated-magpie.md`, with its Phase 0 superseded by
`~/.claude/plans/nested-singing-scroll.md`, and two addenda at the end (2026-09-20, 2026-09-23)
recording what landed on `main` while Phases 2–6 were open.

## The goal, in one paragraph

1 teacher, 1 class (6th grade), 30 students, 30 parents linked 1:1, parent-enabled `eeg` +
`headband_optical` consent (camera off), each student doing 20 practice questions (two sessions of
10, topics and difficulty independently randomised) and 20 adaptive questions on difficulty bias
"Auto" — **driven entirely through the real UI**, no API or database shortcuts — then a verification
pass over every student, teacher and parent surface, then a bug-and-metrics report.

## Where it stands

| Phase | State |
| --- | --- |
| 0 — simulator realism | **DONE** (#187, #190) |
| 1 — product features | **DONE** (1a #191, 1b #192, 1c #194) |
| 2 — operational setup | **CONFIG DONE; STACK NOT STARTED** — this is the next action |
| 3 — scripted account creation | NOT STARTED (opens with a real decision: Playwright is not installed) |
| 4 — scripted sessions | NOT STARTED |
| 5a — full-coverage pass on new code | NOT STARTED |
| 5b — human verification pass | NOT STARTED |
| 6 — report and cleanup | NOT STARTED |

## Suite counts

**There is no current measurement at this head.** The counts this document used to carry were taken
several merges ago and do not describe `cce61383`; quoting them would break the canary rule's whole
point (`CLAUDE.md`, *Canary*). Measure before you rely on a number, and record the **total**, not the
pass count:

```bash
EEG_SOURCE=sim API_TOKEN=t ADMIN_TOKEN=a EEGResearch/.venv/Scripts/python.exe -m pytest EEGResearch/tests -q
```

```bash
SUPABASE_URL=http://localhost:54321 SUPABASE_SERVICE_ROLE_KEY=x .venv/Scripts/python.exe -m pytest Website/AdaptiveLearning/backend/tests -q
```

```bash
cd Website/AdaptiveLearning/frontend && npm test
```

---

# Phase 0 — simulator realism. DONE

Merged as **#187** and **#190**. The simulator models the *device* around the signal, so a
classroom-scale run on `EEG_SOURCE=sim` exercises the same paths a headband does: pairing through
the bridge's own state machine, a battery that is null for the first stretch and then drains,
electrode contact that varies on the clock in two layers (so `degraded` and `poor` are both
reachable), a cognitive state that answers the lesson, and an opt-in synthesised pulse fed through
the unmodified heart path.

All of it is documented in `docs/signals.md` under *The simulator pairs like a headband*. Three
things a later change must not undo, because each was a review round:

- the `eeg_age_ms` clock must not be stamped by the *consumer's* reads — two earlier shapes each
  lost a page state;
- a synthesised heart rate is opt-in **and** marked, on **both** ingestion paths, through the shared
  mapper;
- the answer notification must not run on the request thread.

# Phase 1 — product features. DONE

| Step | Shipped as | What it is |
| --- | --- | --- |
| 1a | **#191** | `PracticeSetup` offers 5/10/15/20 and hands the count to `PracticeTest`. Frontend only; nothing is sent to the backend. No "No limit", and the picker is hidden in flashcard mode. |
| 1b | **#192** | The strategies panel on the teacher report, framed by `viewerRole`. The endpoint was always gated on relationship rather than role. Heading stays "At-Home" on both. |
| 1c | **#194** | `POST /api/students/{id}/chart-summary` — deterministic sentences plus an optional model rephrasing under `chart_summary_llm_enabled`, with the four bounds. `ChartSummaryPanel` on both report routes, on demand. |

**Three things about 1c a later change should not undo**, all in `CLAUDE.md` under *The chart
summary states numbers*: the model is handed the finished sentences rather than the aggregates,
which is the only reason numeric fidelity is checkable at all; the allowed figures are read *out of
those sentences*, never enumerated from the basis fields; and the four reads behind one response
each report their own `retrieved`.

**The residual limitation Phase 6 has to report**: the numeric check is a *containment* check. A
reply that swaps the focus and stress figures uses only allowed numbers and passes. Closing it means
parsing the reply back into measurements, which is a second implementation of the sentences being
parsed — so it is stated rather than solved.

# Phase 2 — operational setup. CONFIG DONE; STACK NOT STARTED

**This is the next action.** The two `.env` edits are made and verified present at this head. Both
files are gitignored, so this section is the only record of them and Phase 6 reverts from it.

| File | Key | Original | Now |
| --- | --- | --- | --- |
| `Website/AdaptiveLearning/backend/.env` | `GENERATION_DAILY_CALL_LIMIT` | `200` | `5000` |
| `EEGResearch/.env` | `EEG_SIM_OPTICS` | absent (= false) | `true` |

Both carry a `Phase 6 reverts this` comment in the file itself, so the record survives losing this
document. Verified on 2026-09-21: `GENERATION_DAILY_CALL_LIMIT=5000` and `EEG_SIM_OPTICS=true` are
both still in place.

**`EEG_SIM_OPTICS=true` was a decision, not a default.** The run gets a heart channel, so the heart
tiles, RMSSD, the heart series and the chart summary's heart sentence all have data to verify in
Phase 5b. Every row carries `raw.synthetic: true` through both ingestion paths. **Phase 6 must state
that every heart figure in the report is synthesised** — that is the cost of the choice, and the
alternative was leaving the whole heart path unexercised.

**The call-limit arithmetic**: 30 students × ~40 questions × 2 model calls ≈ 2,400. `200` is a
deliberate dev ceiling with its reason written beside it — a bound that stops a runaway loop on a
laptop making its first billed calls — so this raise is temporary by design.

### Pre-flight, and one check that has already gone stale once

- **Run the stack from a clean checkout at a known commit.** On 2026-09-23 the main checkout was on
  another session's branch (`response-shaping`) with that session's uncommitted edits to
  `History.jsx`. A run launched from there tests neither `main` nor anything recorded. Either wait
  until that checkout is back on `main` and clean, or run from a worktree at `origin/main`.
  A worktree needs its own copies of both gitignored `.env` files and the venvs and `node_modules`
  that `start.ps1` expects. Record the commit the run was launched at, because Phase 6 reports
  against it and the next pre-flight item depends on it.

- **`npm install` in the frontend of whichever checkout runs the stack.** `main` moved from a vite 8
  beta to stable vite 8 and the lockfile changed, so a `node_modules` installed before that is stale.
  Keep the `overrides: { vite }` entry; `CLAUDE.md` explains why removing it breaks the suite.

- **Re-run the migrations.** On 2026-09-23 the newest on `main` is
  `20260919000000_security_events.sql`, and another branch adds `20260920000000`. Treat whatever
  this document names as perishable: migrate to whatever the launched commit carries, rather than
  to a name read here:

  ```bash
  npx supabase migration up
  ```

  The failure is silent rather than loud: the security log's writer never raises, so an unmigrated
  stack loses rows and says nothing.

- **`questions.ccss_standard`, `questions.figure` and `signal_daily_rollup.stress_sample_count` must
  exist**, or `add_question_to_supabase` 500s on every generated question.

- **No `FACE_DEBUG_PREVIEW_ENABLED` line in `EEGResearch/.env`.** Clear when last checked.

- `ANTHROPIC_BASE_URL` is set in the Windows environment to `https://api.anthropic.com`, which is
  correct. `CLAUDE.md` flags a *stale* value here as a trap costing rounds to find; if generation
  ever fails with `WinError 10061`, that is the first place to look — and it is inherited at process
  start, so clearing it needs a new terminal.

### Start the stack, then verify the conversion rather than trusting it

```bash
./start.ps1
```

**No `-Muse`, no `-Camera`, no `-Optics`, no `-LocalCalm`.** Then confirm backend (8000), sidecar
(8001) and frontend (5173) are healthy.

**Read the machine's state as it is now (2026-09-21), not as an earlier run left it:**

| File | Key | Current value |
| --- | --- | --- |
| `EEGResearch/.env` | `EEG_SOURCE` | `muse` |
| | `EEG_DEVICES` | `default:muse@8765` |
| | `FACE_ENABLED` | `false` |
| | `PUSH_ENABLED` | `false` |
| | `EEG_SPECTRUM_SOURCE` | `sdk` |
| `backend/.env` | `INGEST_MODE` | `pull` |

So the camera half is already gone and the mode is already `pull` — **but the headband half is not**.
`EEG_SOURCE=muse` with `default:muse@8765` in the registry is the state a plain run has to convert,
and the registry wins over `EEG_SOURCE` for the device it names. `Update-DeviceRegistry` rewrites a
`default:` entry to what this run asked for, and `test_launcher_device_registry.py` pins that
behaviour — but **verify the result**, because the symptom of the old failure is silent: a
`default:muse@8765` left standing beside `EEG_SOURCE=sim` makes the sidecar look for a bridge that is
not running, and the run reads `eeg_source: muse` with `no_signal` throughout while every window
looks ordinary.

After launching, read back:

- `EEGResearch/.env` — `EEG_SOURCE=sim`, no `muse@8765` in `EEG_DEVICES`, no `camera:` entry,
  `FACE_ENABLED=false`, `PUSH_ENABLED=false`, and **`EEG_SIM_OPTICS=true` still present**.
- `backend/.env` — `INGEST_MODE=pull`.
- The sidecar's `/api/v1/state` — `eeg_source: sim`, and a `heart` block once a session is armed.

# Phase 3 — scripted account creation. NOT STARTED

**Playwright is not installed anywhere in this repo** (nothing in `package.json`, no
`node_modules/.bin/playwright`). Installing it is step zero and is a real decision: a dev dependency
plus a browser download in a repo that has neither.

Every step is a real form submission against the running app — no API calls, no database writes. The
local Supabase stack auto-confirms signups (`enable_confirmations = false`), so there is no email
step.

1. **Teacher**: register, create one class (6th grade), note the join code.
2. **30 students**: register each (role=student), set `profile.grade_level` to "6th Grade" via
   `Profile.jsx`, join the class with the teacher's code via `JoinClass.jsx`.
3. **30 parents**: register each (role=parent) and link to the corresponding student through
   `LinkChild.jsx`, one parent per student, 1:1. **Read `LinkChild.jsx` at the launched commit
   before scripting this step**, because the link flow is changing on another branch:
   - *Today on `main`*: the parent enters the student's user id, read from Profile → Overview.
   - *If the code flow has landed*: the student generates a link code in Profile (8 characters,
     valid 30 min, single use, a new code replaces the previous one), and the parent enters it.
     Script it pair by pair, generating the code and redeeming it straight away, so no code expires
     while it waits. A wrong code and an expired code show the same message, and redemption is
     rate-limited per parent (10/hour), so a script that retries blindly locks itself out.
4. **Consent**: as each parent, Parent → Settings, turn on `eeg_enabled` and
   `headband_optical_enabled` for their linked child. **Camera stays off.**
5. Assign each student a persisted **ability profile** — roughly 5 struggling, 20 average, 5
   high-performing, plus mild per-topic variance. Persist it to a file: it drives Phase 4's
   correctness and the Phase 6 report has to be able to explain each student's numbers.

**One open decision.** Phase 5a wants the admin console verified, and admin is
`profiles.role = 'admin'` set through the dashboard SQL editor — there is no form to create one, so
it cannot be done under this phase's no-shortcuts rule. Either add an admin account here as a stated
exception, or record in Phase 6 that those surfaces went unverified.

# Phase 4 — scripted sessions. NOT STARTED

Per student, in order:

1. **Two practice sessions** (Practice → Test mode), topics and difficulty independently randomised
   per session, question count **10** via the picker from 1a. Answers driven by the ability profile —
   a weighted probability of a correct pick, never always-right or a coin flip.
2. **One adaptive session, 20 questions**: connect the headband first and confirm the UI reaches
   "Connected" (this is what Phase 0's pairing work made possible), leave difficulty bias on
   **Auto**, set the question goal to 20, answer 20, click "Finish session" when the goal banner
   appears, disconnect.

### What the EEG can and cannot do to difficulty

**Expect difficulty to rise only through the correct-answer streak** (`_decide_bias`): `focused`
needs focus ≥ 0.624 **and** calm ≥ 0.5 and is close to unreachable at the contact this product gets;
`stressed` (calm < 0.377 on the `sdk` scale) still eases. Design the ability tiers against that, and
say it plainly in the report — **an EEG-driven difficulty *rise* is not something this run can
produce.**

**The second wearer's capture settles which scale that is.** Two captures on a second adult, no
channel reaching chance (0.37, 0.43, 0.37, 0.504 against the first wearer's 0.90, 0.91, 0.27, 0.82).
`EEG_SPECTRUM_SOURCE` stays `sdk`, the two decisions that capture was gated on are retired, and
**the run must not use `-LocalCalm`**. Phase 6 cites this rather than the single-wearer figures; the
numbers and what two adults cannot establish are in `EEGResearch/tests/fixtures/EEG_REFERENCE.md`.

### The binding constraint is the per-address rate limit, not the waiter cap

`GENERATION_MAX_CONCURRENCY` is 8 and `GENERATION_MAX_WAITERS` is 30 (grep for them rather than
trusting a line number — `main.py` is ~9k lines). Neither is what will bite. The five GET routes that
resolve no caller are bounded **by address**, and this whole run is one address:

| route | load at 30 students | limit |
| --- | --- | --- |
| `/api/eeg/health` probe | ~360/min (12/min per open page) | `PUBLIC_PROBE_RATE_LIMIT` 1800 |
| `/api/generate-question` | ~180/min at human pace; **600–900/min scripted** | `PUBLIC_GENERATE_RATE_LIMIT` 600 |

The probe is comfortable. Generation is not: a script answering instantly does 20–30 questions per
student per minute. **Pace it to roughly a question every 6 s per student** rather than raising the
cap — raising it throws away the one measurement this run could make about whether the shipped
default suits a classroom.

That pace clears both generation bounds: 30 students at ~10/min each is ~300/min against the shared
600/min address budget, and each student's ~10/min sits under the separate per-user
`GENERATION_RATE_LIMIT` of 60/min. Grep for both rather than trusting these figures.

### Measure refusals by counting 429s, not from the security log

Each refused request is one 429 carrying `Retry-After`, and `apiFetch` retries **503 only**, so a 429
is never absorbed and a client-side count is exact. Record it per student and per minute.

**`security_events` cannot supply that number even though it looks as if it can**: `rate_limited` is
cooled at 300 s on `(kind, actor, limiter)` and the public limiter records `actor_user_id=None`, so
the whole run collapses to one key — at most one row per five minutes, with no count and no address,
by design. One refused request and ten thousand leave the same handful of rows. It is a presence
flag: useful for *whether* the cap was reached, worthless for *how far past* it.

# Phase 5a — full-coverage pass on new code. NOT STARTED

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
  teacher's Live monitor and the student's own signal readout to confirm the simulator's task
  response produces a visible directional shift rather than plausible noise. **The streak probe
  asserts on calm and the `stressed` label, and on confidence as signal quality** (moved by contact,
  never by calm). It must not expect `focused`.

Between them these four cover all four picker values, the full connect/disconnect/reconnect cycle,
both streak directions, and every chart-summary and strategies data state.

**One surface none of them reaches**: the admin console, including its security-events page, which
sits behind `AdminGuard`. See the open decision at the end of Phase 3.

# Phase 5b — human verification pass. NOT STARTED

Driven by hand in a browser, not scripted — scripting defeats the point.

- **Student side**: for a representative subset (~8–10 across ability tiers, plus any student that
  hit an edge case in Phase 4 such as a headband retry or a generation retry), walk every tab —
  Dashboard, Adaptive, Practice and Practice History, Session Review, Flags and analytics tiles,
  Profile, Preferences. Spot-check the rest for broken renders.
- **Teacher side**: every class-wide page in full (Dashboard, Classes → class detail, roster, topic
  heatmap, class accuracy trend, time-of-day heatmap, cohort signal trend and roster,
  focus-vs-accuracy, alerts feed, Question Bank) — these already reflect all 30 — plus full
  per-student report and analytics pages for the subset, including the strategies panel and the
  chart-summary panel.
- **Parent side**: the parent Dashboard with all 30 children's tiles, plus full per-child report
  pages for the subset including both panels, and the consent and erasure screens.
- Note every bug, inconsistency, confusing copy, dead-looking tile or state that contradicts
  `CLAUDE.md`. **Fix nothing in flight** — the run has to stay a consistent, unmodified target
  throughout. Everything goes in the report for a follow-up pass.

**Surfaces added since the plan was written, which this pass should cover deliberately:**

- **The per-series chart filter** (#195): toggles above `SessionReview`'s session timeline and above
  `SignalPanel`'s daily and term charts, letting a reader draw any combination of focus, EEG stress,
  heart rate and RMSSD. **It reaches parents as well as teachers**, because `StudentProgressReport`
  is shared by `teacher/StudentReport` and `parent/ChildDetail`. Worth checking on both routes, with
  the "everything off" state and its *Show all* among them.
- **The CCSS badge and question figures** on the five surfaces that present a question.
- **`ScaleNote`** wherever a window straddles a score-scale change.
- **The student's session lists** on History, Dashboard and Profile: another branch changes them to
  show one page of sessions next to the real total, drawn as a dash when no total came back. Verify
  whatever the launched commit carries, and say which version that was.
- **The parent link flow**, in whichever form Phase 3 found it. If it is the code flow, check that a
  wrong code and an expired code read the same, and that a newly generated code replaces the old
  one.

# Phase 6 — report and cleanup. NOT STARTED

**Revert first:**

- `GENERATION_DAILY_CALL_LIMIT` back to `200` in `backend/.env`.
- `EEG_SIM_OPTICS` removed from `EEGResearch/.env` (it was absent, not `false`).
- Anything Phase 4 raised for pacing, if the decision to pace rather than raise was reversed.

**Then publish** an HTML artifact and a matching `.docx` covering: bugs ranked by severity with repro
steps and screenshots; UI/UX issues; per-student and class-wide success metrics (**the official 30
only**, QA-1..4 called out separately); adaptive-difficulty progression, stating that the EEG never
pushed difficulty up and why; **that every heart figure in the report is synthesised**; consent and
EEG coverage; simulator before/after notes; Phase 5a's coverage results; the chart summary's
containment-check limitation; the 429 count if generation was ever refused; and open questions.

The Playwright run log (accounts created, sessions completed, failed steps) feeds the metrics section
directly.

---

# Standing notes for a fresh session

Things that cost time and are not obvious from the code.

**`CLAUDE.md` is now three files.** It was split into `CLAUDE.md`, `docs/signals.md` and
`docs/question-generation.md`, each with a stated trigger for when to read it. Match the triggers
against what you are about to do before the first edit; the signals and generation rules this plan
leans on are in the two docs.

**Branching and worktrees.** `main` is often checked out in another worktree or by another session,
so `git checkout main` may fail. Branch from `origin/main`:
`git worktree add -b <branch> "$SCRATCH/wt-x" origin/main`. Documentation-only changes go straight to
`main` by ref; code always goes through a PR.

**A fresh worktree cannot run the frontend suite as-is** — no `node_modules`, and installing them is
a multi-minute download. Linking them in with `ln -s` **does not work**: MSYS copies instead of
linking and node then fails with `MODULE_NOT_FOUND`, which vitest reports as a run producing no
tests — silence that reads as a pass. Use a real junction
(`cmd //c "mklink /J node_modules <abs path to the main checkout's node_modules>"`), or run the suite
in the main checkout and confirm that checkout's branch carries no frontend changes of its own before
quoting a number.

**The sidecar's editable install points at the main checkout.** Running `pytest EEGResearch/tests`
from a worktree without `PYTHONPATH` set **silently tests the main checkout's code** — a green run
that exercised none of your changes:

```bash
PYTHONPATH="$PWD/EEGResearch" EEG_SOURCE=sim API_TOKEN=t ADMIN_TOKEN=a EEGResearch/.venv/Scripts/python.exe -m pytest EEGResearch/tests -q
```

**Mutation checks: restore from a copy, never `git checkout --`.** A `git checkout` restore inside a
mutation helper has wiped uncommitted edits here before. The working shape is `cp file /tmp/orig`,
mutate, run, `cp /tmp/orig file`, and assert the mutation actually applied — a `sed` that matched
nothing reports a green suite that proves nothing.

**Never run a controlled comparison in a tree you do not control.** If another session is editing the
checkout, an A/B across two runs is not a comparison: the other variable moves between them. Use a
worktree at a known commit. This is the canary rule's dirty-tree clause, and it costs whole rounds
when ignored.

**Editing Python or JSX from bash heredocs is fragile.** Quoting inside a `python - <<'EOF'` block
inside a compound bash command has failed with `unexpected EOF` more than once. Write the edit script
to the scratchpad and run it by path, or use the Write tool.

**`test_eeg_poller.py`'s first test fails when that file runs alone**, on `main` as well as on a
branch: `main` is first imported by the school-year fixture, which rewires the consent check after the
consent fixture set it. It passes in the full suite, where `main` is imported at collection. Not a
regression; do not chase it.

**Three venvs.** `EEGResearch/.venv` runs the sidecar, `Website/AdaptiveLearning/backend/.venv` runs
the backend app, and the repo-root `.venv` is what `pytest` uses for the backend suite. A package in
one says nothing about the others.

**The working directory shifts between tool calls.** `cd` inside a compound command moves it for
later calls; use absolute paths.
