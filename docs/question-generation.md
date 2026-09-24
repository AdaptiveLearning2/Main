# Question generation

Part of the AdaptiveLearning conventions; `CLAUDE.md` is the root and holds what binds here as much
as anywhere — the canary, the four numbered rules this file cites, and fusion asymmetry, which the
difficulty bias below is an instance of. Read those first.

**Read this file when you are touching** a `LLM_*_generation.py` generator or its prompt,
`LLM_topic_decider`, `llm_client` or either provider, a schema in `question_schemas.py`, a solver
(`safe_solve`, `geometry_solvers`, `angle_solvers`, `hs_solvers`), grade or topic gating, a lesson
plan seed, question figures or CCSS codes, or the difficulty bias on `profiles`.

## Learning preferences live on `profiles`, and difficulty is a bias

Three columns: `difficulty_bias`, `session_duration_minutes`, `practice_reminders`. They were
`localStorage.al_prefs`, written by the Preferences tab and read by nothing — the backend picks the difficulty and
cannot see a key in one browser's storage.

**`difficulty_bias` is a shift, never an absolute difficulty**, and that is a safety property rather than a
simplification. `_shift_difficulty` applies it on top of what the model chose from the student's accuracy history,
and `LLM_topic_decider` overrides it *downward* whenever the fused signal says stressed — the same asymmetry
`signal_fusion` documents. Storing "always hard" would store a value the ease-off rule has to contradict, and a
setting the system routinely ignores is worse than one that does not exist. It is why the control offers three
options and not four: medium and adaptive would both mean no shift.

**Bounds are stated twice on purpose** — Pydantic on `UpdateProfileRequest` and a CHECK in the migration — and they
must agree, or a value that passes one and fails the other surfaces as a 500 from the client library instead of a
422 naming the field. The CHECK is a **range**, not the four durations the UI offers, so a fifth button is not a
migration.

**Duration is advisory.** The page asks between questions; nothing ends on a timer. A session closed mid-question
discards an answer a child was part way through giving. **And its clock starts at the first question, not at
Connect.** Under pull, `toggleHeadband` creates the session before anything has been asked, so a clock keyed on
`sessionId` charged the 12 s scan, seating the electrodes and every reconnect against the student's planned
duration. `fetchQuestion` starts it, beside `armRecording` and for the same reason — a paired headband is not a
lesson. Push never had it, since `toggleHeadband` skips session creation there. The test costs two 22 s waits and
they are not padding: the reminder is checked on a 20 s interval and the tick the clock *starts* on reads ~0
elapsed, so a short settle passes against the bug. `Adaptive.jsx` now has a `finishSession` — before this it never
called `/end` at all, so an adaptive session stayed open until the stale sweep on the student's *next* start.

**`practice_reminders` is a dashboard banner and is named for that.** There is no push infrastructure — no service
worker, no VAPID, no scheduled fan-out — so "Notifications: daily reminders to practice" described a system that
does not exist. The banner needs *both* reads to have landed before it renders: derived from a failed
`/api/sessions`, it tells a child they skipped a day they did not skip. Its "today" is the **browser's local day**,
deliberately not `_school_day` — that helper buckets recorded data against the school's timezone, and this is a
nudge about the student's own afternoon.

### A run of correct answers pushes difficulty up on its own

`_decide_bias` in `LLM_topic_decider`: at least `PERFORMANCE_PUSH_MIN_ANSWERS` (3) of the session's last ten answers
at `PERFORMANCE_PUSH_ACCURACY` (70%) or better shifts up — **and the newest `PERFORMANCE_PUSH_RECENT_CORRECT` (2)
must be right**, because the aggregate cannot tell a rising student from a falling one: 7 of 10 is 0.7 whether the
misses were the first three or the last three, and pushing a child who has just failed three in a row is the harm
the asymmetry exists to prevent. `get_session_performance` keeps the order as `recent` (newest first) for that — its
`desc=True` is what makes `recent[:2]` the newest two, and the fake in its test sorts by the flag so that direction
is pinned. A caller without it gets no push.

Also required: the control on Auto, the fused label not `stressed`, and no channel having withheld an increase
(`FusedState.increase_withheld`, the facial veto, carried on every fused state past the ease-off step). It used to
need a `focused` reading at the moment of choosing, and on hardware that is a state a student cannot hold: five
correct answers at grade 1 stayed on easy throughout, because every decision landed on `stressed` (a loose strap) or
`neutral`.

**A run of misses vetoes a push from either source.** Correctness is the one channel here with no quality gate, so
three straight misses is a trusted opinion that the student is falling, and every channel with an opinion must agree
to raise; a focused reading over that run is the false-focused case the asymmetry exists for. No answers yet is no
opinion, and focused pushes. The asymmetry is untouched — stressed still eases whatever the answers say, and a manual
Easier/Harder still wins — and `test_decide_bias.py` brute-forces it.

**`start_session` prewarms at the student's bias, not 0.** `QUEUE_SIZE` questions are generated before the first
answer and served first, so a hardcoded default there makes the setting do nothing for the opening of every session.

### A practice test's length is a prop, and flashcards have none

`PracticeSetup` offers 5/10/15/20 and hands the number to `Practice` through `onStart(session, count)`, which passes
it to `PracticeTest` as `questionCount` (default 10, the value it was a module constant at). **Nothing is sent to
the backend** — generation is one question per request, so the count is only ever a client-side stopping rule.

Two differences from Adaptive's question goal, both deliberate. There is **no "No limit"**: Adaptive's number raises
a dismissable banner beside a Finish button, and a test has no manual-finish affordance, so it must always auto-end.
And the picker is **hidden in flashcard mode** — a deck ends on "Done", at any point, so a count there would name a
limit that does not exist.

## Every model call goes through `llm_client`, and the provider is a setting

`backend/llm_client.py` is the only place either provider is reached. Fourteen call sites used to import `ollama`
directly, so a provider switch was fourteen edits and the bounds below had nowhere to live. Count the `generate_text(`
sites rather than trusting a number here — today it is the seventeen generators, the decider and the strategies pass.
Two of the decider's belonged to `parallel_topic_and_difficulty_calculation`, which spent *two* model calls on what the
live path does in one and was reachable from nothing; deleted with its sole caller, since dead code that bills twice
per question is a trap for whoever wires it up next.

**A question served costs exactly two model calls**: the topic-and-difficulty decision, and the generation. Each has
its own three-attempt loop, so six is the worst case — but the decider's loop is *around its own call only*, so a
retried decision never re-runs a generation. `CLAUDE_MAX_RETRIES` defaults to **0**, against the SDK's 2, so nothing
multiplies underneath: the call sites' loop is the one worth keeping, since it also rejects a *well-formed* response
for being bad JSON or the wrong shape.

**`LLM_PROVIDER` defaults to `ollama`.** A fresh checkout must not begin billing an Anthropic account; a deployment
opts in with `LLM_PROVIDER=claude` and `ANTHROPIC_API_KEY`. Neither package is imported until its branch is taken.
**`start.ps1` skips Ollama when `backend/.env` says `claude`** — it ran unconditionally, so a Claude deployment still
started `ollama serve` and would pull a multi-gigabyte model nothing calls.

**`temperature` is not a parameter of `messages.create` in `anthropic` 1.x, so the Claude branch sends no sampling
parameter at all.** It went with the 0.x → 1.x major version along with `top_p` and `top_k`; passing it raises
`TypeError` *before a request is built* — every question, from the first call. The API's own default is 1.0, exactly
what `CLAUDE_TEMPERATURE` defaults to, so the hot path asks for nothing and cannot be refused for asking. A caller
wanting something else goes through `_claude_sampling`, which puts it in `extra_body`. **That path is unverified** and
degrades safely, but a strategies response reading `source: "rule-based"` against a working key is the first place to
look.

**A test double must not be more permissive than the thing it stands in for.** This survived every review and a green
suite because `_FakeMessages.create` took `**kwargs`, so `test_llm_client.py` pinned a request shape the SDK cannot
accept and the one assertion anyone would have trusted could not fail. It now validates every kwarg against
`inspect.signature` of the **installed** SDK, read at call time rather than copied into a list that ages.

Verification stops at the network boundary without credits, and that boundary is worth reaching: **a `400` carrying a
`request_id` proves the request was built, sent and validated — a `TypeError` proves it never left the process.**

### The four bounds

| Bound | Setting | Why the existing one was not it |
| --- | --- | --- |
| Per-call deadline | `GENERATION_LLM_TIMEOUT` (30 s) | The SDK's default is **ten minutes**; a prefetch worker blocked that long never refills the queue |
| Concurrency | `GENERATION_MAX_CONCURRENCY` (8) | `_prefetch_active` bounds *per user*, so the peak was however many children pressed start at once |
| Per-student volume | `GENERATION_RATE_LIMIT` / `_WINDOW` (60/min) | The queue bounds calls *in flight*, not calls *over time* |
| Waiting callers | `GENERATION_MAX_WAITERS` (30) | The fourth bound, and it was missing |
| Spend | `GENERATION_DAILY_CALL_LIMIT` (2500/24 h, Claude only) | Nothing bounded it; free against a local model |

**The spend ceiling counts calls, and a question served is two of them.** It was 5000, justified as "eightfold
headroom" on ~600 generations a day — comparing a call ceiling against a question count and overstating it by two.
**Size it against the workload, not against whichever example is written down**, and against the worst case:
`max_tokens=2048` at $5/MTok makes one call cost up to ~$0.0102, so 2500 is ~$25/day where the ~$0.0023-per-question
average suggests ~$2. It bounds neither tokens nor a restarting process (in-memory, so several uvicorn workers
multiply it and a crash-loop defeats it). **Claude-only** on purpose: a ceiling against a free local model would
refuse a child a question to protect nothing.

**`GENERATION_MAX_WAITERS` is the threadpool bound, and it is not the concurrency one.** That one bounds calls *in
flight*; this bounds callers *blocked waiting to become one*, and only the second protects the app:
`_generation_slots.acquire(timeout=…)` blocks in the caller's own thread, and FastAPI runs these sync endpoints on
anyio's shared ~40-slot threadpool — so at a concurrency of 8 a class of thirty starting together puts twenty-two
requests to sleep in threadpool slots and `/api/signals/*` queues behind them. The per-student rate limit does not
help: that counts one student over time, this is thirty at one instant. Latent while the prefetch queue absorbed it;
`QUESTION_QUEUE_SIZE=0` made the inline path the only path. **Both** generation endpoints take it.

