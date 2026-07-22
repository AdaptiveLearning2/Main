# Manual QA checklist

Things to confirm **in the running application** that the automated suites cannot cover.

Covers every merged PR through #25 (`10ae0aa`). Written 2026-07-22.

## Why this exists

The test suites are real but narrow, and it is worth knowing exactly where they stop:

| Suite | Count | Blind spot |
|---|---|---|
| `Website/AdaptiveLearning/backend` | 30 | Runs against a **fake Supabase**. No RLS, no real policies, no service-role behaviour. |
| `EEGResearch` | 60 | Runs with `EEG_SOURCE=sim`. **No hardware** — nothing about real electrode contact is exercised. |
| `frontend` | 13 | Covers **two components** (`SignalPanel`, `ClassDetail`) out of 38. |
| `native-bridge-build` | — | Compiles with `ENABLE_LIBMUSE=OFF`, so **none** of the libMuse packet handling is built, let alone run. |

So: access control is tested against a database that does not enforce anything, and EEG contact handling is tested against a simulator that always reports perfect contact. Those two areas carry the most unverified risk, which is why they are sections 2 and 6.

---

## 1. Smoke — the stack starts (#1, #23, #25)

> **Read this before anything else.** As of #25 the EEG service has **no default
> tokens**. `API_TOKEN` and `ADMIN_TOKEN` are required and the service will not
> start without them. Any machine whose `EEGResearch/.env` predates #25 — including
> a second machine of your own, and every teammate's — will fail to start until
> those are set. This is intended (the old defaults were `learner-token-123` /
> `admin-token-123`, committed in a git repo and therefore known to anyone who had
> seen the code), but it *will* look like a broken build to whoever hits it first.

