-- The daily rollup: one row per student per day per channel, written as
-- sessions close rather than generated at expiry. Writing it continuously
-- means it's never a race against the delete job, and it doubles as the read
-- path for historical weeks before anything is deleted.
--
-- This is what makes deleting per-sample rows on `ends_on` survivable -- only
-- the detail goes, not the year. The delete job (a later migration) refuses
-- to delete a day with no rollup row, so a bug in this writer becomes visible
-- data staying too long, not silent permanent loss.

CREATE TABLE IF NOT EXISTS "public"."signal_daily_rollup" (
    "user_id" "uuid" NOT NULL REFERENCES "public"."profiles"("id") ON DELETE CASCADE,
    -- The school's calendar day, resolved in `retention_window.timezone` --
    -- the same day the weekly report buckets by, so the two never disagree.
    "day" date NOT NULL,
    "channel" "text" NOT NULL,

    -- cognitive
    "avg_focus" double precision,
    "avg_stress" double precision,
    "avg_engagement" double precision,

    -- heart. Absolute units, unlike the 0..1 ratios above -- don't scale these
    -- as percentages.
    "avg_heart_rate_bpm" double precision,
    "avg_rmssd_ms" double precision,
    "avg_stress_score" double precision,
    -- Which sensors contributed that day. Accuracy differs between the
    -- headband and the camera, so a reader comparing two weeks needs to see
    -- that the sensor changed, and this is the only place that survives once
    -- the raw rows are gone.
    "heart_sources" "text"[],

    -- The two pie distributions, kept as counts rather than a single winner --
    -- the shape of a day isn't recoverable from just its most frequent label.
    "emotion_counts" "jsonb",
    "stress_counts" "jsonb",

    -- How much was behind the averages, so a thin day stays visibly thin after
    -- the detail is gone -- otherwise four samples and four thousand look the
    -- same once the raw rows are deleted.
    "sample_count" integer NOT NULL DEFAULT 0,
    -- Rows that produced a usable measurement: `trusted` for heart,
    -- `emotion_trusted` for emotion, and for cognitive (no trust flag) a row
    -- whose measurements survived the contact check. Never equal to
    -- sample_count by construction, since poor contact deliberately writes
    -- rows with null measurements to keep "recording but unable to measure"
    -- distinguishable from "no session".
    "trusted_sample_count" integer NOT NULL DEFAULT 0,

    "updated_at" timestamptz NOT NULL DEFAULT "now"(),

    PRIMARY KEY ("user_id", "day", "channel"),
    CONSTRAINT "signal_daily_rollup_channel" CHECK (
        "channel" IN ('cognitive', 'heart', 'emotion')),
    CONSTRAINT "signal_daily_rollup_counts" CHECK (
        "trusted_sample_count" <= "sample_count")
);

-- The primary key covers per-student lookups; this index serves the delete
-- job's other access pattern, which sweeps by day across all students.
CREATE INDEX IF NOT EXISTS "signal_daily_rollup_day_idx"
    ON "public"."signal_daily_rollup" ("day");

-- Revoke before granting: Supabase already grants anon and authenticated
-- every privilege by name, so a bare GRANT alone would change nothing.

REVOKE ALL ON TABLE "public"."signal_daily_rollup" FROM "anon";
REVOKE ALL ON TABLE "public"."signal_daily_rollup" FROM "authenticated";
GRANT SELECT ON TABLE "public"."signal_daily_rollup" TO "authenticated";
GRANT ALL ON TABLE "public"."signal_daily_rollup" TO "service_role";

ALTER TABLE "public"."signal_daily_rollup" ENABLE ROW LEVEL SECURITY;

-- Read-your-own, matching the per-sample tables this summarises -- a summary
-- of a student's own data shouldn't be harder to reach than the samples it
-- came from.
--
-- SELECT only: no insert/update/delete policy for anyone, so PostgREST can't
-- write this table under any JWT. The rollup is derived data; the only
-- correct writer is the function below.
CREATE POLICY "own rollup readable" ON "public"."signal_daily_rollup"
    FOR SELECT TO "authenticated"
    USING ("auth"."uid"() = "user_id");


-- A function rather than backend code, because the aggregation has to happen
-- where the rows are. A day holds thousands of samples, and the reporting
-- path already caps its reads -- pulling rows into Python to average them
-- would silently average a capped subset, and this rollup is what survives
-- the delete, so it can't be quietly wrong.
--
-- Recomputes rather than accumulates, so it's idempotent: closing two
-- sessions on the same day, or replaying a close, converges on the same
-- answer. An incremental writer would have to be exactly-once, which nothing
-- here can promise.
--
-- SECURITY INVOKER (the default, stated for emphasis): a definer function
-- aggregating across every student's signals would be a ready-made way to
-- read anyone's data if ever reachable by a lesser role.
CREATE OR REPLACE FUNCTION "public"."rollup_signal_day"(
    "p_user_id" "uuid",
    "p_day" date,
    "p_timezone" "text" DEFAULT 'UTC'
) RETURNS void
LANGUAGE "plpgsql"
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
DECLARE
    day_start timestamptz;
    day_end   timestamptz;
