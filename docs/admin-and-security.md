# Admin and security

Part of the AdaptiveLearning conventions; `CLAUDE.md` is the root and holds what binds here as much
as anywhere — the canary, the four numbered rules, access control, consent, and the admin role.
Read those first.

**Read this file when you are touching** an `/api/admin/*` endpoint or an admin page, the security
log (`security_events`, `_record_security_event`), the operational counters (`ops_metrics`, `ops_counters`),
the 403 a new endpoint answers, or a rate limiter (`_SlidingWindowLimiter`) or a test of one.

## The admin read surfaces send counts and timestamps, never readings

`/api/admin/live-signals` answers "is data arriving" for every open session. **It reads timestamps alone**
(`latest_signal_ts_for_sessions` returns `session_id, ts`), so the readings never leave the database rather
than being fetched and dropped on the way out. An admin has no relationship to those students entitling them
to the values, and asking for less is a stronger version of that property than filtering afterwards: the test
asserts that no signal table is read directly, which is the only place the difference shows.

It shares `_LIVE_WINDOW_SEC`/`_STALE_AFTER_SEC` with `class_live` — two sets of numbers would let one page
call a session live while the other called it stale — but **not its row reads.** `class_live` reads the
newest row per channel for the whole roster in **one** `latest_signals_for_sessions` RPC
(`_latest_signals_many`); this endpoint makes **one call per channel** for every open session, so a failed
channel reads unreadable while the other stands. Keep both set-based, never a per-session read fanned into a
thread pool: callers waiting on work queued in the same pool **deadlock**.

**Five states per channel, and they are not a scale**: flowing, quiet, stale, **never-reported**, and
**unreadable** (`seen: null`). The last two are the ones to keep apart — a session that never had that
sensor is a different fact from one whose sensor stopped, and both differ from a read that failed.

`/api/admin/health` reports `ok` / `degraded` / `unknown`, and **a check that could not run is `unknown`,
never `ok`.** `/api/admin/consent-summary` is counts only. `/api/admin/env-flags` lists the env-var
switches read-only, from a **named list** — `os.environ` also holds the service-role key, and a dashboard
that enumerated the environment would eventually render a secret.

Tests: `backend/tests/test_admin.py`. `conftest`'s `_feature_flags_are_default` pins the defaults for every
other test file, and **deliberately does not take `monkeypatch`** — requesting it from an autouse fixture
pytest orders early hoists `monkeypatch`'s setup ahead of `_join_poller_threads` and inverts their
teardown, which failed three unrelated tests in teardown for a reason nothing in their bodies could
explain. `pytest --setup-plan` shows the ordering directly. Two more autouse fixtures swap database-backed state
the same way: the daily question budget and the station pairer run in memory for every test but their own, which
take the real functions from `real_claim_daily_question()` / `real_pairing_funcs()`.

## Operational counters are hourly cells keyed by what, never by whom

`backend/ops_metrics.py` keeps `(UTC hour, kind, key) → n, sum, max` in memory and flushes it every
`OPS_FLUSH_SECONDS` (60) through `ops_counters_add` into `ops_counters`. **In the database, not in memory**,
because Render Free sleeps and restarts, and an in-memory "today" would cover the minutes since the last cold
start. A failed flush keeps its cells for the next one; past `_MAX_PENDING` new cells drop and are counted
(`dropped`), restored ones included. **A flush is not idempotent**: one that lands but whose reply is lost is
sent again, so a count can be high by one flush. Rare, and the price of a single upsert with no flush ids.
Once `stop` gives up waiting for a flush, that flush stays silent, since a print during exit is fatal: `stop`
silences it under the print's lock, so a print under way finishes first, waiting at most `_PRINT_WAIT_SECONDS`
so a blocked stdout cannot hang shutdown. Kept 90 days
(`expire_ops_counters`, 03:50).

