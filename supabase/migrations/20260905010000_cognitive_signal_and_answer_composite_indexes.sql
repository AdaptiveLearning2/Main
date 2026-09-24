-- session_answers(session_id, answered_at DESC) for latest_signals_for_sessions'
-- DISTINCT ON; supersedes answers_session_idx (dropped in 20260905020000).
-- On a large table, build CONCURRENTLY by hand first; IF NOT EXISTS then no-ops.

CREATE INDEX IF NOT EXISTS "answers_session_answered_idx"
    ON "public"."session_answers" USING "btree" ("session_id", "answered_at" DESC);