Measured against a real server with `scripts/load_test_generation.py`, which runs uvicorn and fires a class at it.
Nothing is billed: only the *network peer* is faked, so the semaphore, the budget arithmetic and every refusal are the
shipped code. Three findings:

- **The waiter cap *subsumes* the concurrency cap, it does not add to it.** `_generation_waiter` wraps the whole call,
  so it bounds requests **in flight**; the semaphore bounds model calls inside that. A check written the obvious way
  asserted 8 + 12.
- **The threadpool is only starved past ~40 in flight.** anyio's default limiter is exactly **40** threads (measured),
  shared with every other sync endpoint. At 30 waiters a probe on `/api/topics` stayed at 31 ms; at 60 in flight its
  **worst** probe was **11.9 s**, at 80, **23.9 s** — while **p50 and p95 stayed under 25 ms in every run**. A
  percentile-only report shows a healthy service that is intermittently hanging for twenty seconds. **Watch the max.**
- **The cost is refusals, and how many depends entirely on arrival.** 30 students, 2 s per call, at the old cap of 12:
  **40% served on a simultaneous start, 87% over 10 s, 100% over 30 s.** A synchronised start is a teacher saying
  "everyone start now", and on it 18 of 30 got a 503. **The cap is now 30**, which serves the whole class and costs no
  extra model calls, only threads and waiting, since a refused student generates nothing. Not 40, because anyio's pool
  is 40 and every other sync endpoint draws from it.

**The 503 carries `Retry-After: 5`, and `apiFetch` honours it** — for **GET only**, so a retry can never replay a side
effect; both generation endpoints are GETs, which makes that free. Bounded at two retries and clamped to 10 s, because
the header is a request from the server and not an instruction. **Jitter is the load-bearing half, not a refinement**:
every browser refused in one burst holds the *same* `Retry-After`, so honouring it exactly reforms the burst one round
later. `jittered()` is full jitter over `[0, delay]` — a tight band around a common centre is still a herd. **A retry
added anywhere else needs the same treatment.**

**Take it through `_generation_waiter()`, never a bare acquire/release pair.** The refusal raises `HTTPException`
*inside* the guarded block, so a hand-written release is skipped on the path most likely to run, and a
`BoundedSemaphore` acquired with `blocking=False` turns leaked permits into generation being off for the life of the
process.

**The budget covers the whole call, so time spent queueing comes out of it** — the model call is charged the
*remainder*, and a caller that queues its budget away is refused rather than started with no deadline left. Charged
twice, one caller blocks for nearly double what it asked for. It belongs here rather than at a call site, because this
is where the queueing happens. Tests on both must assert `<=` the budget, never `==` it.

`_ensure_queue` submits to a pool sized to `GENERATION_MAX_CONCURRENCY` rather than spawning a daemon thread per
question. **A submit that fails must roll the in-flight count back**, because `_prefetch_worker` owns that decrement in
its `finally` and a worker that never starts never runs one — the count would stay raised for the life of the process
and that student's queue would never refill again. Swallowed rather than raised for a separate reason: `_ensure_queue`
runs *after* the response is assembled, so letting a failed refill out turns a served question into a 500.

**`QUESTION_QUEUE_SIZE` defaults to 0 — prefetching is off.** A queued question is billed when *generated* and earns
its cost only when *answered*, so any depth above 0 pays for the unanswered questions of everyone who closes the tab —
precisely the rows `expire_old_questions` collects. Free against Ollama, which is why it was 2 and unconditional
before. The cost of 0 is latency. An env var so a deployment can raise it once the per-question cost and the real
abandonment rate are known, without a deploy.

**A test that reads `QUEUE_SIZE` instead of pinning it goes vacuous at 0**: `assert len(submitted) == main.QUEUE_SIZE`
becomes `0 == 0` after submitting nothing. Any test whose point is prefetch *behaviour* must
`monkeypatch.setattr(main, "QUEUE_SIZE", n)`.

### Refusal, not fallback

**On breach the answer is to refuse — `GenerationUnavailable`, surfaced as 503, never a fallback.** Serving from the
bank or a cheaper model would change what a child is asked with nothing saying so (rule 1). 503 rather than 500
because a ceiling is a decision this deployment made. The prefetch worker is the one place a refusal is *silent*, and
that is safe because it is invisible by construction: the queue stays short and the next question is generated inline.

**An API that cannot be *reached* is a 503, not a 500, and the message names the base URL.** `generate_text` catches
`anthropic.APIConnectionError` (which `APITimeoutError` subclasses) and re-raises it. Unclassified it was a 500 with a
200-line traceback, and the student's page said *"make sure the backend is running"* while the backend was running.
**`AuthenticationError` is deliberately excluded** — a different branch, and a bad key is a misconfiguration that must
stay loud rather than read as a passing outage. That is the line between classifying and swallowing.

The URL is in the message because it is the whole diagnosis when it is wrong: a stale `ANTHROPIC_BASE_URL` in the
*user's Windows environment* gives exactly `WinError 10061`, and took three rounds to find because "Connection error."
names nothing. Inherited at process start, so clearing it needs a new terminal.

### The Claude branch constrains its replies with a schema; the Ollama branch does not

`extract_json` hunts a JSON object out of prose because `llama3.1:8b` wraps replies in fences and preamble — a property
of *that model*, carried across to Claude untouched, so the retry loop went on absorbing malformed JSON from a provider
that can be told not to produce any. `question_schemas.py` is one schema per topic, sent as `output_config`.

**It replaces no code-level check, and that is the whole caveat.** `grade_appropriateness`, `question_consistency`,
`SCENARIO_VARS`, the scenario-grade checks and the bounded solvers all still run. A schema constrains the *shape* of a
reply, never whether the question is solvable, in band, or consistent with the data it will be scored against. It is
enforced by the provider and **only on one branch** — Ollama sends no schema, so a dev run exercises the unschema'd
path. `extract_json` stays for that, and because a reply truncated at `max_tokens` is the one malformed-JSON path left
on Claude.

What it buys beyond fewer retries: `scenario` is pinned to an enum of the **one** scenario selected, so the
wrong-scenario class becomes unrepresentable rather than merely rejected. Geometry's `variables` keys and the scenario
enums are **derived** from `SCENARIO_VARS` and the block tables, never restated — a second copy of a scenario's keys is
how the schema and the solver drift.

**A schema must admit the reply its own prompt shows.** `angles` pinned every scenario's variables to a numeral, and
`algebra_complementary`'s example is `["x + 10", "2x - 20"]`: on Claude every attempt came back as bare numbers with no
`x` to solve for, and failed, billed. Only Claude enforces a schema, so nothing on the dev path could show it.
`test_every_angle_blocks_own_example_is_allowed_by_its_schema_and_solves` reads the examples from the blocks.

**Two JSON Schema keywords are refused by this endpoint, and neither is guessable from the spec.** Both found by
sending a request and reading the 400:

    For 'array' type, 'minItems' values other than 0 or 1 are not supported
    For 'object' type, 'additionalProperties: object' is not supported. Please set it to false

So `angle_solvers.SCENARIO_ARITY` **cannot** be expressed and stays a runtime check, pinned by a test because every
other shape here is constrained and a reader would assume this one is too. Probability's two bag scenarios return
**`None` rather than a schema**: their `items` maps invented category names to counts, and a schema listing every key
*except* the one carrying the data would be accepted, constrain nothing, and read as covered.
`test_no_schema_uses_a_keyword_the_api_refuses` walks every schema for both, because a violation is a 400 on the first
question of that topic. **A generator added without a schema keeps the `extract_json` path silently**, which is why
`test_every_generator_sends_a_schema` is an exhaustiveness check.

**Switching provider does not invalidate the checks below it, but it does invalidate every measured rate.** Those
checks are code and provider-agnostic — that is why the rules were moved out of prompts. Every "measured on
llama3.1:8b" figure below describes the **Ollama** path. Before a deployment runs on Claude, redo the sampling:
**count how often each fail-open check *engages*, not just how often it fires.** A check whose input it can no longer
locate reports a perfect record while doing nothing, and that has already happened here. Label new figures with the
model.

## `grade` reaches a prompt rebuilt from its number, never as the caller wrote it

`grade` is interpolated into **nineteen** prompts — `Student Grade Level = {grade}` in `LLM_topic_decider`, and one
`a {grade} student` line in each generator — and every one of those strings is client-supplied.
`GET /api/generate-question?grade=` is a query parameter with no request model behind it; `PUT /api/profile/me`, the
two class endpoints and `POST /api/practice-sessions/start` all declared it a bare `str | None`. A value carrying a
newline closes the line it sits on and opens an instruction of its own, in a prompt whose whole job is to be
followed.

**Escaping it is the weaker answer and is not what is there.** A grade is not free text: the only thing any consumer
wants from it is the number `grade_levels.grade_number` already reads. So `grade_for_prompt` hands the prompt a label
**rebuilt from that number** — `CANONICAL_GRADE_LABELS`, fourteen fixed strings, plus `UNKNOWN_GRADE_LABEL`.
Nothing the caller wrote survives, so injection is unrepresentable rather than filtered for. **Assert membership of
the closed set, never the absence of a payload**: an absence test passes against a filter that strips one sequence
and misses the next.

**The labels round-trip** (`grade_number(CANONICAL_GRADE_LABELS[n]) == n`), which is why the substitution is
behaviour-preserving — `_allowed_topics`, `grade_band` and every generator's `GRADE_OVERRIDES` key on the number and
never on the string. Break the round-trip and the grade gates move with no other symptom. Two dropdown labels are
relabelled on the way through ("Highschool" → "9th Grade"), safe for the same reason.

**Applied at two chokepoints, not nineteen**: `question_generation` is the sole dispatch point to all seventeen
generators, so sanitising there covers eighteen of the sites and the decider covers its own. That is sound only while
it *is* sole — a test walks the module's AST and fails on a generator called from anywhere else.

**The edge checks (`validated_grade`, on all five entry points) are the second layer and are not what stops an
injection.** They keep an unreadable grade out of the column, off a teacher's class list and off a profile badge. The
first draft was a length cap plus "does it parse", and `"5th Grade\r\nOUTPUT"` cleared both at seventeen characters:
**a cap bounds how much can be said, never whether a second line can be started.** It now refuses control and format
characters and the line and paragraph separators (`Cc`, `Cf`, `Zl`, `Zp`) that `\n` is not the only spelling of.
`test_grade_prompt_injection.py` keeps three short payloads from being simplified away, and one long single-line one
keeps the cap from reading as redundant.

