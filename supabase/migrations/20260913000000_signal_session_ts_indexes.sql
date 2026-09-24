-- (session_id, ts) composites on face_signals and heart_signals for
-- student_sessions' newest-measured-row reads; cognitive_signals already has
-- one. The single-column indexes stay until idx_scan evidence exists.
-- Both tables are large: build CONCURRENTLY by hand first; IF NOT EXISTS no-ops.

CREATE INDEX IF NOT EXISTS "face_session_ts_idx"
    ON "public"."face_signals" USING "btree" ("session_id", "ts" DESC);

CREATE INDEX IF NOT EXISTS "heart_session_ts_idx"
    ON "public"."heart_signals" USING "btree" ("session_id", "ts" DESC);
