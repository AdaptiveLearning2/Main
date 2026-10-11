# Reporting

Part of the AdaptiveLearning conventions; `CLAUDE.md` is the root and holds what binds here as much
as anywhere — the canary, the four numbered rules this file cites, consent and the school year, and
the reporting rules every surface obeys. Read those first.

**Read this file when you are touching** a reporting endpoint or the function behind it (the weekly
report, the signal summary, the term trend, the cohort panels, the teacher analytics),
`signal_daily_rollup` or `rollup_signal_day`, a session close or either sweep, session alerts,
archived charts, how an answer is recorded, or the strategies and chart-summary panels.

## The daily rollup is written as sessions close, never at expiry

`signal_daily_rollup` holds one row per student per school day per channel (`cognitive|heart|emotion`), written
by `_rollup_session_days` at the end of `end_session`. Writing it continuously is what keeps it from being a
race against the end-of-year delete — generating it at expiry would make the one job that destroys data also
the first to read it.

**The aggregation is a Postgres function (`rollup_signal_day`), not backend code**, because a day holds
thousands of samples and the reporting path caps its reads — averaging a capped subset in Python would be
quietly wrong, and this is the copy that survives the delete. It **recomputes** rather than accumulates, so
closing two sessions on one day, or replaying a close, converges; an incremental writer would have to be
exactly-once, which nothing here can promise.

`_rollup_session_days` **never raises**: it runs last in `end_session`, after the writes that matter, because a
failed summary must not cost a student their session record and stats update. It rolls up every school day the
session touched (two if it crossed local midnight), bounded so a corrupt `started_at` cannot spin.

Averages are over **trusted rows only** for heart and emotion, as the weekly report and the summary publish — an
untrusted reading is one the quality gate rejected, and averaging it here would smuggle it past that gate
permanently. `heart_sources` deliberately **includes** untrusted sources: its job is to explain a change in the
numbers, and a sensor whose readings were all rejected is exactly such an explanation. `trusted_sample_count` is
defined per channel (cognitive has no trust flag, so it counts rows that produced a measurement rather than the
nulled ones a poor-contact headband writes).

Its access rules differ from `retention_window`'s: the rollup carries a **read-your-own `SELECT` policy** and
`authenticated` keeps `SELECT`, unlike the per-sample tables, which no client reads. There is no insert/update/delete
policy for anyone, so with RLS on, PostgREST cannot write it whatever JWT it carries — the only correct writer
is `rollup_signal_day`.

## The term trend reads the rollup and nothing else

`/api/students/{id}/signal-trend` answers week-over-week averages, and is deliberately **not** built on
`_weekly_signal_report`. That one aggregates every per-sample row of its week in SQL (`weekly_signal_days`),
which is right only while those rows exist. The trend reads `signal_daily_rollup` because it is the only copy that
outlives `expire_signal_rows`, and a trend is the surface most likely to be read *after* a year ends; half a year
of it is a few hundred rows.

**Weeks are weighted by `trusted_sample_count`, and that is derived, not chosen.** `rollup_signal_day` writes
`avg(focus)` for cognitive and `avg(…) FILTER (WHERE trusted)` for heart; Postgres `avg()` skips nulls, so both
stored averages already have the trusted count as their denominator. Weighting by `sample_count` would divide by
rows the average never saw. A mean of daily means is the other wrong answer — it weights a 4-sample day like a
4000-sample one.

**`avg_stress` needs its own count.** The cognitive `trusted_sample_count` is
`count(*) FILTER (WHERE focus IS NOT NULL)`, focus and stress are derived independently, and the local calm's
hold rule made stress-absent-focus-present the *ordinary* row: a day of 4000 focus rows with 200 fresh calms
weighed its stress as 4000 and read 0.32 against 0.70. The rollup records `stress_sample_count`, and every reader
weights stress on it through `_stress_weight` in `main.py` and
`COALESCE(stress_sample_count, trusted_sample_count)` in the two cohort RPCs — the fallback is **per row**, for
rows rolled before the column, and is the old approximation on exactly the rows it always applied to.

`avg_rmssd_ms` still carries that approximation, because the rollup stores one count per channel and about one
trusted window in five is gated out of RMSSD. `avg_focus`, `avg_heart_rate_bpm` and `engagement` (served from
`avg_focus`) are exact. The error is between days, never within one.

**A week with nothing recorded is a gap, not a missing bar** — dropped, a fortnight off school renders as the
weeks either side sitting adjacent. Weeks are whole and Monday-anchored for the same class of reason: counting
back `weeks * 7` days from today leaves a part-week at each end that looks like a full one.

**Body arousal is the heart rollup's categories, pooled, and only the rollup has them.** `stress_counts` holds the
trusted windows per `heart_stress` category; the share is `high ÷ (low + moderate + high)`, pooled over the window's
counts (a mean of daily shares weights a one-window day like a full one) with `calibrating` left out. It is never
EEG stress and never combined with it. **It is not "time above resting":** `high` is a trusted window 10+ bpm above
the lesson's own opening baseline (`heart_stress`), and the copy says exactly that. The rollup is written as a session
closes, so an open session's day has no categories yet: the weekly report reads the heart rollup row even beside raw
rows. `_body_arousal` gives every surface (week, day, report, usual) the same `state` — `measured`, `calibrating`,
`pending`, `unusable` (headband rows, none classified), `camera_only` (rPPG gets no category), `none`, `unknown`,
`not_requested`, `not_retrieved` — with a share of None, never 0, for every state but `measured`.

**`pending` means a running lesson has heart rows the rollup has not counted yet** (`_running_heart_lessons`):
`sessions.ended_at IS NULL`, started inside `_SESSION_ABANDONED_AFTER_SEC`, with at least one heart row of any quality
since it started. It is never inferred from a day lacking a rollup row, and a lesson without heart rows is not pending.
On the weekly report a day's rows belong to the running lesson only from the school day it started.