**A backend test must not hold a `main` class object from collection time.** `test_consent_gates_polling` calls
`importlib.reload(main)`, which rebinds every class in the module — so a `@pytest.mark.parametrize` capturing
`main.CreateClassRequest` gets a stale object, and anything keyed on it raises `KeyError` in a full-suite run while
the file passes on its own. Parametrize by **name** and `getattr(main, name)` inside the test.

## The repeat-avoidance history is the fourth prompt input, and the only open one

Four things reach a generation prompt and three are closed: `grade` is rebuilt from its number, `topic` comes from the
seeded `math_topics` vocabulary (`record_topic_attempt` refuses to invent a row, so no caller can create a name), and
`difficulty` is one of three — `practice_sessions` revokes ALL from both client roles, so the stored value cannot be
PATCHed past its validator. A lesson plan is dashboard-authored and clamped.

The fourth is `get_user_history`: the model's own previous `question_text`, replayed so the next question is not a
repeat. Seventeen generators newline-join it and follow it with *"DO NOT generate a question matching any of the
above"*, so a reply carrying a newline put a line of its own in instruction position. `_prompt_safe_history` flattens
and bounds it at the two sites the history is **read** — `question_generation` and the single-prompt decider — not in
each generator, the chokepoint `grade_for_prompt` already argues for.

**It flattens rather than refusing**, the opposite of `validated_grade`: that guards an edge where a bad value is the
caller's and a 422 names the field, while this runs on the *previous* reply, so raising would fail a generation
because of the question before it. A dropped or shortened entry costs at most one repeated question.

**No student can supply this text**, so it is a model-to-itself feedback path rather than an injection route — and
what bites with no attacker at all is the missing bound: ten unbounded strings ahead of our own instructions is cost
on the Claude branch and context pressure on Ollama. `_HISTORY_TEXT_MAX` is a bound with a reason, not a measurement
(nothing measures real question lengths) — treat it like `EMOTION_MIN_CONFIDENCE`.

**The decider's own site is safe by accident, which is why it is flattened too.** It interpolates the *list*
(`Recent Question History = {recent_global}`), so Python's repr escapes a newline to a literal `\n` and its labelled
INPUT block cannot be forged — where `Student Grade Level` is the next line. That holds only because that one site
does not call `join`, and is one edit from not holding.

`test_history_prompt_injection.py` drives ten spellings of a line break, because a filter written for `\n` alone
passes a test that only checks `\n` is gone — nine of the ten survive it.

## Question generation can be grounded in a lesson plan

`lesson_plans` holds curriculum text keyed on `(topic_name, grade_band)`, at the same `early`/`middle`/`upper`/
`advanced` granularity the generators' own `_grade_band()` uses — not per exact grade, since one lesson plan already
covers a band. Public read, like `math_topics`/`questions`; written only via the dashboard, since it is reference
content the backend never mutates.

`lesson_plan_context.append_lesson_context(prompt, topic_name, grade_band)` is the one-line call site wired into
every generator, right after the grade-magnitude block. Cached with the same 30 s TTL and `time.monotonic()` pattern
as `_feature_flags()` (a lesson-plan edit lands within the TTL, not on the next restart), and clamped to 2000 chars
before it reaches a prompt — dashboard-authored text is still bounded like every other prompt input here.

**The Supabase client is created lazily, on first lookup, not at import.** `main.py` imports `LLM_topic_decider`,
which imports the generation modules, which import this one — so eager `create_client()` ran ahead of `main.py`'s own
`RuntimeError` for missing credentials, and a misconfigured deployment saw a bare `KeyError` three imports away
instead of the clear error `main.py` exists to give it.

**A cell has four ways to contribute nothing, and they are named.** `_lookup()` returns `(text, reason)` —
`NO_ROW` / `BLANK_ROW` / `READ_FAILED` / `NO_CREDENTIALS` / `FOUND` — and each logs its own line. All degrade
identically to the difficulty/grade heuristics, which is right (this is prompt grounding, not a consent or access
gate), but they are very different problems: a content gap somebody has to write, a half-finished edit, an outage,
and a misconfigured process. Nothing in generation branches on it, so this is diagnostics, deliberately.
**`READ_FAILED` and `NO_CREDENTIALS` are not cached**, unlike the other three: caching an outage would keep answering
`None` for the full TTL after the database came back, and credentials can be loaded later in a process's life.

**Don't scrape third-party worksheet sites into this table.** Vendors gate real content behind membership and hold
copyright on what isn't; their topic taxonomy for a grade also doesn't line up with this product's topics. Write
original objectives per topic/grade_band instead.

### What a lesson plan may ask for

**A lesson plan must describe question shapes the generator can actually emit, and the limits are tighter than the
grade band.** Objectives are prompt text, so anything they invite the model will attempt — and the solver then scores
it, correctly or not. Read off the code, then confirmed by generating: `algebra` takes `solve(...)[0]` and splits on a
single `=`, so one linear equation with one solution; `probability` has three scenarios (one named category, its
complement, a die condition) and no compound or conditional events; `rationals` is `a/b` fractions with mixed numbers
forbidden; `mean`/`median`/`mode` are a listed dataset and one statistic — no box plots, no MAD, no comparing
distributions; `angle_relationships` is two angles in one stated relationship, with no diagram to refer to. So at
these bands **`advanced` means harder numbers and one more reasoning step inside the same question shape, not
different mathematics.**

**Three wrong-answer bugs came from seed text alone, all found by reading generated output and none catchable by
`grade_appropriateness`** (llama3.1:8b). "Counted from a described condition" produced *"either blue or yellow"* — a
compound event — scored **1 against a true 10/21**. "Recognise that a dataset may have no mode at all" is true of the
subject and wrong as an instruction: nine distinct readings, **no mode, answer 0**. And a percentage framing scored
**1**, because percentages give the solver no counts to divide. Each is now forbidden in the objectives *and* in the
row's `notes`. **An objective that is pedagogically true can still be an instruction the solver cannot score** —
check what a cell generates before trusting it, not just what it says.

**`notes` is prompt text, not a margin note.** `_lookup` appends it to `objectives` and sends the pair, and the
2000-char clamp covers both — so a `notes` field written as documentation for the next editor is documentation the
model reads, and repo-internal references are noise inside a prompt. Keep it to constraints on the question; the
reasoning aimed at a person goes in the seed file's `--` comments, which are sent nowhere.

**Five seed files, 34 rows** (count the INSERT tuples rather than trusting this):
`supabase/seeds/lesson_plans_priority_topics.sql` covers `ordering`, `geometry` and `expressions` across all four
bands; `lesson_plans_remaining_topics.sql` covers seven topics at `upper`/`advanced` only, since `_allowed_topics`
already keeps them out of grades 1–5 and an unseeded cell fails open to the heuristics;
`lesson_plans_young_topics.sql` covers the four young topics in **five rows, not sixteen**, because `TOPIC_MAX_GRADE`
makes most of that grid unreachable — and `patterns`' `middle` row is written for grades 4–5 rather than the band
ceiling of 6, since **a capped topic's band text is not the band's text**; `lesson_plans_hs_topics.sql` and
`lesson_plans_spread_topic.sql` cover the grade-9 topics. All five are dashboard-run scripts rather than migrations,
because a migration would re-apply their text over any later dashboard edit on every rebuild.
## Grade appropriateness is code-enforced twice: which topic, and what that topic asks

Both layers were prompt hints once, and both leaked. `randomize_selection()` — the fallback whenever an LLM call
fails to parse — picked uniformly across all topics with **no grade parameter at all**, which is how a 1st grader
landed on algebra.

### `_allowed_topics(grade)` is the single source of truth, keyed per topic

`TOPIC_MIN_GRADE` has **one entry per topic**, not grade brackets. A bracket has to be *remembered* for every topic it
should exclude, and two were not: `angle_relationships` was allowed from grade 4 against **7.G.5** (30 of 30 above
grade) and `probability` from grade 6 against **7.SP.5** (10 of 10). Neither is reachable by prompt tuning — the topic
arrives before the concept, so no version of the question is grade-appropriate.

**The generalisation, needed three times now — topics inside a grade bracket, scenarios inside a topic, scenarios
inside a band: a per-bucket minimum cannot describe an item that arrives after the bucket it belongs to. Whenever a
gate is one number for a group, ask which member of the group arrives last.** A per-item minimum cannot omit an item,
and one added without a minimum fails a test rather than defaulting to available everywhere.

**`TOPIC_MAX_GRADE` is the answer to `TOPIC_MIN_GRADE` being a floor with no ceiling.** "8 + ? = 11" is 1.OA.8 and
does not become a grade-9 question by using bigger numbers; past grade 3 that skill is `algebra` with proper
notation. Without a ceiling the difficulty tiers would rank it as somebody's "easy". Deliberately **not** applied to
the original ten, which all scale. It repeals a property a test used to assert — that topics only ever accumulate
with grade — and `test_each_topic_is_offered_over_exactly_the_grades_it_declares` replaces it with what that test was
protecting: availability follows the declared tables and is contiguous.

**A grade is read numerically, through `grade_levels`, and an unreadable one counts as the youngest.**
`profiles.grade_level` is free text. Every grade rule used to match exact strings and fall through to its *most
permissive* branch, so `"Grade 1"` missed every branch of `_allowed_topics` **and** of `_grade_band` — algebra and
`advanced` content to a 6-year-old. Both halves matter: fixing the topic gate alone still leaves advanced material
reaching a child. `grade_number` parses a digit or a named label, rejects anything outside 0–13 so `"2026 cohort"`
cannot become grade 2026, and answers `None` when it cannot tell, which every caller treats as the youngest.
`_grade_band` in every generation file delegates to it.

`_safe_topic(topic, grade)` checks the LLM's own selection against the same table and `randomize_selection()` draws
from it, so there is no third way a topic reaches `question_generation()`. **The decider's prompt lists that table
too, never a list of its own**: one goes stale as topics are added, and a pick outside the grade is replaced at random,
discarding the choice made from the student's performance. Grade 0 (kindergarten) is served grade 1's topics, since
none starts earlier and an empty list has nothing to draw from.

### Difficulty and grade are one table, not two independent scales

`DIFFICULTY_COMPLEXITY[difficulty]` described the question's *structure* and `GRADE_COMPLEXITY[grade_band]` only
scaled a number's magnitude on top, so "easy" meant "one-step equation with x" at every grade.
`COMPLEXITY_BY_GRADE[grade_band][difficulty]` replaces both with one self-contained instruction per cell, "early"
grounded in grades 1–3 arithmetic rather than smaller versions of the same structure.

