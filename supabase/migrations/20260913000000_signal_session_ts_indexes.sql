-- `face_signals` and `heart_signals` get the composite `cognitive_signals`
-- and `session_answers` already have, for the same query shape.
--
-- `student_sessions` now derives a session's last activity from four sources
-- rather than one: the newest answer AND the newest *measured* row in each of
-- the three signal tables. Answers alone let that page call a student idle
-- who was streaming EEG through a long question, while `class_live` -- which
-- has always read all four -- showed the same student active at the same
-- moment.
--
-- Each new read is
--
--     WHERE session_id IN (...) AND <measure> IS NOT NULL
--     ORDER BY ts DESC LIMIT 500
--
-- COGNITIVE_SIGNALS IS ALREADY COVERED and is deliberately not touched here:
-- `cog_session_ts_idx` (session_id, ts) was added by
-- `20260831000000_class_analytics.sql`. Re-creating it under the same name
-- with a DESC ordering would be a silent no-op under IF NOT EXISTS and would
-- read as a gap having been filled -- which is precisely the false read of
-- the schema that `20260905010000` records catching in review. The direction
-- does not matter either way: a btree scans backwards, so an ASC composite
-- serves `ORDER BY ts DESC` exactly as well.
--
-- What is left uncovered is the other two, which carry only the
-- single-column session_id index --
--
--   face_session_idx  (session_id)  -- 20260625000000_init.sql
--   heart_session_idx (session_id)  -- 20260805000000_heart_signals.sql
--
-- so the IN is served by the index and the ORDER BY is a sort over every
-- matching row. `face_signals` is written per camera frame window and
-- `heart_signals` per held optics window, and this endpoint runs whenever a
-- teacher opens a class's session list.
--
-- The single-column indexes are deliberately NOT dropped. Each is a prefix of
-- the new composite and therefore redundant for lookups -- the same
-- conclusion `20260905020000_drop_redundant_indexes.sql` reached for its
-- siblings -- but that migration's own rule is to check
-- `pg_stat_user_indexes.idx_scan` first, and nothing here has. A redundant
-- index costs writes; a dropped one something still uses costs a sequential
-- scan on two of the largest tables in the schema. Left for whoever takes the
-- "Database efficiency" item with the measurement in hand.
--
-- Plain CREATE INDEX, not CONCURRENTLY: Supabase wraps each migration in a
-- transaction and CONCURRENTLY cannot run inside one. Both tables are large
-- in any deployment that has run a headband or a camera, so this takes an
-- ACCESS EXCLUSIVE lock while building -- build them by hand with
-- CONCURRENTLY outside a transaction first, per this repo's documented
-- convention, and the IF NOT EXISTS below will no-op.

CREATE INDEX IF NOT EXISTS "face_session_ts_idx"
    ON "public"."face_signals" USING "btree" ("session_id", "ts" DESC);

CREATE INDEX IF NOT EXISTS "heart_session_ts_idx"
    ON "public"."heart_signals" USING "btree" ("session_id", "ts" DESC);