**`unknown` is "could not check", never an absence**: the raw heart read or the sessions read failed, a lesson's heart
rows or `any_rows` could not be read, heart rows exist that neither the rollup nor the running lesson accounts for
(never summarised), or it is the trend's current week while any session is open — running, or past the abandon
window and not yet swept, since either may hold uncounted rows — or when that could not be checked (the trend sees
finished lessons only; its caller passes `open_session`, so `_signal_trend` itself reads only the rollup). A surface
that needs open sessions for several answers reads them once (`_open_sessions`) and passes them on.
The raw aggregate names trusted sources only, so poor-contact rows name no sensor; a surface that counts no usable
heart sample asks whether any row landed, in one read for all its students (`_heart_rows_many`), so the parent
dashboard and a child's own report give the same answer. `pending` beside `measured` is `true` for a running lesson with heart rows and `null` when that could not be
checked; `few_readings` flags fewer than `_AROUSAL_MIN_WINDOWS` classified windows. The pending sentence names no
sensor, since a running lesson's rows may be camera rows.

**A declined channel is filtered out of the query, not out of the result**: one `.in_("channel", …)` narrows the
single query, so a declined channel is never read and then dropped in Python. Assert on the **filter**, not
the payload. `_FakeSupabase` records every query it builds (`fake.queries`, each with `.filters`) so that
assertion is possible at all.

## A child is compared with their own usual

`/weekly-report` and `/signal-summary` carry `usual`: each of focus, calm, heart rate, heart-rate variability and
body arousal this period against the same student's prior 8 weeks, built by `_personal_baseline` from one named-column
read of `signal_daily_rollup`. Calm is `1 − avg_stress`; it is the same number as EEG stress, the right way up.

**One value per day, then the median and interquartile range of those days, unweighted.** The range is meant to show
day-to-day spread, so a 4000-sample day counts once, like a 60-sample one. It is widened to at least ±3 points (±2 bpm,
±3 ms) so a narrow history does not call a one-point move a change. A day is left out when it is in the current period,
thinner than `_USUAL_DAY_FLOOR`, on a different score scale (focus and calm), a different calm source (calm only) or a
different heart sensor class (heart measures). The floors and widths are judgement calls, not measurements.

**The verdict compares numbers as the tile prints them**, rounding halves up as `Math.round` does (Python's `round`
sends them to even), so 65.4% beside "usual 55–65%" reads as usual and a reader can check every verdict against the
printed range. It sets a period mean against a range of daily values, so it calls fewer periods unusual than a
like-for-like comparison would; that is the price of being checkable.