**Eleven of the seventeen topics use it, and the six that do not are not an unfinished migration.** In `geometry`,
`angle_relationships` and `probability` difficulty already selects a *scenario*, so a second table would state the
difficulty rule twice in two places that can disagree; they keep `GRADE_COMPLEXITY[band]` for magnitude alone, with
grade gating their scenarios through `_pick_scenario`. `quadratics`, `functions` and `spread` choose difficulty in
coefficients in code.

**`GRADE_OVERRIDES` is a per-grade line appended to the prompt**, deliberately not folded into the band-keyed table —
a thirteenth column for one rule would make every other topic's table wrong by omission. Prompt-level, so it can leak;
`grade_appropriateness` is where a code check belongs if it does.

### A gate has two halves, and the second is easy to leave out

**`SCENARIO_MIN_GRADE` decides which prompt block is *sent*; nothing about that constrains what comes *back*.** Both
scenario-gated topics shipped with only the first half, so a `sphere_volume` reply to a grade-4 request (8.G.9) and a
`triangle_sum` reply to a grade-7 one (8.G.5) were each solved and served, walking past the gate written to stop
them. The reply is now checked against the same allowed set inside the retry loop.

That is not defensive: **Haiku returned a scenario other than the one asked for twice in this work**, once with
another scenario's variable keys. Selecting a block is a prompt-level act; only validating the reply is enforcement.

Two scenario leaks were the concrete bugs, both now keyed on `grade_band` rather than `difficulty` alone (a
"hard"-difficulty 1st grader is a real state, since the two are independent inputs): `expressions`' `simplify`
(`2x + 3x`) was picked by unconditional `random.randint(1,3)` at every grade, and `angle_relationships`' scenario 5
(`algebra_complementary`) was gated to "hard" difficulty with no grade check. Both are withheld from `early`/`middle`.
`geometry` gained an `EARLY_BAND_SCENARIOS` filter orthogonal to its `DIFFICULTY_SCENARIOS`.

**That filter was itself too generous, and it filtered one band.** It admitted `triangle_area` (**6.G.1**) to a band
meaning grades 1–3, and fixing that band alone left the larger half: `middle` was unfiltered entirely, offering circle
area (7.G.4), the Pythagorean theorem (8.G.7), and a hard tier of **only** volumes — so a 4th grader on that tier was
always asked a grade-8 question. `SCENARIO_MIN_GRADE` now records the grade each formula is introduced at and
`_pick_scenario` filters **every** band. **Nothing else catches this class** — `grade_appropriateness` looks for
variable notation, and a lesson plan steers what a scenario *asks* rather than which are offered — and both instances
were found by reading generated output. **Check a band's scenarios against the standard they claim to match, not
against whether they look simple**: area of a triangle looks as elementary as area of a rectangle and is three grades
apart.

A per-scenario grade is the pattern in three places. `angle_relationships` sits at grade 7 for 7.G.5 but
`triangle_sum` is **8.G.5**, and its medium tier is *only* that scenario — so every grade-7 student on that tier got a
grade-8 question, 4 of 10 overall. Grade 7's medium tier now falls back to the rest of the topic.

### Difficulty tiers are relative to what a grade can see

A topic used to map a difficulty to a fixed list of scenario numbers — right while every scenario is available, wrong
once a grade filter removes some. Geometry's hard tier is the volumes and the hard ones are 8.G.9, so gating on grade
left grade 6's `medium` inverting a formula while its `hard` multiplied three numbers.

**That is not cosmetic, because difficulty is what the biosignals move.** `signal_fusion` labels a student `focused`,
the decider shifts medium → hard, and at those grades that handed them an *easier* question — the fusion firing
correctly and being undone one layer down. **Anything that narrows what a tier can offer has to be checked against
the tier ordering, not just against the grade rule it was written for.**

`scenario_tiers.pick` ranks the *available* scenarios by `SCENARIO_DIFFICULTY` and slices them into thirds, so `hard`
is the hardest third of whatever remains and cannot invert. Small sets overlap rather than emptying, so
`random.choice` never sees an empty list.

**`SCENARIO_DIFFICULTY` is ordered by steps to solve, deliberately not by the grade that teaches it.** Conflating the
axes reads `algebra_complementary` (set up and solve an equation) as easier than `triangle_sum` (one subtraction). A
CCSS-grade metric reported angles as broken when it was correct.

### A band's tiers are written for its ceiling, so its youngest grade is over-served

`grade_band` buckets 1–3, 4–6, 7–8, 9+, and every tier is written for the top of its band. At grade 4, bottom of a
three-grade band: **66% of questions above grade**, from four mechanisms — geometry gated on the band ceiling rather
than the grade (volume is 5.MD.5), `expressions` middle tiers allowing parentheses (5.OA.1), `rationals` middle tiers
using unlike denominators (5.NF.1), and `mean`/`median`/`mode` offered from grade 4 against 6.SP.5c.

**Where a per-item minimum exists, use the grade; the band ceiling is only a fallback for a grade that cannot be
read.** The next two are `GRADE_OVERRIDES`. Grade 4 went **66% → 43% → 0%**, the last step when
`mean`/`median`/`mode` moved from 4 to 6.

**That step cost breadth, and the cost is the point of recording it**: grades 4–5 now offer five topics where they
offered eight. `test_the_cost_of_that_decision_is_four_topics_for_grades_four_and_five` (whose name predates
`patterns` reaching grade 5) and `test_mean_median_mode_wait_for_the_grade_that_teaches_them` hold both decisions, so
a later widening has to be a choice rather than a drift.

Grades 1–2 had the same shape and were fixed by **adding a scenario rather than removing the topic**: the easiest was
`rectangle_area` (3.MD.7), so a strict reading left those grades no geometry and dropping the topic left them two.
`rectangle_area_by_counting` is **2.G.2**, the one numeric geometry standard below grade 3. It reuses
`solve_rectangle_area` because rows × columns *is* length × width — the difference is entirely in the wording, which
is the scenario block's job, and a second solver would be a copy free to drift.

### Grade 1 had two topics, and now has six

`missing_number` (1.OA.8 through 3.OA.4), `patterns` (1.NBT.1 and 2.NBT.2 through 5.OA.3), `graphs` and
`shape_fractions`. All answer to a single whole number an exact solver produces, which is the constraint that rules
out most of 1.G and 1.OA — a shape-partitioning question has no number to score.

**The first two write the unknown as `?`, never `x`**, which is the whole distinction from `algebra` (6.EE.7);
`grade_appropriateness` lists them in `FORBIDDEN_BANDS` because the prompt asks and only the check enforces. **Neither
solver touches sympy**, so neither needs the bounded subprocess — the arithmetic is one operation on integers matched
by `^\d{1,4}$`; `test_young_topics.py` pins the first three. Both refuse rather than guess, and `solve_pattern`
derives the step then **checks it against every known term**: `2, 4, 6, ?, 9` has a first-pair step of 2 and is not an
arithmetic sequence, so taking the first pair would answer 8 confidently for a question with no single right answer.

**Each has its own shown-versus-scored check rather than `dataset_mismatch`**, which locates a dataset after the last
colon. There is no dataset here, there is an equation — and the question *is* the equation, so a text reading
"8 + ? = 12" over variables scoring 11 is answered correctly and marked wrong. Both also refuse any digit outside the
equation, since a second number on screen leaves a young reader unable to tell which one is meant.

**Grade 1 has no geometry at all**, and that is the end of this thread rather than a gap in it. 1.G produces no number
a solver can score, and the tempting fix — *"3 triangles and 4 squares, how many shapes?"* — is addition wearing a
geometry label. So `TOPIC_MIN_GRADE["geometry"]` is 2 and grade 1's list is `ordering`, `expressions` and the four
young topics: **six**, none of them geometry. That is the honest size of what this system can ask a 6-year-old. **An
unreadable grade lands there too.**

**A new topic needs five things wired, and the third fails silently.** `ALL_TOPICS` and `TOPIC_MIN_GRADE`; a `case` in
`question_generation`'s match (which now raises by name rather than falling through to an `UnboundLocalError`); **a
`math_topics` row, via a migration** — `record_topic_attempt` joins on `questions.subject` and attributes nothing when
that finds none, so a topic without one serves and scores questions while crediting the work to nothing; an entry in
`FORBIDDEN_BANDS`; and `frontend/src/lib/topics.js`.

**That last was four hardcoded lists, and was six.** `Analytics.jsx` counted the newer topics in *nothing* — its chart
drops empty bars, so they vanished with no hint anything was missing — and `Questions.jsx` offered no way to filter
the bank to them. Both bite hardest for grades 1–3, whose topics those are. `lib/topics.js` is now the only place a
list is written down, and **`topics.test.js` parses `ALL_TOPICS` out of `LLM_topic_decider.py` and fails if the two
disagree** — a React bundle cannot import Python, so the copy is checked rather than trusted. A third test fails on
any new file that writes a list of its own. `get_user_history` derives its per-topic history from `ALL_TOPICS` rather
than listing them again: `question_generation` reads `history[topic] if topic in history else []`, which fails *open*,
so a forgotten topic quietly lost its repeat-avoidance.

### Grades 9+ have no content of their own, and prompts cannot give them any

`advanced` was `upper` with the magnitude clause deleted. **An empty restriction reads to a model as no requirement,
not a harder one**, so it produced the easiest shape that fit: **83% of grade-9 questions three or more grades below
grade**, including `Simplify 5/9 + 7/11 - 2/9` (5.NF.1) on the **hard** tier. Every `advanced` tier now states a
requirement and the model complies.

**It barely moved the number — 83% → 81% — and that is the real finding.** The score is by the CCSS grade of the
*concept*, and every concept these solvers could score topped out at grade 8. Harder numbers inside 8.EE.7b are still
8.EE.7b. **Closing it needed solvers, not prompt text**, and each new solver has to be able to *score* what it asks.

Re-measured with `scripts/audit_grade_appropriateness.py` (which exists because the original audit left no script and
could not be repeated) after `quadratics` and `functions` landed: grade 9 **81% → 56%**, then **69%**, **81%** and
**100%** at grades 10–12.

**The grade-12 figure is arithmetic, not a sample.** The highest concept anything here can *score* is grade 9, so every
question this system can ask a 12th grader is ≥3 grades below by construction. **Adding topics at grade 9 cannot move
grades 11–12**; only a solver above grade 9 can.