Reads merge the unflushed cells, so the current hour is never a flush behind. **They page by `id`**
(`_PAGE_ROWS` 1000, PostgREST's silent cap) up to `_MAX_PAGES`, and say `complete: false` past it rather than
showing a short total as whole. "The last N hours" is N hourly buckets, the current one included (`since_hour`).

**A key never names a person, a session, a caller or an address.** A station id (`station1`) may be one: it
names a device, and the stations view exists to say which device. Refusals are `"<status> <method> <route template>"`,
taken from the matched route (`/api/sessions/{session_id}/end`), never `request.url.path`, which carries the
id. A request refused before routing — the public limiter's 429, a declared-length 413 — is matched against
the router by `_route_template`, for the same reason. 404 is not counted: it is a mistyped URL, not a refusal.
Counting lives in `security_headers`, which already sees every response and sits outside `public_rate_limit` and
the size cap, rather than in a middleware layer of its own.

| kind | key | written by |
| --- | --- | --- |
| `refusal` | status, method, route template | `security_headers` middleware |
| `llm_call` | `<provider>:ok`, `:unavailable:<reason>`, `:error:<Exception>` | `llm_client.generate_text` |
| `llm_latency_ms` | provider (sum and max; includes waiting for a slot) | same |
| `llm_tokens` | `<provider>:in` / `:out` | same, from the provider's usage fields |
| `question` | `served:inline`/`served:queue`, `prefetched`, `prefetch_failed`, `prefetch_discarded` | `generate_question`, `_prefetch_worker` |
| `session_discarded` | the `close_reason` the discarded session would have had | `_close_session` |
| `station_refused` | station id (a device) | `_reserve_and_call`, `eeg_start` |

`/api/admin/generation` and `/api/admin/refusals` read them; `hours` is clamped to a week. The cost is an
**estimate** from `CLAUDE_PRICE_*_PER_MTOK` (claude-haiku-4-5 list prices by default) and says so. The daily
call ceiling is `llm_client`'s in-memory window, so the page labels it **"this server process"**; it counts
Claude calls only, so under Ollama the payload sends `null` and no tile is drawn. The Engine page's panels keep
their last counts through a failed poll, with a note, and hand `LoadError` the read's Error (`loadError` from
`useAdminResource`) so a 403 or 503 is not reported as an unreachable backend.

## Today and Stations

**`/api/admin/today`** is one `admin_today(p_since)` read from the school day's local midnight (`_school_day_start`):
- It returns lessons started, open now (any day), ended by `close_reason` (`unrecorded` for a null), answers, and students with a session or an answer.
- Discarded empty sessions leave no row, so they come from the `session_discarded` counters.
- Teacher and parent activity is recorded nowhere, and the page says so rather than showing 0.

**`/api/admin/stations`** joins `station_pairings` (holder, seconds since their page polled, idle past `PAIRING_IDLE_SECONDS`) with `eeg_poller.snapshot()` and today's `station_refused` counters:
- The snapshot carries ids, state and the page's age, never a reading.
- A poller still running for a page silent past `_PAGE_WATCHING_SEC` is the abandoned-lesson case, and the page marks it.
- Pollers are this server process's.
- Under push nothing is paired or polled here, so the list is empty and the page says why.
- Names come from `profiles.display_name`, and are null with `names_retrieved: false` when unreadable.
- Names and refusal counts are reused for `_STATIONS_SLOW_SEC` (30 s), so the page's 5 s poll reads pairings, pollers and last lessons fresh. A new holder or a failed read is never served from that cache. Last lessons are never cached: one can end, or a station be released, with nothing the cache keys on changing.
- Each station carries `last_ended`: the `close_reason` and time of the last lesson that ended on it today, from `sessions.eeg_device_id` (`20261013000000`). `/api/eeg/start` writes it on every start, so a lesson that moves headband names the latest. A released station has no pairing row, so this is what keeps it listed. The time is drawn in the school's timezone (`timezone` on the payload), named, since "today" is the school's day. Pull only; `last_ended_retrieved: false` says a released station may be missing.

## Funnel, consent changes and signal quality

Three read-only RPCs (`20261011000000`), each called inline (so `_missing_rpc` sees a literal name and consequence) and shaped by `_jsonb_as`. A jsonb object can come back wrapped in a one-row list, but a one-row array must not be unwrapped.

