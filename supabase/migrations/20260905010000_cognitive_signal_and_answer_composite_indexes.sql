-- cognitive_signals and session_answers are the two write-heavy tables in
-- the "Database efficiency" review still missing the composite index their
-- own access pattern needs -- every other signal/session table already has
-- one:
--
--   face_signals   (user_id, ts DESC)   -- 20260721000000_student_signal_summary.sql
--   heart_signals  (user_id, ts DESC)   -- 20260805000000_heart_signals.sql
--   sessions       (user_id, started_at DESC) -- 20260721000000_student_signal_summary.sql
--
-- cognitive_signals(user_id, ts DESC) mirrors that pattern for
-- rollup_signal_day's per-user/day aggregation and the weekly report's
-- per-user time-range reads, which today plan against two separate
-- single-column indexes (session_id, ts DESC, user_id) rather than one
-- index-only scan.
--
-- session_answers(session_id, answered_at DESC) supports
-- latest_signals_for_sessions' `DISTINCT ON (session_id) ORDER BY
-- session_id, answered_at DESC` (20260827000000), today served by the
-- single-column session_id index plus a sort.
--
-- Plain CREATE INDEX, not CONCURRENTLY: Supabase wraps each migration in a
-- transaction and CONCURRENTLY cannot run inside one. Both tables are small
-- at this point in the product's life (see the "Database efficiency" plan
-- section for the volume estimate) -- if either has grown large by the time
-- this runs, build the index manually with CONCURRENTLY outside a
-- transaction first, per this repo's own documented convention, and this
-- migration's IF NOT EXISTS will no-op.

CREATE INDEX IF NOT EXISTS "cog_user_ts_idx"
    ON "public"."cognitive_signals" USING "btree" ("user_id", "ts" DESC);

CREATE INDEX IF NOT EXISTS "answers_session_answered_idx"
    ON "public"."session_answers" USING "btree" ("session_id", "answered_at" DESC);
