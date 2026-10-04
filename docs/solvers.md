# Solvers

Part of the AdaptiveLearning conventions; `CLAUDE.md` is the root and holds what binds here as much
as anywhere — the canary and the four numbered rules — and `docs/question-generation.md` holds the
generators that call these. Read those first.

**Read this file when you are touching** a solver (`safe_solve`, `geometry_solvers`, `angle_solvers`,
`hs_solvers`), a distractor generator, the bounded sympy worker, or a `SOLVE_*` setting.

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

The algebra multi-root case is the one to notice: `docs/question-generation.md` already *said* the topic is one linear equation with one
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
in supabase and dotenv at import, so the pure arithmetic lives apart from the prompt and the retry loop.
Anything a bounded worker must run needs the same separation — which is why `invalid_reason` moved alongside the angle
solvers rather than staying beside its caller.

**Most topics need only the parse bounded.** `safe_sympify_values` covers five of the eleven; `geometry` and
`angle_relationships` moved whole, since both keep sympy *expressions* past the parse. Three parses stay in-process and
read the **worker's own output**: `sympify(solved)` in `expressions` and `rationals`, and `median`'s fallback. **A parse
whose operand came from the model belongs in the worker — and the worker is not a sandbox**: sympify *evals*, as the
backend's user, with its files and network. So `_run` refuses any request text or non-`values` result that is not plain
arithmetic (`_SAFE_TEXT`: no quotes, brackets, commas, backslashes or `__`), before a worker starts or a caller parses;
a model's `×`, `÷`, `·`, U+2212, thousands comma and stray whitespace are rewritten first, in the request the worker gets.

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

`_probe_startup()` runs on its own thread, started by `_lifespan` and joined at shutdown — not at import, where its
sympy subprocess delayed every cold start. It times one trivial solve and raises the **startup** budget if the
configured value is under `_STARTUP_SAFETY_FACTOR` (3×) of the measurement. It **clamps up and logs; it never refuses
to start** — one topic's tuning knob must not take the backend down. A probe that cannot run keeps the configured value
and says so separately: that means the subprocess mechanism is broken, a different problem, and a budget guessed from a
failed measurement is worse than one someone chose. `SOLVE_STARTUP_PROBE=0` skips it. **The probe must escape the
budget it validates** — it runs a solve, so a too-small value made it time out reporting the problem it should have
measured; `_run` takes a `startup_timeout` override for that one caller.

**Off the import path, it runs beside live traffic, and three things follow.** It skips the concurrency cap
(`queued=False`), so a class that starts at once cannot make it time a queue as startup, or refuse it and log a broken
solver. Until it finishes, every other solve gets the probe's own 60 s startup budget, since the configured value
(15 s by default) is unchecked on that machine until then. That window is normally the first seconds after boot, but
a hung worker keeps the probe alive about two minutes (two 60 s startup attempts), and a solve meanwhile can hold a
slot for up to 120 s the same way. And each attempt re-reads the budget, so a retry picks up a clamp that landed meanwhile.
Once stopped for shutdown it prints nothing, including the lines `_run` prints for it (`say=`).

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
**`_probe_startup` must catch it**: on its own thread, an exception ends the probe on a traceback instead of the line
that says the subprocess is broken.