**And the metric over-reads for practice topics.** It counts the grade a concept is *introduced*, so a 9th grader
finding a median under S-ID.2 is doing grade-appropriate work this measure scores three grades below. Good for "the
topic arrived before the concept", poor for "the student has outgrown this".

**Capping the grade-8 topics was considered and rejected, on arithmetic rather than taste.** "Concept grade ≥ student
grade" leaves grade 9 two topics and grades 10–12 **zero**; "within two grades" leaves 8, 5, 2 and **zero**. **Capping
cannot fix a ceiling** — it converts "serves below-grade content" into "serves no content", and `_safe_topic` calls
`random.choice` on that list, so empty is a 500 on every question at that grade.
`test_the_grade_eight_topics_are_knowingly_uncapped` holds the decision.
(`test_topics_at_eighth_grade_are_those_inside_both_bounds` replaced a test reading "allowed iff it has no ceiling",
true only until a topic had a floor above 8; assert the range, not the absence of a cap.)

### `quadratics`, `functions` and `spread` are the grade 9+ content

All three sit at grade 9 and are the first topics whose concept is above grade 8. `hs_solvers.py` is **pure
bounded-integer, no sympy, so no bounded subprocess**: every value goes through `parse_int`, matching `^-?\d{1,4}$`
*before* `int()` sees it. Reach for `safe_solve` only when something downstream needs a sympy object.

**The equation shown is rendered from the coefficients being scored, never parsed out of the text.**
`render_quadratic`/`render_polynomial` are the only things that write an equation and each generator requires the
output verbatim in `question_text` — same direction as `question_figures`: derive the presentation from the scored
data and a disagreement stops being representable. Sign handling is load-bearing, since `x^2 + -5x + 6 = 0` is
something the model "corrects", costing a retry on every negative middle coefficient.

**A two-root equation is only scoreable because the question names which root**, chosen *before* the call and pinned
in the prompt — `target` is deliberately absent from the schema. `shown_matches_scored` checks that too, because a
text asking for "the smaller solution" scored against the larger is a well-formed question, correctly solved, marked
wrong. It also refuses a text naming neither.

**The coefficients are chosen in code, and `quadratics` is the worked example for why.** Across three promptings —
the constraint as a description, as a construction recipe, and as a recipe with a worked example — llama3.1:8b
produced a factorable quadratic **0 of 3, 2 of 3 and 1 of 4** times, nearly always failing on `irrational roots`: a
freely chosen `b` and `c` almost never leave `b² − 4ac` a perfect square. Of the successes, two dropped the constraint
and one copied the example verbatim, so the tier was *also* not producing the content it named. No wording fixed it,
because it is not a wording problem.

`_choose_coefficients` builds from two distinct integer roots, so the equation is factorable by construction. Three
things follow, and the second generalises: **every retry that class caused was a billed model call that could not have
succeeded**; the `hard` tier can be the AC method, the right Algebra I rung and the *least* achievable thing to ask
for; and the tiers get a uniform meaning. Same move as `target`, and as the scenario in geometry and probability —
**decide the part with a right answer in code, and let the model write the sentence.** Reach for it whenever a
generator retries against a constraint the model keeps missing rather than against a malformed reply.

Four quadratics are refused rather than served: no real roots, a **repeated** root (where "the larger" names nothing
and every distractor would simply not be a root), irrational roots, and roots that are not whole numbers. That
restricts the topic to equations factorable over the integers, which is A-REI.4b's core rather than a limitation
worked around.

**`functions` hands its coefficients over too, and needed it more.** Its `_FOOTER` required each function verbatim
under a `FUNCTIONS AS THEY MUST APPEAR` section — **and that section was never emitted**, because the footer was
written for a design only `quadratics` implemented. The model was pointed at instructions that did not exist:
`compose` failed **3 of 3**, two of the topic's three tiers. **A prompt that references a section it never emits is
worth grepping for whenever a generator retries on formatting.** The inner function of a composition is always
degree 1, which bounds the answer by construction.

**`functions` is grade 9 on a narrower claim than its name suggests.** Evaluating a rule at a value is 8.F.2 and grade
8 does not require function notation; what is high school is the notation (F-IF.2) and composition (F-BF.1c) — which
is why `compose` is the medium *and* hard tier, since a version whose every tier was `evaluate` would be 8.F.2 wearing
an `f(x)`. `MAX_ABS_RESULT` bounds it because composition squares its input: a question answered 48,271,009 tests
calculator ownership.

**`spread` is S-ID.2, and it is standard deviation only.** The IQR and MAD S-ID.2 also names are **6.SP.5c** —
offering them would put grade-6 content inside a topic added to serve grades 9–12. Four things are load-bearing, and
the last three were each found after the first looked finished:

- **The answer is exact because the data is built to make it so.** Most datasets have an irrational standard
  deviation, and an answer rounded to whatever precision the formatter chose is the
  answered-correctly-marked-wrong failure wearing a decimal point. `_DEVIATION_PATTERNS` holds multisets summing to
  zero whose population variance is a perfect square, scaled and shifted by `_choose_dataset` — hardcoded rather than
  searched, because that is a property of the numbers and a search is a loop whose termination depends on its input.
- **The question must say "population standard deviation", and the generator refuses without it.** Sample standard
  deviation over n−1 is what many high-school courses teach and gives a different number, so "standard deviation"
  alone has two defensible answers and scores one. Not fixable by any solver; only the wording removes it.
- **Checking the data is *contained* in the text binds neither its extent nor its label.** The containment check every
  sibling uses was wrong here three ways at once: `"14, 16, 17, 18, 20, 25"` contains `"14, 16, 17, 18, 20"`, so an
  appended value passed; the sets could be written in either order; and — the one no ordering check catches — the same
  sets in the same order under **swapped labels** asks for A−B while B−A is scored. `shown_matches_scored` compares the
  text's comma-separated *runs* exactly and in order, and requires each set verbatim under its `Set A:`/`Set B:`
  label. **When the semantics live in a label the prose writes, the label has to be part of what is checked.**
  Deliberately strict where `question_consistency` fails open, because the generator supplied every number: any other
  number is a reply that ignored its instructions, not an ambiguity to read generously.
- **Binding the data is not binding the question, and the ask needs its own check.** Two correctly labelled sets in
  the scored order still pass every data check under a text asking for *Set B's own* standard deviation — scored 3
  where the screen says 5, with 5 on the option list, since `near` offers each set's own spread as a distractor. **It
  applies to the interrogative clause, not the whole text** — "a coach checks how much more consistent the team is" is
  context this prompt asks to vary, and refusing it costs three retries and a 503. Scoping has two traps, both found
  only by mutation: the clause may *contain* the data, whose labels then win the ordering, so the verbatim anchors are
  stripped before the labels are located; and **an ask is not always a question** — with no `?` anywhere a
  clauses-ending-in-`?` search finds nothing, and two arms reading that empty result in opposite directions is how one
  silently skipped its guard while the other refused a correctly worded imperative. Fall back to the closing sentence,
  not the whole text, which reinstates the context false-positive. Then **it must not pin word order**: direction rides
  on which label appears first, and a magnitude phrase separates it from "which is larger, Set B or Set A?", whose
  answer is a label where a number is scored.

**A rule a scenario cannot use is not noise in a prompt, it is a suggestion.** The shared footer told a *one-set*
prompt about `Set A:`/`Set B:` labels and comparisons, and llama3.1:8b duly invented both — labelling the single set
"Set A", making up a `Set B: 74`, and asking for Set B's standard deviation, which would have been scored as Set A's:
4 refusals in 6 one-set generations. Splitting the rules per scenario took Ollama from **1.25 to 1.00 model calls per
question**. **What the prompt *shows* beats what it *says*, and that includes rules it shows for a case that is not the
one being asked.**

**None of the three appears in `FORBIDDEN_BANDS`, deliberately, exactly as `algebra` does not.** Variable notation is
what they *are*; a band rule would refuse every question either exists to ask.

**Measured on both providers with the seeds injected, which is the path no test covers** — an unseeded cell fails open
to the heuristics, so any validation that does not inject the lesson text exercises the wrong path. Haiku 4.5: **6 of
6 in 6 calls**, no retries, answers correct by hand. llama3.1:8b: 3 of 3 per topic. Redo it after any edit to a seed
row or to a prompt these topics send.

### Angle answers are whole numbers through 5th grade, and decimals after

`algebra_complementary`'s coefficients are unconstrained, so it returns things like 11.875, and the lesson-plan text
asking for whole numbers did not stop it. `LLM_angle_relationship_generation` checks the **solved value** and
regenerates when it is fractional for a young student. A decimal is not a defect in itself — from 6th grade it is
ordinary mathematics — so the rule is scoped to the grades where it is not.

**Keyed on the raw grade, not `_grade_band()`.** The line falls between grades 5 and 6 while `middle` spans 4, 5 **and**
6, so no band boundary is in the right place; pinned by
`test_the_cutoff_splits_the_middle_band_which_is_why_it_is_grade_keyed`. An unrecognised grade falls through to
"decimals allowed", matching `_grade_band()`'s own `advanced` default: the constraint is a scaffold, so the safe
direction when the grade is unknown is to leave the mathematics alone.

**The solve moved inside the retry loop to make this possible** (`_solve_scenario`). Whether the answer is a whole
number is a property of the *solved value*, so it cannot be checked until the scenario has been evaluated — and a
question that fails has to be regenerated, not patched.

## A question may carry a figure, and it is a spec the client draws

`questions.figure` holds a specification built by `question_figures.py`;
`components/questions/QuestionFigure.jsx` is the only thing that turns one into pixels. It exists because grades 1–3
mathematics is largely visual and the standards this system could not ask were mostly the visual ones —
`rectangle_area_by_counting` (2.G.2) was being asked in *words*, "a rectangle split into 3 rows of 4 same-size
squares", which is a description of a picture rather than the picture.

**The figure is derived from the data the solver uses, and no generator asks a model for one.** That is the design,
not a preference. `question_consistency` exists because a model free to write the question text and the scored data
separately eventually disagrees with itself, and the student answers the version on screen while being marked against
the other. A picture is the same hazard with **no text for any check to read**. Reading `variables`, the same dict
`geometry_solvers` indexes, makes that disagreement unrepresentable rather than unlikely.

**A spec, not an SVG**, for two reasons. The drawing and the sentence a screen reader is given come from one object,
so they cannot describe different pictures — the rule `AccessibleChart` exists for, and it binds harder here because
a figure has no text of its own. And a renderer fixed later applies to every question already in the bank; stored
markup bakes today's renderer into rows that outlive it.

