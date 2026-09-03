# Handoff

Written 2026-09-02. A snapshot, not a standing document — **`CLAUDE.md` is the
thing that is kept current**, and anything here that turns out to be durable
belongs there instead. Read `CLAUDE.md` first; this file only covers what is in
flight and what is not written down anywhere else.

---

## 1. Where things stand

**Nothing is in flight.** `main` is at `dfb9636`, no open PRs, working tree
clean. Everything this file described as pending has merged:

| PR | merged | what |
| --- | --- | --- |
| #165 | 2026-09-02 | question goal, session lifecycle, and the first grade 9-12 content |
| #166 | 2026-09-02 | time the solver's startup and its arithmetic separately (§2b item 5) |
| #167 | 2026-09-03 | dedupe keys for `cognitive_signals` and `face_signals` (§2b item 6) |
| #168 | 2026-09-03 | `spread` (S-ID.2), the third grade 9-12 topic (§6 item 4) |

**Three commits landed directly on `main` without review** — `cee8cff` (this
file), `9e76156` (the generation load test) and `6d625d7` (the waiter cap and
`Retry-After`). That was a mistake of process, not of content: they are green
and were gone through in conversation, but they should have been a branch and
a PR. Left in place rather than rewound, because `headband-connection-recovery`
was already pushed on top of them and unwinding would have rewritten a branch
that had left this machine. Worth knowing if the history reads oddly.

Both of #168's production steps are complete: `20260915000000` applied via the
Supabase integration (confirmed in Remote), and
`supabase/seeds/lesson_plans_spread_topic.sql` was pasted into the dashboard
SQL editor. Neither the seed nor its siblings is a migration, so a rebuilt
production needs all four seed files again — see §2a.

<details>
<summary>What #165 contained, kept for the commit-level detail</summary>

Nine commits:

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

Merged just before that branch, for context: **#159–#164** — scenario grade
gating, structured outputs, four new grade 1-3 topics, and the figure pipeline.

</details>

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
| 1 | ~~**Grades 9-12 have no content of their own.**~~ **Done, as far as it can go without new solvers.** `quadratics` (A-REI.4b), `functions` (F-IF.2, F-BF.1c) and `spread` (S-ID.2), all on exact integer solvers in `hs_solvers.py`. Grade 9 went 81% → 56% three-or-more-grades-below. | `systems` was on this list and **does not qualify**: a 2×2 linear system is 8.EE.8, so it would be another grade-8-ceiling topic wearing a grade-9 label. **Grades 10-12 cannot be moved by another topic at this level** — the scoring ceiling is grade 9, so the rate reaches 100% at grade 12 by construction (§6 item 1). The next real lever is a solver for content *above* grade 9, and there is no candidate yet that this system can score exactly. |
| 2 | ~~**The spend posture has never been exercised end to end.**~~ **Exercised 2026-09-03** with `scripts/load_test_generation.py` — a real uvicorn, a real class, a faked network peer so nothing is billed. Every bound held. **What it found is a UX result, not a bounds bug:** on a *simultaneous* start, 30 students at the shipped `GENERATION_MAX_WAITERS=12` are served **40%** — 18 get a 503 that no client retries. Staggered over 10s it is 87%, over 30s 100%. | Open decision, not open work: raise the cap (20 → 67%, 30 → 100%, no starvation at either, no extra model calls), or make the client honour the `Retry-After: 5` the backend already sends, which costs no threads. The daily ceiling is still unexercised — reaching it needs 2500 calls, which the harness could do but which measures arithmetic rather than behaviour. |
| 3 | **`attention` still has no producer** (Phase 11 step 3). | Blocked on a *labelled reference*, not on code. Every surface that rendered it was removed in #86; put the UI back in the same change that fills the column, not before. |
| 4 | **Camera rPPG accuracy.** The ONNX export works and the cost objection is gone (22 MB, 1.5 s load vs ~34 s). | Still needs the video + ECG capture. `scripts/capture_face_video_ecg.py` is that capture. The POS rejection stands. |
| 5 | ~~**`_solve_worker` pays sympy startup inside `SOLVE_TIMEOUT`.**~~ **Done.** The worker signals readiness once sympy is loaded and `_run` times startup and the solve separately. Under CPU saturation: **7/8 where it was 0/8**. 2× oversubscription is **not** fixed (1/8, was 0/8) — the machine cannot start interpreters fast enough, and no budget arrangement changes that. The trade is the tail: a contended startup now holds a slot ~30s where it used to fail in 3. See CLAUDE.md for what the two budgets now mean — they are tuned in opposite directions. |
| 6 | ~~**Database efficiency**~~ **Closed as a decision, not abandoned.** Everything concrete has shipped; **partitioning is deliberately not being done yet**, and the trigger for revisiting is in section 7. | Shipped: the `cognitive_signals(user_id, ts DESC)` composite in `20260721000000`, `session_answers(session_id, answered_at DESC)` in `20260905010000`, the redundant-index drops in `20260905020000` (four, not two), the `face_signals`/`heart_signals` composites in `20260913000000`, and the dedupe keys in `20260914000000`. |

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

## 6. Left open by the grade 9-12 work

