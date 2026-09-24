-- Four indexes that duplicate or prefix a surviving one; write cost only.
-- Must run after 20260905010000, which adds answers_session_idx's replacement.

-- Same columns as the unique heart_session_source_ts_key.
DROP INDEX IF EXISTS "public"."heart_session_source_ts_idx";
-- Prefix of user_math_performance_user_id_topic_id_key.
DROP INDEX IF EXISTS "public"."perf_user_idx";
-- Prefix of cog_user_ts_idx.
DROP INDEX IF EXISTS "public"."cog_user_idx";
-- Superseded by answers_session_answered_idx.
DROP INDEX IF EXISTS "public"."answers_session_idx";