**A figure is an enrichment and never a requirement.** `figure_for` returns `None` for a scenario with no figure,
values it cannot use, or a size it will not draw, and it never raises — it runs after the solve, so an escaping
exception would turn a checked, grade-appropriate question into a 500 over a decoration. The client matches that: an
unrecognised `type` renders nothing rather than throwing, which is what an older bundle meets against a newer bank.
Both ends bound the grid at 12 a side, because a bank row outlives the code that wrote it.

**The column is nullable with no default** — same four-state rule as `sessions.chart_paths`. A `'{}'::jsonb` default
would claim every question ever generated was considered for a figure and found to need none. The generators return
the key as `None` rather than omitting it.

**Every surface that *presents* a question renders its figure, and there are five.** This shipped wired into two —
the adaptive view and flashcards — leaving the teacher's session review, the bank modal and practice test mode showing
the wording with nothing to count, which is a different question from the one the student answered.
`normalizeQuestion` returns a fixed object, so a key it does not name does not exist for any caller downstream; it
carries `figure` through. `QuestionFigure.test.jsx` walks the source and fails on a file that renders question text
without it — matching `<QuestionFigure`, **not the bare name**, which a dangling import satisfies and which is
exactly what deleting the element leaves behind. The first version of that check passed against a build with the
render removed and the import kept. The teacher dashboard's "Recent Questions" list is the one stated exception: a
`line-clamp-2` row is a *reference* to a question, not the question.

## A question carries its Common Core code, resolved by grade and scenario

`questions.ccss_standard` is the machine-readable copy of the codes `TOPIC_MIN_GRADE` and the two
`SCENARIO_MIN_GRADE` tables only ever cited in comments. `ccss_standards.ccss_for(topic, grade, scenario)` resolves
it and every generator attaches it; `CCSSBadge.jsx` renders it on the same five surfaces `QuestionFigure` reaches,
with the same source-scan exhaustiveness test.

**Resolved by grade, not band, and by scenario first.** A band spans three grades and the standard changes inside it
(`1.MD.4` at grade 1, `2.MD.10` at grade 2, both "early"), and a scenario names the standard inside a topic. A
*ladder* of `(floor_grade, code)` per topic or scenario picks the highest floor at or below the student's grade; a
grade below every floor takes the lowest rung, since the defense-in-depth tiers still describe content.
`test_every_scenario_in_a_gate_table_has_a_code` pins the scenario tables to the gate tables. The one imprecision is
difficulty inside a band: grade 6 algebra's two-step medium tier is 7.EE.4 content and reads `6.EE.7`, because the
resolver does not see the tier.

**`add_question_to_supabase` dedupes on text *and* standard.** The code is the first stored field derived from the
student's grade, so one text generated at grade 6 and again at grade 8 is two rows (`6.EE.7`, `8.EE.7b`) rather than
one whose badge belongs to whichever grade wrote it first and then contradicts what the second student saw. Nothing
constrains `question_text` unique, so the second row inserts cleanly — and a text regenerated after the column landed
no longer matches its NULL-coded predecessor, so the bank gains one row per such question, visible to a teacher as a
duplicate. That is the accepted trade: updating the old row in place would stamp a grade-8 code on a row grade-6
answers already reference. **A match also needs the same answer and figure**: `shape_fractions` and `graphs` keep
digits out of the text, so on text alone every new figure took the first row's id. The options are **not** compared —
the wrong answers are random and shuffled every generation, so comparing them, even as a set, almost never matched and
added a row per question served. A repeat is instead **served the stored row's options**, wrong answers and order,
because an answer is stored as an index into them. A single-string answer is a lookup filter, so a generic text with
many rows still finds its match inside the candidate cap; a list answer (`mode`, `ordering`) is compared parsed, since
`correct_answer` is text and returns it as JSON text. And **every scenario-selecting generator checks the reply's
scenario name**
(`expressions` was the one that did not): an off-name reply misses `SCENARIO_LADDER` and takes the topic's grade-1
rung.

## `shape_fractions` reads a fraction off a picture, and refuses an ambiguous one

1.G.3 (halves and fourths), 2.G.3 (thirds), 3.NF.1 (a/b as a parts of b). **Distinct from `rationals`, which is
4.NF.3 onward and is fraction *arithmetic*** — this is recognition, which is why it sits at grade 1 while `rationals`
starts at 4. Its figure is required, for the same reason `graphs`' is.

**Lowest terms is required, and refusing otherwise is the point.** Two shaded parts in four is a perfectly good
picture and an ambiguous question: `2/4` and `1/2` are both correct readings, and whichever the solver picked, a
student giving the other is marked wrong for a right answer — the failure this codebase treats as the worst
available, and worse than a refusal, which costs one retry. Reducing the answer instead is worse still: the student
is asked to read the picture, and the picture says two of four. `question_figures._part_whole` deliberately does
**not** check it — a reducible fraction is perfectly drawable, and drawability and answerability are different
questions.

**Grade 1 has exactly three legal pictures, and the model reaches for a fourth.** 1.G.3 holds it to 2 or 4 parts, and
lowest terms leaves only `1/2`, `1/4` and `3/4` — while half-of-four is the shading a model produces first. On
llama3.1:8b at grade 1 / easy: `2/4` on all three attempts, so the request **failed outright** rather than degrading.
That is the cost of a refusal landing on a cell with almost no legal answers left, and it is not visible from either
the standard or the refusal rule alone. The fix is in the lesson plan, which names the three fractions for grade 1:
4 of 4 generate afterwards, 2 of them still spending one retry on `2/4`. **Check a narrow cell's retry rate, not just
that it can succeed** — a tier whose legal answers you can count on one hand is where an exhausted retry budget stops
being theoretical.

**The distractor space is checked exhaustively rather than sampled**, because it is 21 fractions. Two properties,
both violated before: an option of one or more cannot be part of a shape, so `2/1` is not a misreading a child could
make but an option nobody considers — which quietly makes a three-way choice a two-way one; and `1/1` and `2/2` were
both offered against `1/2`, two options of equal value that go together with a single thought. Halves is what forced
neighbouring denominators into the candidate list: with only near-misses of 2, the sole proper distractor available
was `1/3`.

**The whole is a constant width and the parts divide it**, not the other way round. Fixed-size parts drew eighths at
twice the width of halves, which says the wrong thing about what a whole is — and is exactly the misconception these
standards are about. Found by rendering it and looking.

## `graphs` is the one topic whose figure is required

1.MD.4 ("how many more or less"), 2.MD.10 (a bar graph with up to four categories), through 3.MD.3. Two scenarios:
`how_many_total` is one addition, `how_many_more` a reading *and* a subtraction. It is the visual precursor to
`mean`/`median`/`mode` — counts read off a graph at grades 1–2, statistics over a listed dataset at grade 6.

**Everywhere else a figure that cannot be built costs the picture and nothing else**, because the question text
stands alone. "How many more cats than dogs?" is not answerable read aloud — the counts live *only* in the graph. So
this generator treats an unbuildable figure as an unusable reply and retries. `figure_for` keeps its own fail-open
contract and still returns `None` rather than raising; **the decision that `None` is fatal belongs to the topic that
cannot do without it**, so a new figure type does not inherit a requirement it does not have.

**A digit in the question text is refused.** Writing the counts out hands the student the reading the question exists
to ask for — it stops being a graph question and becomes arithmetic. Checked, not merely requested.

**A comparison is read from the question text, never from the model's `target`.** The student answers the sentence,
so `comparison_in_text` takes the bars named after the last "how many more", singular or plural, and scores the first
minus the second. Anything but exactly two named bars is a retry, and so is a smaller-first comparison (a false premise).
`backend/repair_graph_comparisons.py` checks stored rows against the same rule.

**`categories` is a list of `{name, count}`, not a map.** The obvious `{"cats": "7"}` cannot be schema'd at all — the
API refuses an open `additionalProperties`. Choosing a shape that *can* be schema'd costs the generator one
indirection and is cheaper than accepting the gap.

**Asking how many more of the *smaller* bar is refused, not answered with an absolute value.** The question on screen
asks for something with no answer; scoring the difference would mark a student right for answering a question nobody
asked.

Capped at grade 3: 3.MD.3 is the last bar-graph standard, and grades 4–5 move to line plots (4.MD.4, 5.MD.2), which
is a different figure and a different reading.

**Two things about the renderer were found by looking at it, not by a test.** Column width is derived from the widest
label — at a fixed width "storybooks" and "picture books" printed on top of each other, with both labels present and
correct in the DOM, on a figure whose entire job is to be read. And gridlines are ruled at every unit, so bars can be
*counted* rather than compared, which is the reading 1.MD.4 asks for. The component is named `BarGraph` because the
obvious name is one `AccessibleChart.test.jsx` matches as a bare word to find Recharts charts rendered outside it —
and a hand-written `<svg>` is the case that guard states it cannot see, so the match would be a false accusation. The
word cannot appear in that file's comments either; the guard reads the file, not the syntax tree.

## `grade_appropriateness` checks the output, because everything else only checks the prompt

`COMPLEXITY_BY_GRADE` and the lesson-plan text are both **prompt-level** — they ask the model for something and
nothing verifies it complied. So `find_violation(question_text, topic, grade_band)` runs inside each generation retry
loop: a violation retries, and exhausting the retries raises, which `_prefetch_worker` already catches. Thirteen of
the seventeen topics are wired in; `algebra`, `quadratics`, `functions` and `spread` are the exemptions above.

**It tests one thing — algebraic variable notation reaching a band that must not see it — and the narrowness is the
design.** A check with a real false-positive rate is worse than no check: it burns retries, and a question rejected
for a bad reason looks exactly like a model that cannot follow instructions. `x` as a multiplication sign is the
false positive that actually occurs, so the pattern is `\d+[xyn]\b` — anchored so `2x` matches while **`6 x 4` and
`6x4` do not** (the trailing `4` kills the word boundary). `test_ordinary_questions_are_not_refused` is the
load-bearing half of its test file; a naive `/[xyn]/` fails exactly those cases. `geometry` is early-only, since
`upper` legitimately labels triangle sides `a`, `b`, `c`.

What it deliberately does **not** check: magnitude/decimal/negative rules (`-` is also a hyphen and a range
separator, so detection would be guesswork), and whether the question reflects the lesson-plan text's *content* —
that needs a model to judge, which puts an unbounded LLM call on the hot generation path. **So this bounds the damage
a bad lesson plan can do; it does not confirm a good one was followed.**