1. **The audit was re-run for grades 9-12** (2026-09-02, Claude Haiku 4.5, 64
   questions, `scripts/audit_grade_appropriateness.py`). Grade 9 went from
   **81% to 56%** three-or-more grades below grade. The rest is the finding:

   | grade | ≥3 below | at grade |
   | --- | --- | --- |
   | 9 | 56% | 2 of 16 |
   | 10 | 69% | 3 of 16 |
   | 11 | 81% | 3 of 16 |
   | 12 | **100%** | 0 of 16 |

   **Grade 12 is not a sampling result, it is arithmetic.** The highest
   concept anything here can *score* is grade 9, so every question this
   system can ask a 12th grader is three or more grades below by
   construction — the two new topics included. The rate climbs one grade at a
   time and reaches 100% at grade 12 whatever else is added beneath that
   ceiling. Adding topics at grade 9 cannot move grades 11-12; only a solver
   for content above grade 9 can.

   Caveats worth carrying: 16 questions per grade against the original's ~71,
   topics drawn uniformly from `_allowed_topics` (what `randomize_selection`
   does) rather than by the live weighted path, and the classification is a
   judgement about which CCSS standard each question exercises.
2. ~~**Whether the grade-8-ceiling topics should be capped is undecided.**~~
   **Decided 2026-09-02: no.** Not a judgement call in the end — the highest
   concept anything here can score is grade 9, so a cap keyed to the
   student's grade leaves grade 9 with two topics and **grades 10-12 with
   zero**. `_safe_topic` calls `random.choice` on that list, so empty is an
   `IndexError`: a 500 on every question at that grade. Capping cannot fix a
   ceiling. The metric also over-reads — it counts where a concept is
   *introduced*, and S-ID.2 has high schoolers using mean and median. See
   CLAUDE.md and `test_the_grade_eight_topics_are_knowingly_uncapped`.
3. **Both topics now hand the mathematics to the model rather than asking for
   it, and both needed to.** `quadratics` was measured producing a usable
   equation 0 of 3, 2 of 3 and 1 of 4 times across three promptings.
   `functions` looked fine until the lesson plan was injected, and then
   `compose` failed **3 of 3** on llama3.1:8b — every one `text does not
   contain 'g(x) = x + 2'`, the model returning coefficients and writing them
   differently in the prose. That is two of three tiers, since `compose` is
   medium *and* hard. Its root cause was a dangling prompt reference: `_FOOTER`
   told the model its text must match a `FUNCTIONS AS THEY MUST APPEAR`
   section that was never emitted. Both are now measured 3/3 on Ollama and
   6/6 on Claude with the seeds injected, zero retries.
4. ~~**`spread` (S-ID.2) is the obvious third topic and is not built.**~~
   **Built.** The rounding blocker dissolved the way quadratics' did: the
   data is constructed so the population variance is a perfect square, so the
   answer is an integer and nothing is rounded. The larger hazard turned out
   to be one the entry did not mention — sample vs population standard
   deviation, which differ, so the question must say "population" and the
   generator refuses a text that does not. 6/6 on both providers, plus 2/2
   with the lesson plan injected.
5. ~~**Neither lesson plan is seeded on production.**~~ Done —
   `supabase/seeds/lesson_plans_hs_topics.sql` 2026-09-02 and
   `lesson_plans_spread_topic.sql` 2026-09-03, both pasted into the dashboard
   SQL editor. Not migrations, so a rebuilt production needs them again.
6. **`spread`'s shown-versus-scored check took four review rounds, and the
   shape of that is worth more than the fix.** Each round closed the reported
   hole and moved the failure one layer along: containment accepted an
   appended value and swapped labels → binding runs and labels left the *ask*
   unbound → binding the ask over the whole text refused legitimate context →
   scoping to the interrogative clause let the data's own labels decide
   direction, then missed an ask phrased as an instruction.

   That is one design mistake showing its shape, not four bugs. The check
   reads a natural-language ask the model wrote freely, so every fix narrows
   *where* it reads without changing *that* it reads prose. The invariants
   that never needed a correction are the ones where the generator supplies
   the artefact and demands it back verbatim — the data, the labels,
   `quadratics`' equations.

   **If it needs a fifth fix, narrow it no further — supply the question stem
   too**, and let the model write only the context around it. That takes the
   check out of the loop rather than moving it. Not done now because both
   providers measure 1.00 model calls per question and the remaining holes
   were reachable only by phrasings no observed reply used; doing it on that
   evidence would be a rewrite in search of a symptom.

## 7. Why partitioning is not being done

Measured 2026-09-02 against production:

| table | rows |
| --- | --- |
| `cognitive_signals` | 3,691 |
| `face_signals` | 961 |

That is less than a quarter of one student-hour of EEG, ever. Partitioning is
a technique for tables where bulk deletion or scan cost is a real problem, and
at four thousand rows neither is. It would also cost something: a partition
has to be created ahead of time by a job, foreign keys into partitioned tables
are restricted, and **every unique index must contain the partition key** --
which the three dedupe keys happen to satisfy already (`ts` is in all of
them), but which constrains every key added later.

**The trigger is deployment scale, not code.** At 4 Hz, one student-hour is
~14,000 cognitive rows. A class of thirty using headbands five hours a week
reaches tens of millions of rows within a school year, and at that size the
year-end `expire_signal_rows` delete is the thing that hurts first --
`p_batch_size`, `p_max_batches` and `hit_batch_cap` all exist because someone
already worried about that delete's cost, and `DROP PARTITION` is the answer
partitioning actually gives.

So: revisit when the signal tables pass roughly a million rows, or when the
first real pilot with headbands is scheduled -- whichever comes first. Re-run
the counts before deciding; that is two minutes and this entry is a snapshot.

Not a backlog item until then. An open item nobody can act on reads the same
as one nobody has got to, and this one has an answer.

~~A long planning document from an earlier session lives at
`create-the-plan-to-delegated-prism.md` ... section 6 of it is the origin of
item 6.~~ **That pointer was wrong.** That file is the Claude-migration plan,
marked SHIPPED, and has no database section -- no "database efficiency", no
"database security", and the word "partition" appears in no plan file at all.
Checked 2026-09-02. Item 6 carries its own reasoning now instead of pointing
somewhere.
