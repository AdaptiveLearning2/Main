-- Two indexes maintained on every write for no read benefit, each a plain
-- prefix/duplicate of an index that already covers the same access pattern.
--
-- heart_session_source_ts_idx (session_id, source, ts), created in
-- 20260805000000_heart_signals.sql, has the identical column list to the
-- later unique heart_session_source_ts_key (20260809120000) -- two btrees
-- built and maintained for the one access pattern once the unique index
-- exists, since a unique index is usable for the same plain lookups/sorts a
-- non-unique one on the same columns would serve.
--
-- perf_user_idx (user_id) on user_math_performance, created in
-- 20260625000000_init.sql, is a plain prefix of the unique
-- user_math_performance_user_id_topic_id_key (user_id, topic_id) --
-- equality/range queries on user_id alone are already served by the leading
-- column of that composite index.
--
-- Pure write-amplification removal: neither drop changes what any existing
-- query can plan against, since the surviving index in each pair covers
-- everything the dropped one did.

DROP INDEX IF EXISTS "public"."heart_session_source_ts_idx";
DROP INDEX IF EXISTS "public"."perf_user_idx";