**Forbidden operators in early-band `expressions` are the one exception, and they are checked because reading the
output found them.** On llama3.1:8b with the lesson plans seeded, grade 1 / easy: **2 of 8 questions came back with
parentheses** — `Solve 5 + (2 - 1).` — while the *same prompt* said "ADDITION AND SUBTRACTION ONLY. Do NOT use
multiplication, division, or parentheses." **A few-shot example beats a textual constraint**: every scenario example
in `expr_prompt` is written for older students (scenario 1's is `36/3+(8*2)-(15-7)+4`), and the model followed their
shape over the rule. Three changes, all needed: scenario 2 (`order_of_operations`) is withheld from `early` — it is
5.OA.1 and is *defined* by mixing precedence, so it cannot be expressed within the band's rule at all —
`EARLY_BAND_EXAMPLE` gives the band a worked example in the shape it is allowed, and the operator check rejects what
still slips through. Re-measured: **10 of 10 compliant.** Unlike negatives and decimals these characters have exactly
one reading inside a generated expression.

**That is the general lesson: seeding a lesson plan does not make the model follow it, and neither does an
unambiguous instruction sitting next to a contradicting example.** Anything added to these prompts needs its effect
read off generated output before it is believed.

## The question shown and the data scored are two fields, and they must agree

Every generator returns a `question_text` the student reads and a separate structured field the solver computes from —
`variables` for the dataset topics, `values` for `ordering`, `items` + `scenario` for `probability`. **Nothing checked
that they described the same thing.** On llama3.1:8b, **2 wrong answers in 12 generated**: a `mode` question shown
`8, 4, 12, 16, 4, 14, 8, 10, 20, 4` and answered `[8, 4]` when 4 is the only mode, because the scored `variables` were
not the numbers on screen; and a `probability` question asking *"what is the probability of selecting an EDM band?"*
answered `18/23`, the **complement**, because the text asked a positive question while the JSON said
`scenario: not_probability_of`.

**This is the worst failure shape available here** — the question is well-formed and answerable, the student answers it
correctly, and is marked wrong against data they never saw. Worse than a refused question, which costs one retry.

`dataset_mismatch` compares the numbers **after the last colon** against the scored list (these prompts all put the
dataset there, which is what makes locating it reliable); `negation_mismatch` requires a negated wording and
`not_probability_of` to imply each other, **in both directions**, since a negated question scored as `probability_of`
is wrong by the same amount.

**The two checks do not cover the same topics.** `dataset_mismatch` is in `mean`/`median`/`mode`/`ordering` only, and
`negation_mismatch` is in `probability` only. This file said `dataset_mismatch` covered probability too, for months; it
never has — probability's counts live in the sentence body, not after a colon, so the check would be inert there
anyway. But *documented as wired and absent* is the worst of the three states, because it is the one nobody re-checks.
Verify with `grep -l dataset_mismatch LLM_*_generation.py`, which is cheaper than trusting this paragraph.

**Both fail open**, which is what makes them safe to run on every question: order is ignored (the solvers sort anyway),
non-numeric `variables` are skipped, and a question with no colon-delimited list is left alone rather than compared
against stray numbers in the sentence. A false rejection burns retries and looks exactly like a model that cannot
follow instructions. They catch a clear contradiction; they are not a proof of agreement. `algebra`, `expressions`,
`geometry` and `angle_relationships` are **not** covered: their scored fields mix operators and labels with numbers, so
there is no comparable multiset.

**Measure how often a fail-open check *engages*, never just how often it fires.** A check that never finds anything to
compare reports a perfect false-positive rate while doing nothing, and reads as evidence that it works. The dataset
check was inert on **half** the `ordering` questions, because `_as_floats` rejected fractions as "not comparable" —
while `solve_ordering` sorts on `float(sympify(v))` and handles them fine. Fractions are now one token and both sides
are compared **by value**, so `4/5` shown against `0.8` agrees. A **mixed number** anywhere in the text fails open:
`1 1/2` is one value to a reader and two tokens to the regex.

`scripts/measure_generation_checks.py` is where that measurement lives. Run it against `ollama` for a free baseline and
`claude` for the number that describes production; it bills like any other caller. On claude-haiku-4-5 at 5th grade /
medium, 3 per topic: the dataset check engaged on **12 of 12** applicable questions and agreed on all of them,
`negation_mismatch` 3 of 3, and `grade_appropriateness` on all 27 of the nine topics it is wired into, refusing none.

**Two things about running it are worth more than the numbers.** It must observe the checks *where they run* — the
generators call them against the raw model JSON, and the dict they **return** has the scored field stripped, so a
harness that re-derives the inputs from the return value reports `inert` on everything. That produced a confident "0 of
15, the check is switched off" that was entirely an artefact of the measurement, which is this rule eating its own
tail. And it must keep **`n/a` (never called) apart from `inert` (called, found nothing)** — collapsing them is how a
check that was never wired reads as one that is working, which is exactly how the probability gap above survived.

### Two retry-loop rules every generator shares

**A key read *below* the `for/else` must be validated *inside* it.** `probability` reads `sides`, `items` and
`scenario` after the loop, and `required_keys` can list neither of the first two — they belong to one scenario each. So
a dice reply with no `sides`, a bag reply with no `items`, and a bag question mislabelled `dice` each raised `KeyError`
on attempt 1 and reached the student as a **500**, where every other malformed reply costs a retry. It also never
compared the returned `scenario` against the one it asked for, and this prompt sends all three blocks and names the
wanted one by *number*. The schema covers none of this: it closes the dice half only, and only on Claude. **The
question to ask of any generator is which keys the code below the loop reads that the loop never checked.**

**The `if not raw:` guard comes before anything that touches `raw`.** `extract_json` answers `None` for a response with
no JSON in it — prose, a refusal, an empty completion — which is the exact case the three attempts exist to absorb, so
a `.replace` or a `.strip()` above the guard raises `AttributeError` straight out of the loop and the retry never
happens. `tests/test_generation_retry_loop.py` is parametrised over every `LLM_*_generation.py` file by glob so the
next copy cannot be a one-off.

## The solvers trust model output the retry loop above them already distrusts

Switching to Haiku found five bugs, none in the migration: all five were in code that had only ever seen
`llama3.1:8b`'s output shape, and every one is the same mistake — a solver, or a distractor generator, taking the
reply as well-formed *after* the retry loop that exists to reject malformed replies has already broken.

**Solve inside the retry loop, never after it.** That placement is what turns each of these from a retry into a 500.
Measured against Haiku 4.5 at 8th grade, 3 generations per topic:

| Site | Failure | Was |
| --- | --- | --- |
| `LLM_geometry_generation` | a scenario name the `match` has no branch for | `UnboundLocalError` on `solution`, 2 in 3 |
| `LLM_geometry_generation` | a known scenario missing a variable the solver indexes | `KeyError: 'b'`, 2 in 3 |
| `LLM_algebra_generation` | `solve` returning `[]` for `x+1 = x+2` | `float(None)` → `TypeError`, 1 in 3 |
| `LLM_algebra_generation` | `solve` returning two roots | **a wrong answer** — `[0]` scored one root and marked the other choice wrong |
| `incorrect_solution_generation` | a one-term answer like `5*x` | **an infinite loop**, 28 minutes at 100% CPU |

The algebra multi-root case is the one to notice: this file already *said* the topic is one linear equation with one
solution, and nothing enforced it. **A constraint documented as a limit is a wrong answer waiting for a model that
writes one.**

Geometry is the same shape: every input and answer is a length, area or volume, and the solver checked only that the
answer was finite, so a perimeter of 10 with a known side of 8 was served with -3 correct. `solve_scenario` now
refuses any non-positive input or answer, and three triangle sides that break the triangle inequality; each is a
retry.

**`while len(results) < n` needs a bound and a deterministic filler.** All three generators in
`incorrect_solution_generation` were unbounded, and the symbolic one hung *deterministically* on its commonest input:
`wrong_coefficient` only perturbed an `Add`, so for `5*x` it returned the expression unchanged, leaving
`sign_error`'s negation as the only alternative — two distinct values where three are needed. **Whether a randomised
search can reach `n` distinct results is a property of the *input*, not of how long you try**, so the retry count is
not the bound; the filler is. It read as intermittent because `_pick_scenario` reaches `simplify` about one time in
three, and only above `middle`: **a hang that depends on a random branch below a grade gate looks like flakiness and
is not.**

**A `raise` from a helper does not reach a retry loop the call site sits below.** Every generator's `for ... else` has
already run by the time the solve happens, unless the solve was deliberately moved inside. **If the recovery is a
retry, the check belongs above the `break`, and returning `None` says so where raising does not.**

**Use `faulthandler.dump_traceback_later(n, exit=True)` rather than reasoning about where a hang is.** Two plausible
mechanisms were proposed and implemented against this one before it was measured, and neither was the bug. The stack
dump named it in one run.

**A diagnostic print must not be able to kill what it is describing.** The generators print the raw reply on their
error paths; Windows console streams are cp1252, so a `π`, an em-dash or an accented name raised `UnicodeEncodeError`
from inside the retry loop, which absorbs bad JSON and bad shapes and then died on printing them.
`console_encoding.make_console_safe()` sets `errors="replace"` on both streams and is applied from `llm_client`, which
every generator imports — deliberately at the stream and not at the 23 print sites, so the next print cannot forget,
and so it covers the ones passing model-derived values without looking like it. The console's own encoding is left
alone: forcing UTF-8 onto a cp1252 console trades a crash for mojibake.

### `safe_solve.py`: the boundary is the whole solve, not its last step

A CPU-bound sympy call cannot be bounded in-process — `parse_expr("9**9**9")` never returns, the operand is the
model's, and the spin holds the GIL inside CPython's long-integer code, so a watchdog thread is never scheduled and a
signal handler never runs. **Only an external kill works.** It costs ~0.85 s of sympy import per call, a minority
addition to a ~1–3 s model call, paid only where something is solved.

**All eleven topics that touch sympy are wired, and getting there took four rounds of finding the next unwired one.**
`SCENARIO_VARS` checks that keys are *present*, which says nothing about the values behind them, and a `try/except`
catches exceptions rather than non-termination. **If a topic touches model text with sympy anywhere, all of that topic
belongs in the worker.**

`geometry_solvers.py` and `angle_solvers.py` exist for that: the worker cannot import a generator module, which pulls
in supabase, flask and dotenv at import, so the pure arithmetic lives apart from the prompt and the retry loop.
Anything a bounded worker must run needs the same separation — which is why `invalid_reason` moved alongside the angle
solvers rather than staying beside its caller.

**Most topics need only the parse bounded, not the whole solve.** `safe_sympify_values` covers five of the eleven:
they parse the model's numbers and then do ordinary arithmetic, which cannot hang. Only `geometry` and
`angle_relationships` needed their solvers moved, because both keep sympy *expressions* past the parse. Three parses
stay in-process deliberately, and all three read the **worker's own output** rather than the model's: `sympify(solved)`
in `expressions` and `rationals`, and `format_number`'s fallback in `median`, all bounded by `MAX_RESULT_CHARS`. **A
parse whose operand came from the model belongs in the worker, full stop.**

**Every worker branch checks its result is usable, and the `evaluate`/`simplify` one did not.** `1/0` came back as the
string `zoo` and `0/0` as `nan`; `rationals` served them — `correct_answer='zoo'` among the options — and
`expressions` raised `TypeError` building distractors. **`is_number` cannot be the test**, since `simplify` exists to
return `5*x`. **And `is_finite is False` cannot be it either: `nan.is_finite` is `None`, not `False`**, so that form
catches `zoo` and the infinities and lets `nan` through. The guard tests both.

### Two budgets, two meanings

**`SOLVE_TIMEOUT` (3 s) bounds the arithmetic; `SOLVE_STARTUP_BUDGET` (15 s) bounds getting there.** There was one
budget and it was the wrong one: it covered launching Python and importing sympy, ~99% of an ordinary solve, so a
nominal 3 s was ~3× margin over *startup* and any co-tenant spent it. With CPU hogs running, **8 of 8 solves were
killed at 3.0 s** — and since a solve failure became `SolverUnavailable`, that was a **503 with no retry** on eleven
topics.

`_solve_worker` prints a readiness line once sympy is loaded and `_run` times the two phases separately. Re-measured, 8
concurrent solves (give the hogs ~5 s to spin up — a 2 s settle measures an unloaded machine and reports a fix that
isn't there):

| load | before | after |
| --- | --- | --- |
| idle | — | **8/8**, slowest 2.2 s |
| saturated (18 hogs / 18 cores) | 0/8 | **7/8**, slowest 30.7 s |
| 2× oversubscribed (36 hogs) | 0/8 | **1/8**, slowest 32.4 s |

**2× oversubscription is not fixed.** What the split buys is *saturation*; past that the machine cannot start
interpreters fast enough. The other half of the trade is the tail: a contended startup that retries holds an anyio slot
for ~30 s where the old code failed in 3.

**Only a *startup* timeout is retried, and the split inverted that.** While the budget was ~99% startup, "timeout"
almost always meant contention. Now the two mean opposite things: a solve timeout is 3 s against ~10 ms of arithmetic,
so it is a spin, and a spin spins again — retrying costs a second full startup to reach the same kill with the student
waiting through both. A startup timeout is the machine failing to launch Python in 15 s, which passes. Worst-case hold
on an anyio slot drops to 18 s absolute rather than 42 s.

Tune them differently: `SOLVE_TIMEOUT` has ~300× margin over the maths and can be tightened; `SOLVE_STARTUP_BUDGET`
absorbs contention, and tightening *it* reinstates the old failure. **Treat any startup multiplier quoted here with
suspicion, including these**: startup is ~0.8 s idle on the machine these were taken on and 1.4–2.2 s under ordinary
background load. `SOLVE_RETRY_BUDGET_FACTOR` is **gone** — it widened the solve budget on a second attempt that no
longer exists, and a knob whose name promises tuning that is not available is worse than its absence.

`_probe_startup()` runs at import: it times one trivial solve and raises the **startup** budget if the configured value
is under `_STARTUP_SAFETY_FACTOR` (3×) of the measurement. It **clamps up and logs; it never refuses to start** —
raising there would take the whole backend down over one topic's tuning knob. A probe that cannot run keeps the
configured value and says so separately: that means the subprocess mechanism is broken, a different problem, and a
budget guessed from a failed measurement is worse than one someone chose. `SOLVE_STARTUP_PROBE=0` skips it. **The probe
must escape the budget it validates** — it runs a solve, so a too-small value made it time out reporting the problem it
should have measured; `_run` takes a `startup_timeout` override for that one caller.

**A threshold that depends on how fast the machine is has to be measured on that machine, not written down.** Both
tests around the probe got this wrong in different directions: one pinned the shipped default against the measured
floor and failed *locally* under suite load; the other hardcoded 1.0 s as "obviously below the floor" — true on a
laptop where startup is ~0.8 s and the floor ~2.4 s, false on CI where startup is 0.32 s and the floor 0.97 s, so the
clamp correctly did not fire and the test failed for asserting it had. **CI is the faster machine here, which is the
opposite of the usual flakiness direction.** Probe first, derive the value from that measurement, leave an order of
magnitude rather than a factor of two, and assert what the probe guarantees — that the *effective* budget clears the
floor — not that the shipped default does.

### Concurrent solves are bounded

Solves contend for CPU, and the budget collapsed for all of them at once rather than degrading: unbounded, 16
concurrent all succeeded, **32 gave 7 of 32, 48 gave 0 of 48.** Nothing bounded it —
`GENERATION_MAX_CONCURRENCY` bounds *model calls*, and prefetch, the inline path and practice reach the worker
independently — so a class starting together took every solve-backed topic down. `SOLVE_MAX_CONCURRENCY` (8) fixes it
by queueing: 48 of 48 now succeed, and 96 of 96 within `SOLVE_QUEUE_TIMEOUT` (20 s). The bound stays after the phase
split for a different reason: it is what keeps a class starting together from putting a hundred interpreters on one
machine.

**The permit is taken per attempt**, not around both: holding one through a wait that has already failed shrinks the
effective concurrency exactly when the machine is busiest.

**Waiting for a slot deliberately does *not* come out of the solve budget** — the opposite of `llm_client`, and for a
stated reason. There the wait is the caller's own deadline. Here the deadline exists to bound a CPU spin, and a spin
does not start until the process does; charging the wait would fail solves that then had no budget left to run in,
under exactly the load the bound exists for.

**Take the permit through `_solve_slot`, never a bare acquire/release** — the refusal raises *inside* the guarded
region, which is the path most likely to run under load. Same rule as `llm_client._generation_waiter`.

**Test the bound by holding the permits, not by measuring a peak across threads.** An earlier version passed against a
build with the bound removed, because the pool happened to start its threads in groups no larger than the bound — a
peak is not deterministic, and a mutation check that passes is worse than no check. What *is* deterministic is that a
caller finding no free slot is refused rather than run.

### One solve per attempt

A second worker call per attempt is a different bound, not a slow path: one call measured 0.77 s against 1.47 s for
two, and since `SOLVE_TIMEOUT` is sized so a hung reply costs about three attempts, doubling the calls took mean's
worst case to 18 s where every other topic's was 9 s. `tests/test_one_solve_per_attempt.py` counts calls at runtime,
across all four entry points, because `probability` has two call sites on exclusive branches and a source count
cannot tell those from a duplicate.

### `SolverUnavailable`: a solver that could not run is not a bad reply

Five things in `_run` returned `None` and only one was the model's fault. A timeout, a failure to spawn, a non-zero
exit and unreadable output all say nothing about the reply. Collapsed into one `None`, a load-dependent timeout retried
three times — **billing a model call each time that could not possibly help** — and then raised *"Failed to generate
valid JSON after retries"*, a claim about the model for a subprocess that never ran. It surfaced as an intermittent
failure in an unrelated topic's test. It now raises on the first attempt, at one model call instead of three, naming
the cause.

It subclasses `llm_client.GenerationUnavailable`, so both `main.py` call sites already turn it into a **503**. A worker
that ran and refused the input still returns `None` and still retries, because a different reply genuinely might work.
**`_probe_startup` must catch it**: that function runs at import, and raising there would take the whole backend down.

## An answer's subject is the generator's own name, never the model's

Each `LLM_*_generation.py` returns `question_topic`, which `add_question_to_supabase` stores as `questions.subject` —
the column `record_topic_attempt` joins on. Nine generators hardcode their own topic; `rationals` returned
`question_data["question_topic"]`, and its prompt named `"algebra"` in prose and `"rations"` in the JSON example.
Measured 3 of 3 against Haiku: **every fractions question was stored as algebra**, so a student's rationals work was
credited to algebra in `user_math_performance` — the table the adaptive engine reads to choose what to serve next —
while `rationals` accumulated nothing. A subject outside `ALL_TOPICS` is the other half: the join finds no row and the
attempt is attributed to *nothing*, silently, since the helper never raises.

Letting the model name the topic is the same hazard as letting the caller name it, one layer up.
`test_every_generator_stores_its_own_topic_name_not_the_models` pins both halves — the value must be a **literal** (a
generator reading the model's value could still pass a membership check on any given run) and must be in
`ALL_TOPICS`.

**The repair migration's rule is a prose regularity, not a structural fact.** An earlier version of this entry claimed
an algebra question must contain `=` because `_solve_worker` splits on it; it splits `variables`, which is **never
stored** — `questions` has no such column. What is filtered is `question_text`, which the model writes freely. So it
is a pattern observed on a sample and applied irreversibly, and it is built for that: **three signals must agree** (no
`=`, no coefficient-variable `\d+[xyn]`, a fraction present), a row where they disagree is left alone, and every
change is recorded in `question_subject_reclassification` so it can be audited and reversed. That table is why a
heuristic is acceptable here at all — which is also why its `attempts_moved` counts **only** rows that moved
something: a `rations` row credited nothing, so recording its answer count would tell a reversal to push attempts back
onto algebra that were never there.

Over 25 real rows the three signals partition them completely — 19 with `=` and a coefficient-variable and no
fraction, 6 with a fraction and neither. **A fractional answer is not a usable signal** (genuine algebra answers are
frequently fractions, `7/2`, `17/6`) and neither is "mentions x", since one of the six reads *"Solve for x: 3/4 +
2/5"* — a rationals question wearing algebra's phrasing, because the prompt told the model the topic was algebra.
Only the coefficient form separates them.

It moves the `user_math_performance` counters too, and the repair is **all-or-nothing**: if `math_topics` has no
`rationals` row, nothing moves — subject included. Guarding only the counters would let the subject change while the
attempts stayed on algebra, and a re-run would then find nothing to correct, making that inconsistency permanent.

Rows written before the fix still carry the wrong subject; nothing distinguishes them from genuine algebra rows except
the question text.
