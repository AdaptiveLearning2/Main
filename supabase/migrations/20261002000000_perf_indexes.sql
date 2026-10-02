-- Indexes for hot reads, and drops of indexes another index already covers.
-- On a large table, build each CREATE by hand with CONCURRENTLY first (CLAUDE.md,
-- Database): a plain build holds a SHARE lock, so ingest waits; IF NOT EXISTS then no-ops.

-- The question dedupe lookup (LLM_topic_decider.add_question_to_supabase) on every
-- generated question. Hash: model text can exceed btree's ~2.7 KB entry limit.
CREATE INDEX IF NOT EXISTS "questions_text_hash_idx"
    ON "public"."questions" USING "hash" ("question_text");

-- Open sessions: per student (class_live, start_session) and overall (the stale sweep, admin).
CREATE INDEX IF NOT EXISTS "sessions_open_user_started_idx"
    ON "public"."sessions" ("user_id", "started_at" DESC) WHERE "ended_at" IS NULL;
CREATE INDEX IF NOT EXISTS "sessions_open_started_idx"
    ON "public"."sessions" ("started_at") WHERE "ended_at" IS NULL;

-- Session review pages each table by id (chart_archive.read_session_signals); without
-- these every page re-reads and re-sorts the whole session.
CREATE INDEX IF NOT EXISTS "cog_session_id_idx" ON "public"."cognitive_signals" ("session_id", "id");
CREATE INDEX IF NOT EXISTS "face_session_id_idx" ON "public"."face_signals" ("session_id", "id");
CREATE INDEX IF NOT EXISTS "heart_session_id_idx" ON "public"."heart_signals" ("session_id", "id");

-- Each duplicates, or is a leading prefix of, the index named beside it; every one
-- was an extra write on each insert. The planner uses the survivor instead.
DROP INDEX IF EXISTS "public"."cog_session_ts_idx";      -- cog_session_ts_key (session_id, ts)
DROP INDEX IF EXISTS "public"."face_session_idx";        -- face_session_ts_key (session_id, ts)
DROP INDEX IF EXISTS "public"."face_user_idx";           -- face_user_ts_idx (user_id, ts)
DROP INDEX IF EXISTS "public"."heart_session_idx";       -- heart_session_ts_idx (session_id, ts)
DROP INDEX IF EXISTS "public"."sessions_user_idx";       -- sessions_user_started_idx (user_id, started_at)
DROP INDEX IF EXISTS "public"."classes_join_code_idx";   -- classes_join_code_key (join_code)
DROP INDEX IF EXISTS "public"."memberships_class_idx";   -- class_memberships_class_id_student_id_key
DROP INDEX IF EXISTS "public"."pcl_parent_idx";          -- parent_child_links_parent_id_child_id_key