BEGIN
    -- Local wall-clock midnight to local wall-clock midnight. `AT TIME ZONE`
    -- on a naive timestamp reads it as that zone -- a UTC-based range would
    -- slice the day off by several hours and disagree with the report.
    day_start := (("p_day")::timestamp AT TIME ZONE "p_timezone");
    day_end   := (("p_day" + 1)::timestamp AT TIME ZONE "p_timezone");

    -- cognitive: `focus IS NOT NULL` is the usable count. Poor contact writes
    -- a row with every measurement nulled on purpose, and counting those as
    -- trusted would report a day of bad contact as a day of good data.
    INSERT INTO signal_daily_rollup AS r (
        user_id, day, channel, avg_focus, avg_stress, avg_engagement,
        sample_count, trusted_sample_count, updated_at)
    SELECT p_user_id, p_day, 'cognitive',
           avg(focus), avg(stress), avg(engagement),
           count(*), count(*) FILTER (WHERE focus IS NOT NULL), now()
    FROM cognitive_signals
    WHERE user_id = p_user_id AND ts >= day_start AND ts < day_end
    HAVING count(*) > 0
    ON CONFLICT (user_id, day, channel) DO UPDATE SET
        avg_focus = EXCLUDED.avg_focus,
        avg_stress = EXCLUDED.avg_stress,
        avg_engagement = EXCLUDED.avg_engagement,
        sample_count = EXCLUDED.sample_count,
        trusted_sample_count = EXCLUDED.trusted_sample_count,
        updated_at = EXCLUDED.updated_at;

    -- heart: averages over trusted rows only, matching the weekly report. An
    -- untrusted reading was rejected by the quality gate, and averaging it in
    -- here would smuggle it past that gate permanently once the raw row is
    -- deleted.
    INSERT INTO signal_daily_rollup AS r (
        user_id, day, channel, avg_heart_rate_bpm, avg_rmssd_ms,
        avg_stress_score, heart_sources, stress_counts,
        sample_count, trusted_sample_count, updated_at)
    SELECT p_user_id, p_day, 'heart',
           avg(heart_rate_bpm) FILTER (WHERE trusted),
           avg(rmssd_ms)       FILTER (WHERE trusted),
           avg(stress_score)   FILTER (WHERE trusted),
           -- Every source seen that day, trusted or not -- this column exists
           -- to explain a change in the numbers, and a sensor whose readings
           -- were all rejected is exactly such an explanation.
           (SELECT array_agg(DISTINCT h2.source)
            FROM heart_signals h2
            WHERE h2.user_id = p_user_id
              AND h2.ts >= day_start AND h2.ts < day_end
              AND h2.source IS NOT NULL),
           (SELECT jsonb_object_agg(c.stress_category, c.n)
            FROM (SELECT stress_category, count(*) AS n
                  FROM heart_signals
                  WHERE user_id = p_user_id
                    AND ts >= day_start AND ts < day_end
                    AND trusted AND stress_category IS NOT NULL
                  GROUP BY stress_category) c),
           count(*), count(*) FILTER (WHERE trusted), now()
    FROM heart_signals
    WHERE user_id = p_user_id AND ts >= day_start AND ts < day_end
    HAVING count(*) > 0
    ON CONFLICT (user_id, day, channel) DO UPDATE SET
        avg_heart_rate_bpm = EXCLUDED.avg_heart_rate_bpm,
        avg_rmssd_ms = EXCLUDED.avg_rmssd_ms,
        avg_stress_score = EXCLUDED.avg_stress_score,
        heart_sources = EXCLUDED.heart_sources,
        stress_counts = EXCLUDED.stress_counts,
        sample_count = EXCLUDED.sample_count,
        trusted_sample_count = EXCLUDED.trusted_sample_count,
        updated_at = EXCLUDED.updated_at;

    -- emotion: counts rather than a dominant label, trusted only. FER+ is the
    -- weakest signal here, and an untrusted label already failed the
    -- confidence gate.
    INSERT INTO signal_daily_rollup AS r (
        user_id, day, channel, emotion_counts,
        sample_count, trusted_sample_count, updated_at)
    SELECT p_user_id, p_day, 'emotion',
           (SELECT jsonb_object_agg(e.emotion, e.n)
            FROM (SELECT emotion, count(*) AS n
                  FROM face_signals
                  WHERE user_id = p_user_id
                    AND ts >= day_start AND ts < day_end
                    AND emotion_trusted AND emotion IS NOT NULL
                  GROUP BY emotion) e),
           count(*), count(*) FILTER (WHERE emotion_trusted), now()
    FROM face_signals
    WHERE user_id = p_user_id AND ts >= day_start AND ts < day_end
    HAVING count(*) > 0
    ON CONFLICT (user_id, day, channel) DO UPDATE SET
        emotion_counts = EXCLUDED.emotion_counts,
        sample_count = EXCLUDED.sample_count,
        trusted_sample_count = EXCLUDED.trusted_sample_count,
        updated_at = EXCLUDED.updated_at;
END;
$$;

-- All three revokes are needed: Postgres grants EXECUTE to PUBLIC on new
-- functions, and Supabase separately grants it to anon and authenticated by
-- name, which a PUBLIC-only revoke doesn't touch. This function writes
-- derived data about every student, so it shouldn't stay ambiently callable.
REVOKE ALL ON FUNCTION "public"."rollup_signal_day"("uuid", date, "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."rollup_signal_day"("uuid", date, "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."rollup_signal_day"("uuid", date, "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."rollup_signal_day"("uuid", date, "text") TO "service_role";

NOTIFY pgrst, 'reload schema';