**The period's figure is counted the way it is averaged.** The floor compares the usable readings behind the figure
(`average_samples` on the weekly report, the summary RPC's counts), never all rows: a poor-contact hour is rows, not
readings, and the two endpoints must agree on "too little".

**Every non-comparison is its own status**: `not_enough_history` (fewer than 5 days, or fewer than 2 weeks),
`too_little_this_period`, `pending` (nothing in the period rolled up yet, so its scale or heart sensor is unknown —
never reported as a change), `not_comparable` (`mixed_scale`, `scale_unknown`, `sensor_changed`), `no_current`,
`not_requested` (heart declined, and then not queried) and `not_retrieved` — the rollup read failed, or, with reason
`current_unread`, the period's own figure did (rule 1: not a quiet period). An absent `usual` is an older payload. It is
attached in the handlers, not inside `_weekly_signal_report`, so the report's own query assertions still see exactly
the reads they did. The chart summary reads the rollup once for both its trend and its usual (`rollup=`).

`/api/parent/children` stamps `signal_summary.usual` through `_personal_baselines_many`: one read per heart-consent
group and chunk, so a sibling's consent never widens a read. `_usual_batch_size` derives the chunk from the window and
channel count so a full chunk stays under PostgREST's 1000-row cap; a chunk that fails, or comes back at the cap anyway,
is `not_retrieved` for its own children only.

## The teacher analytics aggregate in Postgres, and one of them is a table not a chart

Five surfaces: a class topic heatmap, class accuracy per school day, a weekday×hour heatmap, a real last-active
column on the roster, and focus-vs-accuracy per student. Three Postgres functions behind them, all
`SECURITY INVOKER` and `service_role`-only — the backend resolves who owns the class before calling, so they are
only ever as safe as the check above them.

**They aggregate in SQL for the reason `rollup_signal_day` and the weekly report do: PostgREST cuts every read at
`db-max-rows` (1000), silently.** A class of thirty answering fifty a day is 45,000 rows a month, so a Python-side
average would describe whichever slice the read returned while the rest read as a quiet term.
`class_answer_buckets` returns at most 720 rows for 30 days however busy the class, because it is bounded by the
*range* rather than by the answers.

**One function serves both the day trend and the time-of-day heatmap**, since they are two readings of one
grouping. It is still called once per endpoint rather than cached between them, so a failure in either cannot
blank the other.

**`last_active_for_users` exists because "newest row per student" has no PostgREST form.** One `in_` query ordered
by time returns the newest rows *overall*, which is one busy student's — the same limitation
`_recent_sessions_many` exists for. That is why the column was absent rather than wrong. It is the greatest of two clocks
(`started_at` and `max(answered_at)`) plus the newest session's newest sample, **never `ended_at`**: a sweep
stamps the last activity it could read, but the sweep time when that read fails, and nothing records which. The
sample is the newest session's only, so each read is one `(session_id, ts)` index lookup rather than a year of
rows per student. **Three states on the roster** — a
timestamp, `null` for never active, and `last_active_retrieved: false`. Collapsing the last two tells a teacher
the class has stopped working, which is both wrong and something they would act on.

**`focus_accuracy_for_user` pairs each answer with the nearest focus reading in the same session**, within
`_FOCUS_MATCH_SECONDS`. Same *session*, not same student: a reading from another session is a different lesson on
a different day. An answer with no reading in range is **dropped, not counted as focus 0** — sessions run with the
headband off, and a zero would drag exactly the unmeasured answers to the bottom of the correlation (rule 2).

**The correlation is withheld below `_FOCUS_MIN_PAIRS` (30) even when the database computed one.** `corr()` needs
two pairs and will happily answer from them; r over a dozen answers is noise, and it reaches a teacher as a single
objective-looking number with no visible denominator. The *buckets* are still returned below the threshold — a bar
chart of five bins shows its own sample sizes in a way a scalar cannot. Keep three outcomes apart: too few pairs,
enough pairs with a null `corr()` (no variance — every answer correct), and a real coefficient.

**EEG consent skips the query, and the test asserts on the RPC call rather than the payload.**

**`Heatmap.jsx` is a real `<table>` and deliberately not an `AccessibleChart`.** That wrapper exists because
Recharts emits a bare `<svg>`; a matrix has no series to plot — it *is* the table, and `<th scope>` headers let a
reader ask for one cell by its two headings, which the sr-only copy could not. The shading is an enhancement on
top of real markup.

**A cell has three states and two of them look alike in colour**: a number, `null` for a topic never attempted
(not shaded, and *not* the zero colour — a topic nobody was served is not one they failed), and a real 0%. Below
`min_attempts` the figure is still shown and marked thin; what is withheld is the confidence, since one answer
colours as strongly as four hundred.

**The topic grid's `cells` are a list aligned to `topics`, built server-side in one pass.** Two independently
ordered lists is the drift `AccessibleChart`'s single `columns` spec exists to prevent; don't re-sort either end.
The time-of-day grid aligns in the *browser* instead, because that payload is sparse — an hour a given day never
used is a real absence, not a missing row.

## The cohort panels weight across students, and the roster floor is a server-side gate

`GET /api/classes/{id}/cohort-signals` answers a class-wide signal trend and the per-student rows behind it, rendered
by `ClassSignalTrend` and `ClassSignalRoster`. One endpoint for both, because they answer one question together and
two fetches could disagree about when they were taken.

**Consent buckets the roster before anything is read.** `_reportable_channels_many` for the whole roster, then grouped
by `(heart, emotion)` flag pair — at most four calls. One call for the whole class would either read a declining
student's heart rows under a classmate's permission or hide the channel from everyone who allowed it. The RPC does
**not** filter consent itself, so passing it a mixed roster with one flag pair is the way to get that wrong. Heart is
`headband_optical OR camera` — the camera carries the rPPG fallback — so a test that declines the headband and leaves
the camera on has not declined heart.

**Weighted on `trusted_sample_count` at both levels, and the second is the one to watch.** The RPC weights across
students within a bucket; `_merge_cohort_trend` then weights across *buckets* in Python, because the buckets exist for
consent reasons and re-averaging their means would weight a bucket of one like a bucket of twenty. Verified against the
applied migration: two students on one day answer **0.7714**, where `avg(avg_focus)` answers 0.5000. A null daily
average contributes no weight rather than a zero — its count is still real, so it stays in `trusted_sample_count` while
staying out of the numerator's denominator, or a day of poor electrode contact drags the class below every student in
it.

**`_COHORT_MIN_STUDENTS` (5) is enforced in the backend, and gates on the roster.** Below it `per_student` is `null`
and the rows are never built — a client-side hide would leave them in the payload for anyone reading it. It counts the
roster, not the students who recorded something: a class of six where two wore a headband is still a class of six, and
gating on the smaller number would expose that pair exactly when they are most identifiable. The class trend still
renders; it is the aggregate the floor exists to protect.

**`_consent_many` / `_reportable_channels_many` are the batch forms**, used wherever a page reads more than one
student — the cohort roster and `my_children` alike — since the single form is one read per student. They fail closed exactly as `_consent` does and *per student* — a failed read
denies **every** requested id with `retrieved: False`, since none of them was found out, while a student with no row
denies with `retrieved: True`. `_channels_from_consent` is the shared pure mapping, so the single and batch forms
cannot drift and disagree about the same student on two pages.

**One failed bucket fails the whole trend, and takes the roster with it.** Breaking out of the bucket loop skips that
bucket's totals call and every later one, so leaving `summaries` as `{}` reports students nobody asked about with zero
counts and `retrieved: True` — "recorded nothing", which renders as `No sensor` and is indistinguishable from a class
that left the headbands in the cupboard. Both flags go false together. The two reads still fail *independently* in the
ordinary case: a broken totals RPC leaves the chart standing. **And the row's `retrieved` has to be read by the tile,
or that fix stops at the API boundary** — without it the row arrives correct and renders `No sensor` anyway, from the
zero counts an unread row carries. The day count needs the same guard: `0` asserts the student recorded on no day at
all.

**`cellLabel` orders it, and the order is not the order the flags arrive in.** Consent unreadable first, then **a known
revocation, which beats the unread row**, then unread, then the sample count. The consent fields come from `channels` —
a *different query* from the totals RPC — so when that read fails the revocation and its date are still fully known;
reporting the outage there discards a fact we hold for one we do not, and `Unavailable` implies a retry might yield a
number that can never appear for a revoked channel. Folding `retrieved` straight into `consentRetrieved` gets this
wrong and looks right, because both spellings produce the correct answer in the two simple cases. **Four states means
the tests have to cover the *compound* cases**, not each flag alone; and a fixture built from a happy-path helper has
to null *every* average, or a leftover default renders a number where the test expects a reason.

**Both panels read the rollup, and that is load-bearing rather than tidy**: `expire_signal_rows` deletes the per-sample
rows and leaves the rollup standing, so a per-sample panel beside a rollup one shows a term of class averages above
"No sensor" for every student, on a fixed date. `student_signal_summary` reads a past day from its rollup row too,
while the row is whole — no session reaching the day still open, or closed after the row was written — and from raw
rows otherwise, back to the row once they expire. `class_signal_student_totals` is the same aggregation as its sibling, grouped by
student rather than by day. `test_the_roster_reads_the_rollup_and_never_the_per_sample_tables` asserts on the tables
that must **not** be read — the two sources look identical while both hold the same data, which is every day of a
school year except the ones after expiry, so nothing about the numbers can see this. **The roster counts days
recorded, not sessions**, for the same reason: a session count comes from `sessions`, a table with a different
lifetime.

Its outlier flag compares against an **unweighted** class mean, deliberately a different number from the trend's
weighted one: this asks "is this student unusual among their classmates", where each classmate is one comparison
whatever their session length. Using the weighted figure would flag a student for sitting next to someone who recorded
all afternoon.

**A conditional `<Line>` needs a conditional column, and the sentence cannot test it.** Assert on `columnheader`.

Both panels honour `viewPrefs.js`'s "Hide sensor data"; the academic panels beside them do not, because that switch
hides sensor data and those measure answers. **It gates every series, not the heart one** — focus, stress and
engagement are EEG-derived and are as much sensor data as a bpm is. Gating only heart left the cognitive lines drawing
real values under a note reading "sensor data is hidden", stating the opposite of what the panel is doing. A test
asserting on that note passes either way; assert that no chart, no `sr-only` table and no numbers are on screen.

## Every session close goes through `_close_session`

**Four close sites** — `/end`, the stale-session sweep in `start_session`, `class_live`, and the background
`_sweep_abandoned_sessions` thread — and `conftest.close_sites()` finds all four. Don't hand-write a fifth: a copied
close sequence drifts silently — a credit reading a column it never selected (`None` → `or 0`, an honest-looking
zero), a discard one site never runs.

**The three sweeping sites stamp when the student was last seen, not when they noticed**, all through
`_last_seen_many` (the `last_activity_for_sessions` read, measured rows only — not `class_live`'s newest reading,
which counts an empty one): the start for a session with no activity, now only when the read fails.

**Order is load-bearing: discard first**, because a rollup of nothing and an archive of four empty charts are work
done for a session about to stop existing.

**`_close_session` stamps `ended_at` itself, and the stamp is a claim.** Stamped at a call site, it can land before
the poller stops, so a tick inserts a signal row after the discard check looked; and an unconditional stamp lets two
raced closes — a delayed `/end` against the sweep — both credit the session's *cumulative* counts, landing every
answer twice in the lifetime totals. `/end`'s read of
`ended_at` is not the guard; that read and the write are two statements. `_claim_session_close` is:
`is_("ended_at","null")` matches at most one row, and the rows the update returns decide: none means another close
won. **It asks for them by name** (`returning=representation`): under `minimal` every result is empty and every close
would skip its credit, rollup and archive, so the default is not trusted.

Stopping the poller stays at the call sites — it takes different ids at each — and **before the call** is the whole
of the ordering rule, pinned by `test_every_close_site_stops_the_poller_first`.

**The credit recounts from `session_answers`.** `questions_answered` is a denormalised cache written in a separate
statement from the answer row, and `_discard_if_nothing_recorded` already distrusts it. The credit did not, so a
session correctly *saved* from deletion by that re-check was then credited zero and the student's work never
reached the lifetime totals — permanently, since no later close revisits a stamped session. `_answer_counts` only
ever revises **upward**: rows fewer than the counter means a short read, and crediting less than a previous reading
loses work.

`_close_session` takes `closed_by`, defaulting to `CLOSED_BY_STUDENT`. The function cannot tell which site is
running and the difference is the entire content of the `session_auto_closed` alert. It defaults to the student so
a new site has to opt *in* to raising one — a wrongly-raised alert is worse than a missing one on a surface whose
value is that every row means something happened. `test_every_close_site_says_who_ended_the_session` partitions the
sites: each is either in `STUDENT_DRIVEN_CLOSERS` or must pass `CLOSED_BY_SWEEP`, so a new closer fails until
someone classifies it. No property of the source separates them — `/end` and both sweeps stop the poller and call
the same helper — which is why the list is by name.

**`close_reason` records why, in the same claim as `ended_at`**:
- `/end` takes the page's reason: `finish`, `sign_out` or `page_closed` (pagehide, or in-app navigation away). A page naming none is `student`, never a guess.
- The sweeping sites pass their own: `superseded` (`start_session`), `live_stale` (`class_live`) and `sweep`.
- `test_every_close_site_says_why_the_session_ended` maps each site to its reason, so a fifth site fails until classified.
- A null reason means the session closed before the column existed. It is never back-filled.
- A discarded session's row is gone, reason and all, so it is counted as `session_discarded:<reason>` in the ops counters instead.
- A claim that fails is never retried without the reason: a close is stamped with why, or not at all.
- A failed claim is `None`, not `False` (another close won): `_close_session` returns `failed`, `/end` answers
  503, and the sweep counts it failed. Reported as "already closed", an unmigrated database would end nothing.

The exhaustiveness tests share `tests/conftest.py:close_sites()` — a closer is a function that calls
`_close_session(` **or** writes an `"ended_at":` of its own. Both halves matter: the first catches a site drifting
away from the helper, the second catches a new site that hand-rolls a stamp. A second test pins the helper's own
contents; the indirection is only safe while both halves exist. **They catch a step being removed, not neutered.**

**Don't put a literal end-stamp key in an alert payload** — that scan reads such a key as a fourth close site,
which happened, and then happened again in the comment explaining it. The timestamps are columns on the session the
alert already points at, so `detail` carries none.

### Abandoned sessions, and two surfaces that were lying about them

Both original sweeps are **on demand** — `start_session` collects a student's strays when they next start one,
`class_live` collects a class's when a teacher opens the monitor — so a student who never comes back is collected
by neither. **`start_session` leaves any session active within `_STALE_AFTER_SEC`**, its start counting, and closes
nothing when activity is unreadable: that may be a lesson live in another tab, and closing it makes that tab's next
answer start a new session that closes this one in turn. A tab's own lesson is not left to that guess:
`Adaptive.jsx` ends it on `pagehide` (a keepalive `/end`), which a reload or a close fires and a duplicated tab does
not — never on the next load, where a copied or stale id would end someone else's. **What is left open is not
free** — uncredited and "in progress" until a later start finds it quiet or the 6 h sweep closes it: a page that died
without `pagehide` or lost the keepalive, one hidden while an answer was still saving (ending it then could refuse
that answer), or a live tab whose only readings for 10 min were empty (a loose headband).

`_sweep_abandoned_sessions` is the third, run from a background thread started in `_lifespan`. **It is a backend
thread, not a `pg_cron` job, and that is not a preference.** Closing a session credits lifetime totals, writes the
daily rollup, archives four charts and raises the alerts; SQL can do none of it. A cron job stamping `ended_at`
would be a fifth close site that skipped all of it.

`_SESSION_ABANDONED_AFTER_SEC` (6 h, `SESSION_ABANDONED_AFTER_HOURS`) is **an age, not an idleness**, and the flag
is named for what it can support. A two-hour session with a student answering throughout is not abandoned by this
measure and is correctly untouched; `class_live` keeps its own much tighter `_STALE_AFTER_SEC` computed from real
last activity, and applies it only to a session with sensor rows: with no sensor, a student reading one question
sends nothing, so silence proves nothing and the session waits for this sweep. This one only has to catch the
session nobody has touched since June, so it errs long — closing a
live one would discard the question a child is part way through answering. `STALE_SWEEP_INTERVAL_SECONDS=0`
disables it. **The same pass closes idle practice sessions** (`_sweep_abandoned_practice`), which nothing else ends:
open past `_SESSION_ABANDONED_AFTER_SEC` *and* no answer or view for as long, closed at that last activity through the
end route's own `_close_practice_session`. Idleness, not age, because a closed one refuses the next answer with a 409.
The practice list's `abandoned` flag reads the same `_practice_idle_since` (null if that read fails); the adaptive flag is the age alone
(`_is_abandoned`). Each pass resumes from an id cursor, so sessions it keeps skipping cannot hold the batch.

Safe in several workers at once via `_claim_session_close`. **The thread must be joined**, like the pollers — it
prints, and a print during interpreter shutdown is a fatal stdout-lock abort.

Two surfaces were asserting things the data does not support. `Sessions.jsx` decided `live = !ended_at`, so an
abandoned session rendered a *pulsing* `● LIVE` badge indefinitely — three states now (live, `never ended`, done)
with `abandoned` derived in `_flag_sessions` so the threshold has one definition rather than a second copy in the
browser. And its duration counted to `Date.now()` for open sessions, printing `83132m 45s` for a student who left
within the hour; an abandoned session shows a dash, because we do not know when it ended.

## Session alerts are operations, never a judgement about a student

`session_alerts` is a teacher-facing feed of things that went wrong with a *session*: `session_auto_closed` (the
stale sweep ended it, the student did not) and `signals_missing` (EEG recording was permitted, a headband was
started — `sessions.eeg_started_at` — and no cognitive row arrived; a session with no headband is not a fault).
Under pull `/api/eeg/start` stamps it. Under push the backend sees no start, so `Adaptive.jsx` reports one through
`POST /api/sessions/{id}/eeg-started` once the sidecar holds the session and a headband streams. **That report is
the client's claim**: a student can stamp their own sensorless session and earn it a false alert. Accepted, since
the alert is about that session only and gates nothing. Read at `GET /api/classes/{id}/alerts`, rendered by
`AlertFeed`. The report also carries `kit_version`, the student kit the sidecar names on `/healthz`, stored on
`sessions.kit_version` with `kit_reported_at` (that the sidecar answered, with a version or with none) for the admin
Sensors kit page: equally a claim, and it gates nothing either. The page sends `kit_version` only once the sidecar has
answered (`null`: an older kit); a read with no answer is retried on the stamp's backoff (`useEegStartReport`), and
until then the lesson counts for nothing, never as "no version". The body never 422s (`EegStartedReport` is lenient,
like the ingest models): a page from before it sends none, and a value the handler cannot read counts for nothing,
since a refusal would lose the stamp.

**The scope is the feature.** `signal_fusion` produces a `stressed` label that no teacher surface consumes, and
routing it here was considered and rejected: it is an inference from signals this codebase already treats as weak,
and a timestamped event reads as more objective than a tile does. That is what retired `identity_confidence` and
the `attention` surfaces. **Every kind in the CHECK whitelist is checkable against the database without
interpreting a person; keep it that way**, and if that ever changes it needs a labelled reference first, not a
column.

**`_raise_session_alerts` runs after the discard and never raises.** After, because an alert about a session about
to be deleted goes with it on the cascade, and an empty session is not a fault worth anyone's attention. Never
raises, because it runs after the credit and the rollup and a session's record must not be lost because a
notification could not be filed.

**`signals_missing` gates on `_may_record`, not `_consent`, and on `is False`, not falsiness.** A student who
declined the headband is working exactly as configured; so is one whose school year has ended or whose recording
flag is off. Alerting on any of those trains a teacher to ignore the feed, and the first would leak a consent
decision as an incident. And `_session_had_signals` answers `True`/`False`/`None` — `None` is a failed count, which
must not become an accusation that recording is broken.

**A `try/except` around `_may_record` catches almost nothing, and that is the trap.** Both helpers behind it fail
closed by *returning*, not raising: `_consent()` answers `retrieved: False` and `_retention_window()` answers
`WINDOW_UNREADABLE`, and `_may_record` spreads both straight through as `record_*: False`. Read as a plain bool, an
outage is indistinguishable from a student who declined — and the outage is the likelier of the two.
`_recording_was_expected` reads `retrieved` and `window_state` rather than inferring from the composed answer, and
returns `None` for either.

**This generalises: anywhere a `record_*` flag decides whether to report a fault, the `False` is three different
facts.** Withholding is right for the unknown one — nobody can act on a database blip — but it has to be *logged*,
because that branch has no other trace. And the test has to assert on the log: the outcome is identical either way,
so a test checking only that no alert was raised passes against the bug.

**No acknowledge or dismiss, by decision.** Both kinds are about a session that has already ended, so there is
nothing to resolve; dismissal implies a triage workflow this product does not have, and the seven-day window
already bounds what is on screen. **An unrecognised `kind` renders as a visible unstyled row**, never dropped — the
CHECK makes it near-impossible, and if it happens a visible row is what gets it reported.

## Archived charts are the other thing that survives the delete

At every session close, `chart_archive.schedule()` renders the session's four charts to standalone SVG
(`chart_render.py`) and uploads them to the private `session-charts` bucket. With the rollup, these are what is
left of a school year once `expire_signal_rows` has run. **The cognitive archive draws EEG stress, not calm**, as every
stored one does (`ARCHIVE_ONLY_SERIES`); the app draws calm, and `test_chart_render.py` holds the two palettes apart.

**Off the request path, and it never raises.** A storage failure must not cost a student their session close — the
session row, their stats and the rollup are all written by then. So the work goes to a two-worker pool and
`schedule()` swallows even a submit failure. That makes the log the only place a failure can surface, and it *has*
to surface: the window in which an archive can still be rebuilt closes on `ends_on`.

**`chart_paths` has four states and no column default.** A path, `null` for a channel that produced nothing, an
absent key for a chart never attempted, and column-NULL for a session the archive never ran on. `'{}'::jsonb` would
claim every pre-archive session was archived and found nothing, and `scripts/assert_signal_rls.sql` fails if a
default appears. **Column-NULL is retried; a session begun on or before the expiry cutoff never is** —
`archive_missing`, the stale sweep's second step, since part of a session read back would be archived as all of it.
**An archive and an erasure each have to see the other**: `erase_signals` takes a per-student advisory lock
first, the archive writes `chart_paths` only through `record_chart_paths` (the shared form of that lock), then
re-reads `signal_erasure`. Drop any of the three and an overlapping pair keeps erased charts. Advisory, never a row
lock on `sessions`: answer saves and `/end` update that row and would stall for the whole erasure.

**Nothing has a policy on `storage.objects`, deliberately.** RLS is on and no policy grants any role anything, so
only `service_role` reads or writes — not even the student the chart is *about*: an object is fetched by URL, not
filtered by a query, so the access decision belongs in the backend where the relationship checks are, handed out as
a short-lived signed URL. The bucket is private for the reason no policy can fix later: **a public object URL, once
pasted anywhere, cannot be un-shared.** All of that is asserted against a real stack in CI.

Two smaller traps: the archive draws **untrusted rows too**, unlike the rollup, because it is a picture of what the
reviewer was shown rather than a number outliving its evidence; and `upsert` in `file_options` must be the
**string** `"true"` — storage-py passes those through as HTTP headers, so a bool arrives as `True` and a replayed
close 409s instead of overwriting.

**Reading them back is `GET /api/signals/session/{id}/charts`**, which resolves whose session it is, applies
`_verify_can_view_student`, and issues a signed URL per recorded chart with a 300 s TTL. Three states stay apart in
the payload, and a surface saying "no charts" has to consult all three: `archived: false` (the archive never ran),
`charts[name]: null` (that channel drew nothing), and `name in unavailable` (a path was recorded and the object
could not be read). It has deliberately **no `retrieved` flag** — unlike the reporting helpers it raises rather than
degrading, so a flag that is never false would be a state that does not exist.

**The object path is derived there, never read out of `chart_paths`.** That column is ordinary jsonb on `sessions`,
which carries a `FOR ALL` own-row policy — so a student could PATCH their own session row through PostgREST, point
it at another child's object, and the endpoint would sign it, having just correctly confirmed they own *this*
session. The stored value records **which** charts exist; it is not an address. The migration revokes the write as
well, but the endpoint must hold without it — a grant is one migration away from being widened back. The
consequence is that changing `object_path`'s scheme means migrating the objects, which was already true.

**A signed URL cannot be revoked.** It stays valid until it expires whatever happens to consent in between, so the
TTL is the only bound on a leaked one — that is the argument for keeping it short, not convenience.

**Storage does not cascade, so a deleted session orphans its SVGs — `sweep_orphan_charts.py` collects them.** There
is still no delete endpoint in `main.py`, which is exactly why a sweep rather than a hook: those deletes come from
the dashboard or a direct connection, where the backend never runs. Run it to report; `--apply` deletes.

**It deletes on *absence*, which is the dangerous kind of job**, and the guards are the point rather than the
sweeping. One failed read of `sessions` makes every object look orphaned, so: the read failing **refuses** instead
of proceeding, more than `max_orphan_fraction` (default 0.5) looking orphaned refuses, a path that is not
`{uuid}/{uuid}/…` is left alone, and `dry_run` is the default. **The bucket is listed *before* `sessions` is read**,
and that order is a guard too — read the table first and a session created in between has objects whose id is
missing from the snapshot, deleted as an orphan while its row sits there. Listing first can only be stale in the
safe direction. Each guard has a test that fails with that guard alone removed; the fraction guard also refuses a
failed read, so the read-failure test has to tell the two refusals apart.

An orphan is not a leak — `/charts` resolves the session row before signing, and the bucket has no policies — so
this is storage that should not exist rather than data anyone can reach. It stops being fine when account deletion
becomes a feature. **Objects for a session that still exists are out of scope on purpose**: `expire_signal_rows`
leaves the archive standing deliberately, and a sweep that "corrected" that would remove the thing that makes a
same-day delete defensible.

## The answers table shows the topic and the option text, never the ids

A `session_answers` row carries a question *id* and a `selected_index`, and `SessionReview` rendered exactly that —
a truncated uuid and the bare number `2`. Both true, neither usable.

`/api/signals/session/{id}` embeds the question on the answer
(`select("*, questions(question_text, options, correct_answer, subject, difficulty, figure, ccss_standard)")`) — one
query however many answers, named columns so a later addition to the bank does not start reaching the browser.
**A read path that names its columns has to name every column a surface renders**, which is how `figure` and
`ccss_standard` each had to be added; `/api/questions` uses `select("*")` and needed nothing. **PostgREST
left-joins the embed**, so an answer whose question has since been deleted arrives with `questions: null`; the
answer still happened, so the row is still shown and says why it cannot expand.

**`questions.correct_answer` is text, not an index.** Comparing it against a position marks the wrong option on
every question whose answer is not stored in order, so `isCorrectOption` compares *values* (trimmed,
case-insensitive) with a numeric fallback for a row that holds an index anyway. And `options` is unschema'd
`jsonb` — `optionList` accepts an array or an object and yields `[]` for anything else, which renders as "options
were not recorded" rather than crashing on `.map`. Chosen and correct are spelled out as words next to the glyph,
not left to colour and a `✓`.

## An answer is recorded by the backend, and the topic comes from the question

`Adaptive.jsx` had no `/api/sessions/{id}/answer` call at all — only `Practice.jsx` did — so every question
answered on the adaptive path was counted in `localStorage` and nowhere else. `session_answers`,
`sessions.questions_answered`, `user_stats` and every report built on them read zero however long a student
practised, while the page's own Topic Accuracy panel showed figures: two records of one afternoon, one of them
private to a browser.

**The question id is what made it possible.** `add_question_to_supabase` returned a bool, so the generated question
reached the page with no id and there was nothing to put in `session_answers.question_id`. It now returns the id —
**and returns the existing row's id on a duplicate** rather than False, because answering a question the generator
has produced before is exactly as real as answering a novel one.

**An answer is one call: `record_answer`.** Under a `FOR NO KEY UPDATE` lock on the session row it refuses a
missing, foreign or ended session before any write, then writes the answer and runs `bump_session_counters` and
`record_topic_attempt`, each in its own exception block, so neither failing undoes the answer. `main.py` maps the
outcome, `forbidden` through `_session_or_403` so it records `authz_denied` like every other refusal. A failed call is
a 503, never a silent drop; deployed ahead of the migration, every answer fails and the log names it. A question
expired since it was served fails the answer's foreign key and is a **410, never a 409**: `recordAnswer` reads a 409
as its session ended and opens another.

**A failed side step is a 200, so the log is its only symptom.** It comes back as `topic_error` or `counters_error`
and `_log_answer_side_errors` prints it by name; a missing function (42883) names its migration. That is the
deploy-ordering trap in its worst form: attribution stops and nothing shows but the numbers not moving. Its tests
assert on the log line, since the response is identical either way.

**The topic comes from the question row, never from the caller.** The client has to be trusted about correctness;
letting it also name the topic would let a page credit one subject for work done in another, and
`user_math_performance` is what the adaptive engine reads to choose what to serve next. `record_topic_attempt` is one
`ON CONFLICT DO UPDATE` incrementing the *stored* counts, so two answers together cannot lose an attempt, and it
returns the topic **name** for the page. The arithmetic and every refusal are asserted in
`scripts/assert_signal_rls.sql`; the backend suite drives a fake and can only check the call and its mapping.

**Topic accuracy is read from `user_math_performance`, not from the browser.** It was
`localStorage.accuracyStats_<uid>` — the only panel whose numbers were not the database's. It disagreed with the
dashboard on the same screen, started from zero on a school computer, and nothing server-side could correct it: a
parent erasing a channel left the figures standing in the child's browser. The client-side upsert is **deleted, not
merely unused** — the backend owns that table now, and a client upsert would overwrite real counts with one
browser's memory. Its `Number(v) || null` also turned every genuine zero into a null (rule 2), which is why the
table sat empty while the panel showed numbers.

**There is no "Reset stats" button any more.** Against localStorage it cleared a browser key; against
`user_math_performance` the same button deletes a student's academic record with one click and no confirmation.
Erasure here is a parent-only, confirmed action.

### A roster surface reads once for the roster, never once per student

`_profiles_many`, `_topic_performance_many`, `_open_sessions_many` and `_stats_including_open_session_many` are the
batch forms; `class_students`, `my_children`, `class_live` and `leaderboard` use them. The stats half was batched
first and the profile lookup was left in the loop beside it, which is the shape to watch for. **"Top N per student"
has no PostgREST form** — one `in_` query returns the newest N overall, which is one busy child's — so
`_recent_sessions_many` (`recent_sessions_for_users`) serves `my_children` and `class_sessions` in one call. It raises,
and each caller sends `None` for sessions it could not read, never `[]`, which would say "no sessions".

## The two model-backed panels on a report page

Both follow the same shape: a deterministic answer that is always available, a feature flag deciding whether a model
gets a chance to replace it, and the four bounds. **Both are on demand and never auto-fetched** — a teacher opening
a class of thirty would otherwise spend a model call per page.

### Strategies

`/api/students/{id}/learning-strategies` always has a rule-based answer; `strategy_llm_enabled` (default **on**)
only decides whether a model gets a chance to replace it. Off, the endpoint never opens a socket — which is what CI
and any deployment without a local Ollama should do. Every failure path degrades to the rules rather than erroring.

The migration that turned the default on also flipped the already-seeded live row, **guarded on there being no
recorded `feature_flag_changes` row for the key**, so a deployment where an admin turned it off stays off. On still
needs a working provider: `_llm_strategies` catches every exception and falls back, so a misconfigured provider and
the flag being off look identical — every response `source: "rule-based"`.

**Tests must pin this flag explicitly, not rely on the suite's default.** The autouse `_feature_flags_are_default`
fixture reads live from `_FEATURE_FLAG_DEFAULTS`, so flipping the production default flipped it for the whole suite
in one step — several tests started actually attempting the model call, one via a genuine ~20 s
`STRATEGY_LLM_TIMEOUT` wait per test. Every test whose point is the rule-based path, access control, or rate
limiting pins it off, even where the assertion happened to pass either way.

Model output is untrusted text: parsed, length-bounded, stripped of markdown emphasis and list markers, and run
through a clinical-term filter and a "stress" filter (calm is not a stress measure, the same `_STRESS_WORD` the
chart summary uses), with anything failing validation falling back to the rules. Extend
`_validated_strategies` rather than rendering raw output.

**The panel is on the teacher report as well as the parent one, and the copy is the only thing that differs.** The
endpoint is gated on relationship rather than role, so a teacher could always ask for this advice and had no way to
see it. `viewerRole` ('parent' by default, and for any value the panel does not recognise) picks the framing;
`_llm_strategies` and `_validated_strategies` are untouched. **The heading stays "At-Home" on both**: the prompt
says *"you are helping a parent support their child's maths practice at home"* and the rule-based fallback says
*"ask your child to explain one solved problem out loud"*, so a classroom-sounding label would claim the model had
been asked for something it was not.

On the teacher page it is **behind "Hide sensor data" with the charts**, because the advice *is* sensor data in
prose — the rule-based list says *"calm readings ran low this week"*, and the model pass is handed the same
averages (calm, as the tiles show it, never "stress"). The whole panel goes rather than its individual lines: the advice mixes topic accuracy with signal
readings and nothing downstream can separate them, and asking the endpoint for a signal-free list would change the
advice rather than hide it. **Assert on the Generate button's absence, not the heading** — hiding a heading over a
live button satisfies a heading check and none of the point.

### Chart summary

`POST /api/students/{id}/chart-summary` describes a student's report charts in plain sentences, flagged by
`chart_summary_llm_enabled`. **That is the fourth copy of the bounds block** — ingest, generation, strategies,
this — and consolidating the four is a standalone change rather than a rider on a new endpoint, because the other
three are reached into by name from their tests (`main._strategy_hits`, `main._STRATEGY_LLM_POOL`).

**The model is handed the finished sentences, not the aggregates.** It is asked to rephrase, never to interpret, and
that is what makes numeric fidelity checkable at all: every number it may use is already in front of it, so one that
is not is an invention. `_validated_chart_summary` rejects the whole reply on any numeral not in the allowed set.

**The allowed set is read out of the deterministic sentences, never enumerated from the basis fields.** Enumerating
was the first shape and rejected *correct* replies in two ways a reader would not predict: the sentence prints a
rounded heart rate where the basis holds a fractional one, and a revocation date puts a day number on screen that no
basis field carries. Reading the text the prompt actually sends closes the whole class, and makes drift between the
two impossible — the same reason `AccessibleChart` drives its sentence and table from one spec.

**The summary says calm, and a reply may say "stress" only where its point did.** The EEG figure is stated as calm
(`1 − stress`, trend computed on calm); body arousal is its own sentence with the excitement/effort/movement caveat,
and the comparison with the child's usual is in words only, so it adds no number. `_validated_chart_summary` rejects a
reply line that says "stress" where the matching rule-based line does not. `basis.sensor_lines` indexes the sensor
sentences so "Hide sensor data" can drop exactly those, and the validator enforces the order that relies on: each
reply line may use only its own point's numbers, and is a sensor line exactly when its point is.

**What it still does not check is a number's meaning within one point.** A reply that relabels a figure inside its
own sentence uses only that sentence's numbers and passes. That is the residual hallucination risk on this endpoint and it
is not closed; closing it means parsing the reply back into measurements, which is a second implementation of the
sentences being parsed.

**The reply must have exactly the baseline's number of points.** A range let a reply drop one silently, and the
likeliest one to go is the channel-absence sentence — the single point whose whole job is to say something is
missing.

Four reads sit behind one response (the weekly aggregate, the rollup-backed trend, the academic totals, the topic
figures) and **each reports its own `retrieved`**. Collapsed into one, a summary missing only its trend sentence is
presented either as entirely fine or as entirely broken. Three consequences that were bugs first: `sessions` comes
from the *signal* aggregate, so a failed signal read leaves it at 0 and printing it reports a quiet week for a query
that never ran; an empty trend is indistinguishable from a student's first week, so a failed trend read must not say
"only one week so far"; and `_topic_breakdown` swallows its exception and answers `[]`, which most callers degrade on
identically — the strategies endpoint falls back to generic advice — but which here becomes the *assertion* "no topic
has been attempted yet". **`_topic_breakdown_with_state` is the form that reports the read**, split out rather than
added as a parameter so a caller that did not know to ask for the flag cannot drop it; reach for it wherever an empty
list would become a claim.

**Zero weeks and one week are different facts, and `_trend_direction` returns a dict for both.** It answered `None`
for each, so a student part way through their very first session — raw rows, so a focus average, but no rollup row
yet, so no week at all — was told that one week had readings. The rollup row is not written until the session closes,
so that state is ordinary rather than an error. **A helper that computes a count and returns it only on the success
path cannot be asked the question the count answers.**

**But the sentence for it names no cause.** A first session is one way to reach zero
weeks; a rollup writer that failed on every day in range is another, and so is a set of rolled days all carrying null
for that series. The read succeeded in all three, so nothing can tell them apart — and *"from this session's own
readings"* contradicted the session count two sentences above it whenever one of the others was the real one.
**Where a branch exists precisely because the code cannot establish a cause, its sentence may not supply one**; state
the observable ("no week has a reading for it yet") and stop.

Channel absence is ordered as `cellLabel` is on the cohort roster and for the same reason. `engagement` is never
named — it is the focus index, and a sentence naming both describes one measurement as two agreeing ones. The heart
channel gets **no trend**: `_CHART_SUMMARY_TREND_MIN_DELTA` is written for the 0..1 ratios focus and stress are
stored on, and against bpm the same number is a twentieth of a beat.

`ChartSummaryPanel` mounts on both report routes, and on the teacher route is behind *"Hide sensor data"* with the
charts — a stronger version of the reason the strategies panel is: that list mentions sensor readings in passing,
where this panel's whole job is to state them.
