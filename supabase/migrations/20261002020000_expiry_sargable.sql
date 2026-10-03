-- The nightly delete bounds on `ts < bound`, the instant the cutoff day ends in the school's
-- timezone; a filter on the local date serves no index. One DELETE per table, capped at
-- p_batch_size * p_max_batches rows: split into statements inside one transaction, the work
-- releases no lock and each statement re-reads every row the earlier ones deleted.
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
    bound      timestamptz;
    removed    jsonb := '{}'::jsonb;
    capped     jsonb := '{}'::jsonb;
    table_name text;
    channel    text;
    n          integer;
    skipped    jsonb := '{}'::jsonb;
    unreached  boolean;
    -- Expired, and its day summarised: one test for the delete and the cap check alike.
    eligible   text := $f$
        s.ts < $1
        AND EXISTS (
            SELECT 1 FROM signal_daily_rollup r
            WHERE r.user_id = s.user_id
              AND r.channel = $2
              AND r.day = (s.ts AT TIME ZONE $3)::date
        )
    $f$;
BEGIN
    cutoff := expired_signal_cutoff();
    SELECT w.timezone INTO tz FROM retention_window w LIMIT 1;
    IF cutoff IS NULL OR tz IS NULL THEN
        RETURN jsonb_build_object('cutoff', NULL, 'deleted', removed,
                                  'skipped_days_without_rollup', skipped,
                                  'hit_batch_cap', capped);
    END IF;
    -- Local midnight after the cutoff day, as rollup_signal_day bounds a day.
    bound := ((cutoff + 1)::timestamp AT TIME ZONE tz);

    FOREACH table_name IN ARRAY ARRAY['cognitive_signals', 'face_signals', 'heart_signals']
    LOOP
        channel := CASE table_name
                       WHEN 'cognitive_signals' THEN 'cognitive'
                       WHEN 'face_signals' THEN 'emotion'
                       ELSE 'heart'
                   END;
        EXECUTE format($f$
            WITH doomed AS (SELECT s.ctid FROM %I s WHERE %s LIMIT $4)
            DELETE FROM %I WHERE ctid IN (SELECT ctid FROM doomed)
        $f$, table_name, eligible, table_name)
        USING bound, channel, tz, p_batch_size::bigint * p_max_batches;
        GET DIAGNOSTICS n = ROW_COUNT;
        removed := removed || jsonb_build_object(table_name, n);
        -- The cap was hit only if eligible rows remain; they appear in neither count.
        unreached := false;
        IF n = p_batch_size::bigint * p_max_batches THEN
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I s WHERE %s)', table_name, eligible)
            INTO unreached USING bound, channel, tz;
        END IF;
        capped := capped || jsonb_build_object(table_name, unreached);

        -- Student-days kept for lack of a rollup row; nonzero means the writer is broken.
        EXECUTE format($f$
            SELECT count(*) FROM (
                SELECT DISTINCT s.user_id, (s.ts AT TIME ZONE $1)::date AS day
                FROM %I s
                WHERE s.ts < $2
            ) d
            WHERE NOT EXISTS (
                SELECT 1 FROM signal_daily_rollup r
                WHERE r.user_id = d.user_id AND r.channel = $3 AND r.day = d.day
            )
        $f$, table_name) INTO n USING tz, bound, channel;
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

NOTIFY pgrst, 'reload schema';
