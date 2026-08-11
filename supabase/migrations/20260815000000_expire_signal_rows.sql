-- The end-of-year delete (Phase 9, last piece).
--
-- Per-sample rows expire; the year does not. `signal_daily_rollup` and the
-- weekly report's fallback path (both already shipped) are what make this
-- survivable: after this runs a parent still sees every day of the year, at
-- reduced fidelity, marked as such.
--
-- **Deleted:** `cognitive_signals`, `face_signals`, `heart_signals`.
-- **Kept:** `sessions`, `session_answers`, `user_stats`,
-- `user_math_performance` -- academic history is not signal data and carries
-- different expectations. A parent asking "how did the term go" is asking about
-- both, and only one of them is a recording of their child's body.
--
-- Not handled here: Supabase Storage. Object deletion does not cascade from a
-- row delete, and `sessions.chart_paths` -- the column that would say which
-- objects to remove -- is Phase 8 and does not exist yet. When it lands, this
-- job grows a step; until then there are no archived charts to orphan.

-- ── which days are expired ──────────────────────────────────────────────────
--
-- Two rules, and the second is what makes "runs on ends_on" true:
--
--   1. Always: days **before `starts_on`**. Once a new school year is
--      configured, the previous one's detail is outside the window and goes.
--   2. Once today (in the school's timezone) is **on or after `ends_on`**: days
--      up to and including `ends_on` -- this year, now finished.
--
-- Expressed as one cutoff so the delete is a single comparison.
--
-- **Idempotent and catch-up by construction.** The cutoff is derived from the
-- window and today, never from "the day this run fired", so a cron that misses
-- its day completes on the next run and a run that repeats deletes nothing new.
-- A same-day delete with no margin is only acceptable because a missed run is
-- self-healing.
--
-- Fails closed like everything else here: no window row means no cutoff and
-- nothing is deleted. An unconfigured school is not one whose data has expired.
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


-- ── the delete ──────────────────────────────────────────────────────────────
--
-- **Refuses to delete a day that has no rollup row**, per student per channel.
-- This is the single most important line in the file. Without it a bug in the
-- rollup writer becomes silent permanent data loss on a fixed date -- the one
-- failure mode in this phase with no recovery, since the rows it would take are
-- the only copy. With it, a broken writer degrades to data that simply does not
-- expire, which is visible, fixable, and reversible.
--
-- Batched. An unbounded DELETE over a year of per-sample rows takes an
-- ACCESS EXCLUSIVE-scale lock footprint and holds it; `p_batch_size` rows at a
-- time keeps each statement short. The loop stops when a pass deletes nothing,
-- or when `p_max_batches` is reached -- a bound so one invocation cannot run
-- unboundedly long against a first-ever expiry of a full year.
--
-- SECURITY INVOKER: pg_cron runs a job as the role that scheduled it, so this
-- has a real invoking user with the privileges it needs. A definer function
-- whose entire job is destroying data is escalation surface for nothing.
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
        -- No window configured. Nothing has expired, because nothing has a term
        -- to have fallen outside of.
        RETURN jsonb_build_object('cutoff', NULL, 'deleted', removed,
                                  'skipped_days_without_rollup', skipped);
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

        -- Student-days left behind on this table because nothing had
        -- summarised them. Counted per channel and reported, not logged: this
        -- is the number that says the rollup writer is broken, and it needs to
        -- surface somewhere a human looks. Zero is the healthy answer.
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
                              'skipped_days_without_rollup', skipped);
END;
$$;

REVOKE ALL ON FUNCTION "public"."expire_signal_rows"(integer, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."expire_signal_rows"(integer, integer) FROM "anon";
REVOKE ALL ON FUNCTION "public"."expire_signal_rows"(integer, integer) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."expire_signal_rows"(integer, integer) TO "service_role";

-- ── scheduling ──────────────────────────────────────────────────────────────
--
-- Daily, not "on ends_on". The cutoff is derived from the window and today, so
-- a run on any other day of the year finds nothing to delete and costs three
-- index scans -- while a run on the right day, or on any day after it if the
-- scheduler was down, does the whole job. Scheduling it for a single date would
-- make a missed run a year-long silence.
--
-- 03:30 UTC: outside the school day for most of the world, and deliberately not
-- midnight, which is when every other cron on a host tends to fire.
--
-- `cron.schedule` upserts on the job name in pg_cron 1.4+, so re-running this
-- migration re-points the existing job rather than adding a second one that
-- would delete the same rows twice over.
--
-- The job runs as the role that scheduled it, which is the migration's role,
-- and that role holds the EXECUTE granted above.
CREATE EXTENSION IF NOT EXISTS "pg_cron";

SELECT cron.schedule('expire-signal-rows', '30 3 * * *',
                     $job$SELECT public.expire_signal_rows();$job$);

NOTIFY pgrst, 'reload schema';
