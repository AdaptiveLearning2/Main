-- The end-of-year delete of cognitive_signals, face_signals, heart_signals.
-- sessions, session_answers, user_stats, user_math_performance are kept.

-- Expired: days before starts_on, plus days through ends_on once today (school
-- timezone) reaches it. Derived, never "today", so a missed run self-heals.
-- No window row means no cutoff and nothing deleted.
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


-- Refuses a day with no rollup row, per student per channel: the deleted rows
-- are the only copy. Batched to bound lock time. SECURITY INVOKER: pg_cron
-- runs as the scheduling role, which already has the privileges.
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
        -- Batch cap hit: unreached rows appear in neither count.
        capped := capped || jsonb_build_object(table_name, n <> 0);

        -- Student-days kept for lack of a rollup row; nonzero means the writer is broken.
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

-- Daily, not on one date, so a missed run is not a year of silence.
-- cron.schedule upserts on the job name, so a re-run re-points the job.
CREATE EXTENSION IF NOT EXISTS "pg_cron";

SELECT cron.schedule('expire-signal-rows', '30 3 * * *',
                     $job$SELECT public.expire_signal_rows();$job$);

NOTIFY pgrst, 'reload schema';
