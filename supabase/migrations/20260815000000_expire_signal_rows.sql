-- The end-of-year delete. Per-sample rows expire; the year does not.
-- `signal_daily_rollup` and the weekly report's fallback path make this
-- survivable: after this runs, a parent still sees every day of the year, at
-- reduced fidelity, marked as such.
--
-- Deleted: `cognitive_signals`, `face_signals`, `heart_signals`.
-- Kept: `sessions`, `session_answers`, `user_stats`, `user_math_performance`
-- -- academic history is not signal data and carries different expectations.
--
-- Not handled here: Supabase Storage. Object deletion doesn't cascade from a
-- row delete, and there's no column yet recording which objects to remove --
-- this job grows a step once that lands.

-- Two rules decide which days are expired:
--
--   1. Always: days before `starts_on` -- once a new school year is
--      configured, the previous one's detail is outside the window.
--   2. Once today (in the school's timezone) is on or after `ends_on`: days
--      up to and including `ends_on` -- this year, now finished.
--
-- Expressed as one cutoff so the delete is a single comparison.
--
-- Idempotent and catch-up by construction: the cutoff is derived from the
-- window and today, not from "the day this run fired", so a cron that misses
-- its day completes on the next run and a repeat run deletes nothing new. A
-- same-day delete with no margin is only safe because a missed run heals
-- itself.
--
-- Fails closed like everything else here: no window row means no cutoff and
-- nothing is deleted.
CREATE OR REPLACE FUNCTION "public"."expired_signal_cutoff"()
RETURNS date
LANGUAGE "sql"
STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
    SELECT CASE
        WHEN (now() AT TIME ZONE w.timezone)::date >= w.ends_on THEN w.ends_on
        ELSE w.starts_on - 1
    END
    FROM retention_window w
    LIMIT 1;
$$;

REVOKE ALL ON FUNCTION "public"."expired_signal_cutoff"() FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."expired_signal_cutoff"() FROM "anon";
REVOKE ALL ON FUNCTION "public"."expired_signal_cutoff"() FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."expired_signal_cutoff"() TO "service_role";


-- Refuses to delete a day with no rollup row, per student per channel. This
-- is the most important line in the file: without it, a bug in the rollup
-- writer becomes silent permanent data loss, since the deleted rows are the
-- only copy. With it, a broken writer just leaves data that doesn't expire,
-- which is visible and fixable.
--
-- Batched, since an unbounded DELETE over a year of rows would hold a long
-- lock. `p_batch_size` rows per pass, stopping when a pass deletes nothing or
-- `p_max_batches` is reached, so a first-ever expiry of a full year can't run
-- unboundedly long in one invocation.
--
-- SECURITY INVOKER: pg_cron runs a job as the role that scheduled it, so this
-- already has the privileges it needs. A definer function whose entire job
-- is destroying data would be escalation surface for nothing.
CREATE OR REPLACE FUNCTION "public"."expire_signal_rows"(
    "p_batch_size" integer DEFAULT 5000,
    "p_max_batches" integer DEFAULT 200
) RETURNS jsonb
LANGUAGE "plpgsql"
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
DECLARE
    cutoff     date;
    tz         text;
    removed    jsonb := '{}'::jsonb;
    capped     jsonb := '{}'::jsonb;
    table_name text;
    channel    text;
    n          integer;
    total      integer;
    batches    integer;
    skipped    jsonb := '{}'::jsonb;
BEGIN
    cutoff := expired_signal_cutoff();
    SELECT w.timezone INTO tz FROM retention_window w LIMIT 1;
    IF cutoff IS NULL OR tz IS NULL THEN
        -- No window configured, so nothing has expired.
        RETURN jsonb_build_object('cutoff', NULL, 'deleted', removed,
                                  'skipped_days_without_rollup', skipped,
                                  'hit_batch_cap', capped);
    END IF;

    FOREACH table_name IN ARRAY ARRAY['cognitive_signals', 'face_signals', 'heart_signals']
    LOOP
        channel := CASE table_name
                       WHEN 'cognitive_signals' THEN 'cognitive'
                       WHEN 'face_signals' THEN 'emotion'
                       ELSE 'heart'
                   END;
        total := 0;
        batches := 0;
        LOOP
            EXECUTE format($f$
                WITH doomed AS (
                    SELECT s.ctid
                    FROM %I s
                    WHERE (s.ts AT TIME ZONE %L)::date <= %L::date
                      AND EXISTS (
                          SELECT 1 FROM signal_daily_rollup r
                          WHERE r.user_id = s.user_id
                            AND r.channel = %L
                            AND r.day = (s.ts AT TIME ZONE %L)::date
                      )
                    LIMIT %s
                )
                DELETE FROM %I WHERE ctid IN (SELECT ctid FROM doomed)
            $f$, table_name, tz, cutoff, channel, tz, p_batch_size, table_name);
            GET DIAGNOSTICS n = ROW_COUNT;
            total := total + n;
            batches := batches + 1;
            EXIT WHEN n = 0 OR batches >= p_max_batches;
        END LOOP;
        removed := removed || jsonb_build_object(table_name, total);
        -- Whether the batch cap stopped this table before it ran out of work.
        -- Rows that were eligible but not reached appear in neither `deleted`
        -- nor `skipped_days_without_rollup`, so `skipped = 0` alone doesn't
        -- mean everything eligible was handled -- `capped` says so instead of
        -- leaving it to be inferred. Harmless either way, since the job is
        -- idempotent and the next run finishes the work.
        capped := capped || jsonb_build_object(table_name, n <> 0);

        -- Student-days left behind because nothing had summarised them. This
        -- is the number that says the rollup writer is broken, so it's
        -- reported rather than just logged. Zero is the healthy answer.
        EXECUTE format($f$
            SELECT count(*) FROM (
                SELECT DISTINCT s.user_id, (s.ts AT TIME ZONE %L)::date AS day
                FROM %I s
                WHERE (s.ts AT TIME ZONE %L)::date <= %L::date
            ) d
            WHERE NOT EXISTS (
                SELECT 1 FROM signal_daily_rollup r
                WHERE r.user_id = d.user_id AND r.channel = %L AND r.day = d.day
            )
        $f$, tz, table_name, tz, cutoff, channel) INTO n;
        skipped := skipped || jsonb_build_object(table_name, n);
    END LOOP;

    RETURN jsonb_build_object('cutoff', cutoff, 'deleted', removed,
                              'skipped_days_without_rollup', skipped,
                              'hit_batch_cap', capped);
END;
$$;

REVOKE ALL ON FUNCTION "public"."expire_signal_rows"(integer, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."expire_signal_rows"(integer, integer) FROM "anon";
REVOKE ALL ON FUNCTION "public"."expire_signal_rows"(integer, integer) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."expire_signal_rows"(integer, integer) TO "service_role";

-- Daily, not "on ends_on". The cutoff is derived from the window and today,
-- so a run on any other day finds nothing to delete and costs little, while
-- a run on the right day -- or any day after it, if the scheduler was down --
-- does the whole job. Scheduling a single date would make a missed run a
-- year-long silence.
--
-- 03:30 UTC: outside the school day for most of the world, and deliberately
-- not midnight, when every other cron on a host tends to fire.
--
-- `cron.schedule` upserts on the job name, so re-running this migration
-- re-points the existing job rather than creating a second one that would
-- delete the same rows twice.
CREATE EXTENSION IF NOT EXISTS "pg_cron";

SELECT cron.schedule('expire-signal-rows', '30 3 * * *',
                     $job$SELECT public.expire_signal_rows();$job$);

NOTIFY pgrst, 'reload schema';