- **`/api/admin/funnel`** (`admin_funnel()`): per role, how many accounts have done each step, as they stand now. It is not a history, and a later step can be larger than an earlier one. Counts are system-wide with no breakdown, so no floor applies.
- **`/api/admin/consent-ops?weeks=`** (`admin_consent_ops`): withdrawals, parent turn-ons and erasures per school week (Monday start, in the school's zone) and channel. `weeks` is clamped to 26.
  - Turn-ons exist only from `_ENABLEMENTS_RECORDED_FROM`: `consent_enablements` had no backfill.
  - `signal_erasure` keeps the latest erasure per student and channel, so the erasure count is a lower bound.
  - The page states both.
- **`/api/admin/signal-quality?days=`** (`admin_signal_quality`): per school day, EEG's usable share from the rollup (always read). Per heart source, it gives an SQI decile histogram, the median and p90 seconds from a session's first heart row to its first `low|moderate|high`, sessions that never calibrated, and the simulated share (`raw.synthetic`).
  - **Heart rows count only while that sensor's consent is on now** (`muse_optics` needs `headband_optical_enabled`, `rppg` needs `camera_enabled`), the same rule every other surface follows for a withdrawn channel.
  - A day, or a day's source, with fewer than `_COHORT_MIN_STUDENTS` students comes back as `withheld` with no figure at all.
  - "Never calibrated" counts only ended lessons. An open uncalibrated one is `still_calibrating`, since it may yet calibrate.
  - Today's EEG row is `partial`: the rollup is written when a lesson closes, so it holds ended lessons only, unlike the heart figures beside it.
  - `days` is clamped to 60.

## Adaptive decisions

**`/api/admin/adaptive?days=`** (`admin_adaptive_reasons`, `20261012000000`) gives, per school day, adaptive questions eased, raised and held, counts by `why` and by fused label, and increases withheld by the facial veto.
- Aggregates only, with the 5-student floor; `days` is clamped to 60.
- `write_failures` counts decisions that could not be stored over the same school days, from `ops_counters`; `null` means the count was unread, never zero. The panel says the figures are short by that many.
- A decision whose `opinions` include `headband_optical` or `camera` is left out while the student has that consent off now. EEG is always read.
- The rows themselves are signal-derived (CLAUDE.md, *Erasure* and expiry): service-role only, expired by `expire_adaptive_decisions` at 03:55, and deleted for a student by any erasure.

## The security log records that something happened, never what was in it

`security_events` is append-only, written by `_record_security_event` from the access helpers, the three
rate limiters and the consent write. Read at `GET /api/admin/security-events`, rendered by
`pages/admin/SecurityEvents.jsx`.

**Every site answering 403 is classified, in `test_security_events.py`, as recording the denial or as not
being one** — a role gate on the caller's own action, device contention, a consent or school-year state.
Not "every 403 records": several would be rows nobody can act on, and a log of those is one nobody reads by
the time a real event lands. What the list removes is the option of not deciding — without it a new refusal
is silent by default. **A hand-rolled copy of a check is where this goes wrong**, because the copy is
outside the helper that records: use `_verify_class_owner` and the other helpers rather than re-deriving
the rule, since re-deriving the *audit* per endpoint leaves the same gaps as re-deriving the check.

**Never a reading, a request body or an IP.** `detail` is context — which check failed, which limiter
fired — and the values live in the tables that own them. An IP would be new personal data about children
for a purpose no consent channel covers, and behind a proxy it is whatever `X-Forwarded-For` says.
`test_security_events.py` reads the call sites and fails on any of those being passed: this is the
property that cannot be walked back once rows exist. Every kind stays checkable without interpreting a
person, the rule `session_alerts` holds, and the CHECK whitelist and `_SECURITY_EVENT_KINDS` are pinned
equal **both ways** — a kind in code alone is a constraint violation the never-raises path swallows, and
one in the schema alone is a filter that can only return nothing.

**Record outside the limiter's lock**, or every caller queues behind a database round trip — worst on
ingest, the most contended of them. That separation is why `_SlidingWindowLimiter.check()` *answers*
instead of raising: the 429 wording, the audit write and the `limiter` label live at the call site,
outside the lock — and the label is `<instance>.name`, never a second copy of the string, or the row
can name a limiter other than the one that fired.

**Which limiters record is a partition, not a habit.**
`test_every_limiter_either_records_or_is_classified_as_silent` requires every limiter to record or to be
listed as deliberately silent with a reason — generation was silent for a while and nothing said
whether that was a decision. **It finds the limiters at runtime and carries a named floor**, because a
partition over a scan is only as good as the scan: an AST match on one assignment shape misses an
annotated one, and collecting them into a registry — which `_PUBLIC_BUDGETS` already is — would
have left it reporting nothing unclassified while examining nothing. Same countermeasure as the
chart-render palette scraper's refusal of an empty result. The generation limiter's call sites differ,
so it is pinned per site too: `practice_question` and `generate_question` record, because `get_user`
resolved a real actor; `_prefetch_worker` does not, because no refusal reaches anybody — a skipped
refill leaves the queue short and the next question is generated inline. `rate_limited` is cooled (300 s) because a limiter fires once per
*request* past the allowance; denials are not, since deduplicating them would hide a caller probing a
series of different students. **The cooldown key carries what makes two of that kind's events different** —
`_COOLED_KINDS` names the `detail` fields, `limiter` here. On `(kind, actor)` alone the first limiter to
fire masks the other two for the whole window, and ingest at ~1 Hz per student is always the one that gets
there first. **So two limiters may not answer to one name**, asserted beside the floor: the name is the
cooldown field, so they would share one bucket, and both the floor and the partition compare *sets* of
names, which collapses them into one entry. **Keyed on object identity, never on how many attribute paths
reach it** — that scan deliberately finds a limiter by every path, so counting paths fails the registry
refactor it was built to survive and blames a duplicated constructor that does not exist.

**The audit must not break what it audits.** It never raises, like `_raise_session_alerts`, and
`_require_admin` — the one hook reaching into the request object — reads `.url.path` defensively.

## Every per-caller rate limit is one `_SlidingWindowLimiter`, and tests patch the object

Strategies, chart summary, ingest, generation and the public address budgets each hold one instance with its own
lock, so the health probe at 1800/min never contends with question generation. A new per-caller limit is another
instance, never a hand-rolled window.

**Not `llm_client`'s generation bounds**, which are a process-wide semaphore plus a daily counter — a
different shape for a shared-availability resource, and folding it in would be a refactor for its own
sake. The per-endpoint pools and waiter semaphores stay per-endpoint too: one shared pool would let a
burst of chart summaries starve question generation.

**`check()` answers rather than raising**, because the public routes call it from middleware where there
is no handler to raise into — and `_claim_generation_slot` wants a bool, since the prefetch worker has no
request to fail.

**A test tightens the limiter object, never `main._X_RATE_LIMIT`.** Those constants are read once at
import to build the limiters, so patching one now changes nothing: the test runs against the real budget
and passes for a reason unrelated to what it claims. `conftest.tighten(monkeypatch, limiter, limit=,
window=)` is the one-line form, and `.reset()` is what the autouse fixtures call.

**Retention is a rolling 180 days on its own `pg_cron` job**, deliberately not `expired_signal_cutoff()`:
that would delete the record of who read a child's signals at the moment those signals expire, and no
bound at all would leave an unbounded log of which adult opened which child's record.

**Don't name an account through `_profiles_many` here.** It substitutes `_placeholder_profile` for a row it
could not read, whose `display_name` is the literal **"Student"** — right where a blank name would otherwise
render as a withdrawn preference, and wrong on an audit page, where it puts a plausible name on every row of
a name-lookup outage and the wrong one on a teacher's. The endpoint reads names itself and sends `null`,
with `names_retrieved` beside it: "no profile" and "could not look it up" are two facts, and the id carries
the row either way.

**`LoadError` is for a request that *failed*, and ignores a message you pass it** — it derives its
sentence from `error.status`. A `retrieved: false` payload is a successful request whose read failed, so
it needs its own wording; on this page "could not be read" and "is empty" must never share a rendering.