- [ ] `EEGResearch/.env` has `API_TOKEN` and `ADMIN_TOKEN` set to real generated values
- [ ] `Website/AdaptiveLearning/backend/.env` has `EEG_API_TOKEN` / `EEG_ADMIN_TOKEN` **matching** the two above — they are the same secrets, and the backend authenticates to the sidecar as a client
- [ ] `.\start.ps1` completes with no Python version mismatch and no `pydantic_core` error
- [ ] Four services reachable: frontend `:5173`, website backend `:8000`, EEGResearch `:8001`, bridge `:8765`
- [ ] `.\start.ps1 -muse` reaches the native bridge without hanging
- [ ] A fresh clone works: `.venv` is no longer committed (#23), so the venv must build from `requirements.txt` on a machine that has never run this repo

Deliberate failure modes worth confirming once, so they are recognised rather than debugged:

- [ ] **Unset `API_TOKEN` and start EEGResearch** → fails immediately with a validation error naming the field, not a confusing runtime 401 later
- [ ] **Unset `EEG_API_TOKEN` and start the website backend** → still boots (it treats the sidecar as optional), but any EEG call raises `Missing EEG_API_TOKEN environment variable` rather than silently reporting the sidecar as unavailable
- [ ] **Run a script from `EEGResearch/scripts/` with no tokens set** → throws a message naming the variable to set, rather than failing with a 401

The middle one is the subtle case: a missing token is a **permanent** misconfiguration,
whereas a stopped sidecar is transient. They used to be indistinguishable — both
surfaced as "EEG unavailable" — so the config error could hide indefinitely.

## 2. Access control — **needs two teacher accounts** (#15, #9)

> Do this section first. Six endpoints originally shipped with no ownership check
> at all. `test_access_control.py` guards the logic, but it substitutes a fake
> Supabase client, so the real policy layer underneath has never been exercised
> by a test.

Sign in as **Teacher A**, and attempt to reach data belonging to **Teacher B**.

- [ ] `/teacher/classes/{B's class id}` → 403 / error, **not** a visible roster
- [ ] `/teacher/live` → B's class is not selectable, and B's students do not appear
- [ ] `/teacher/sessions/{B's student's session id}` → 403
- [ ] A **parent** can open their linked child, and **cannot** open an unlinked child
- [ ] A **student** can open their own data and nobody else's
- [ ] Teacher's **Students** and **Classes** tabs actually populate

The last one is the inverse failure. The `#9` RLS policy exists so a teacher can read the
profile rows of students in their classes; if it is wrong, these lists come back
**empty rather than erroring**, which looks like "no students" rather than a bug.

## 3. Teacher UI (#5, #6, #16)

- [ ] `/teacher/classes` lists classes; creating a class works; the join code displays
- [ ] Copy button on a class **with no join code** → error toast, does not copy the string `"undefined"`
- [ ] `/teacher/classes/{id}` with **zero students** → empty state, no crash
- [ ] A class with an **empty name** → renders "Untitled class", no crash
- [ ] **Stop the backend, then load a class page** → "Couldn't load this class" plus a Try again button
- [ ] Same page while logged in as a non-owner → **not** the words "Class not found"

The last two are one bug: a failed request and a genuinely missing class used to render
identically, sending a teacher to look for the wrong problem.

## 4. Student / adaptive flow (#13, #4)

- [ ] `/adaptive` serves questions without stalling between them
- [ ] Questions respect the student's **grade level** and the difficulty setting
- [ ] With `EEG_SOURCE=sim`, focus / calm / engagement are **non-zero and changing** between ticks
- [ ] Difficulty visibly adapts across a session

If the sim scores sit pinned at the extremes (0 / 100) or barely move, the band-power
unit fix from #19 is not taking effect — the simulator must emit **Bels**, not linear
magnitudes.

## 5. Parent reporting — **needs a linked parent + a child with session history** (#22)

- [ ] `/parent` tiles show **percentages**: focus reads `72%`, never `1%` and never `0.72%`
- [ ] `/parent/child/{id}` weekly report renders all five averages and both highlights
- [ ] The chart draws **three distinct coloured lines**, not flat along the axis floor
- [ ] Summary sentence reads `average focus was 72%`
- [ ] A child with **no data** shows `N/A` and empty states, never `0%`
- [ ] **Stop the backend, reload child detail** → an error message, not a silent "no data"
- [ ] Dashboard loads in reasonable time with several linked children

Worth checking even though `SignalPanel.test.jsx` covers the scaling: the test asserts
against a **fixture**, not against what the pipeline actually writes to
`cognitive_signals`. This confirms the wire format really is `0..1`.

## 6. EEG hardware — **needs the Muse headband** (#8, #18, #19, #20, #7)

> Nothing in this section is covered by any automated test. The EEGResearch suite
> runs on the simulator, which reports perfect contact on all four electrodes, and
> the CI bridge build compiles the libMuse paths **out**.

- [ ] Bluetooth **switched off** → the app reports radio state rather than silently finding no device (#8)
- [ ] Headband connects; band values are non-zero
- [ ] Connection quality reads **good** when correctly seated — not permanently "poor" (#18)
- [ ] **Lift one ear electrode** → quality degrades, scores keep working from the remaining electrodes (#20)
- [ ] **Lift all electrodes** → measurements go blank and the UI shows `—`, never `0` (#18, #22)
- [ ] Focus / calm sit mid-range for an engaged learner, not pinned (#19)
- [ ] Debug panel scores agree with the dashboard (#7)
- [ ] Start / stop a session repeatedly → no stale "live" state after disconnect (#7 TOCTOU)
- [ ] A session recorded with poor contact still appears in history, with blank measurements rather than zeros

### Known physical issue

The Muse S ear electrodes (TP9 / TP10) lose contact within roughly two minutes of
wear. Use **saline** rather than water, and re-wet mid-session to test whether
quality recovers. Until this is stable, treat any contact-quality observation
longer than ~2 minutes as confounded.

---

## Not verifiable here

These merged PRs have no user-facing surface: **#17** (test fix), **#21** (CI),
**#23** (venv untracking), **#24** (test framework). Confirmed by CI, not by clicking.

**#25** is a partial exception. Its auth changes are covered by tests (60 in
EEGResearch, including a regression test that sends a raw non-ASCII header and
asserts 401 — that specific case is unreachable from any Python client, since
httpx refuses to send non-ASCII header values). What tests cannot cover is the
*configuration* consequence, which is why section 1 grew rather than this list.

## Calibration caveat

Absolute focus / calm percentages are **not yet validated against ground truth**. The
bounds in `signal_processing.py` are physiology-informed heuristics, and the per-session
baseline added in #20 assumes the opening ~15 seconds is rest — a learner who starts
already engaged makes that engagement their zero point. Relative trends within a session
are meaningful; absolute numbers are not, pending a multi-subject baseline capture.
