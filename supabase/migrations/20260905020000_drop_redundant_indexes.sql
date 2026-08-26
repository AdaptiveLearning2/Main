-- Four indexes maintained on every write for no read benefit, each a plain
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
-- cog_user_idx (user_id) on cognitive_signals, created in
-- 20260625000000_init.sql, has been a plain prefix of cog_user_ts_idx
-- (user_id, ts DESC) since 20260721000000_student_signal_summary.sql added
-- the latter -- missed at the time (`20260831000000_class_analytics.sql`
-- dropped this table's other newly-redundant index, cog_session_idx, in the
-- same migration that made it redundant, but not this one, which had been
-- redundant for over a month by then). cognitive_signals is the hottest
-- write path documented in this repo (~1 row/sec/active session), so this
-- is the one drop in this file with a measurable cost to leaving in place,
-- not just a tidy-up.
--
-- answers_session_idx (session_id) on session_answers, created in
-- 20260625000000_init.sql, is superseded by this migration's sibling,
-- 20260905010000_cognitive_signal_and_answer_composite_indexes.sql, which
-- adds answers_session_answered_idx (session_id, answered_at DESC) --
-- completing the change 20260831000000_class_analytics.sql explicitly
-- deferred ("would arguably suit (session_id, answered_at)... but that is a
-- separate change with its own measurement to do"). Ordered after that
-- migration in this file's own timestamp for exactly this dependency: the
-- superseding index must exist before the superseded one is dropped.
--
-- Pure write-amplification removal: none of these four drops changes what
-- any existing query can plan against, since the surviving index in each
-- pair covers everything the dropped one did.

DROP INDEX IF EXISTS "public"."heart_session_source_ts_idx";
DROP INDEX IF EXISTS "public"."perf_user_idx";
DROP INDEX IF EXISTS "public"."cog_user_idx";
DROP INDEX IF EXISTS "public"."answers_session_idx";
