-- The daily rollup: one row per student per day per channel, written as
-- sessions close, never at expiry. It is what survives the ends_on delete,
-- which refuses a day with no rollup row.

CREATE TABLE IF NOT EXISTS "public"."signal_daily_rollup" (
    "user_id" "uuid" NOT NULL REFERENCES "public"."profiles"("id") ON DELETE CASCADE,
    -- School calendar day, in retention_window.timezone.
    "day" date NOT NULL,
    "channel" "text" NOT NULL,

    -- cognitive
    "avg_focus" double precision,
    "avg_stress" double precision,
    "avg_engagement" double precision,

    -- heart. Absolute units, not 0..1 ratios.
    "avg_heart_rate_bpm" double precision,
    "avg_rmssd_ms" double precision,
    "avg_stress_score" double precision,
    -- Sensors seen that day, so a change of sensor stays visible.
    "heart_sources" "text"[],

    -- Pie distributions as counts, not a single winner.
    "emotion_counts" "jsonb",
    "stress_counts" "jsonb",

    "sample_count" integer NOT NULL DEFAULT 0,
    -- Rows with a usable measurement: `trusted` (heart), `emotion_trusted`
    -- (emotion), non-null focus (cognitive).
    "trusted_sample_count" integer NOT NULL DEFAULT 0,

    "updated_at" timestamptz NOT NULL DEFAULT "now"(),

    PRIMARY KEY ("user_id", "day", "channel"),
    CONSTRAINT "signal_daily_rollup_channel" CHECK (
        "channel" IN ('cognitive', 'heart', 'emotion')),
    CONSTRAINT "signal_daily_rollup_counts" CHECK (
        "trusted_sample_count" <= "sample_count")
);

-- The delete job sweeps by day across all students.
CREATE INDEX IF NOT EXISTS "signal_daily_rollup_day_idx"
    ON "public"."signal_daily_rollup" ("day");

REVOKE ALL ON TABLE "public"."signal_daily_rollup" FROM "anon";
REVOKE ALL ON TABLE "public"."signal_daily_rollup" FROM "authenticated";
GRANT SELECT ON TABLE "public"."signal_daily_rollup" TO "authenticated";
GRANT ALL ON TABLE "public"."signal_daily_rollup" TO "service_role";

ALTER TABLE "public"."signal_daily_rollup" ENABLE ROW LEVEL SECURITY;

-- Read-your-own, like the per-sample tables. No write policy: the only writer
-- is rollup_signal_day.
CREATE POLICY "own rollup readable" ON "public"."signal_daily_rollup"
    FOR SELECT TO "authenticated"
    USING ("auth"."uid"() = "user_id");


-- In SQL because backend reads are capped. Recomputes rather than
-- accumulates, so replays converge. SECURITY INVOKER on purpose.
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
    -- Local midnight to local midnight; AT TIME ZONE reads a naive timestamp
    -- as that zone.
    day_start := (("p_day")::timestamp AT TIME ZONE "p_timezone");
    day_end   := (("p_day" + 1)::timestamp AT TIME ZONE "p_timezone");

    -- cognitive: `focus IS NOT NULL` is the usable count (poor contact nulls it).
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

    -- heart: averages over trusted rows only, matching the weekly report.
    INSERT INTO signal_daily_rollup AS r (
        user_id, day, channel, avg_heart_rate_bpm, avg_rmssd_ms,
        avg_stress_score, heart_sources, stress_counts,
        sample_count, trusted_sample_count, updated_at)
    SELECT p_user_id, p_day, 'heart',
           avg(heart_rate_bpm) FILTER (WHERE trusted),
           avg(rmssd_ms)       FILTER (WHERE trusted),
           avg(stress_score)   FILTER (WHERE trusted),
           -- Every source seen, trusted or not: it explains a change in the numbers.
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

    -- emotion: counts, trusted labels only.
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

-- All three revokes: anon/authenticated hold named grants PUBLIC does not cover.
REVOKE ALL ON FUNCTION "public"."rollup_signal_day"("uuid", date, "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."rollup_signal_day"("uuid", date, "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."rollup_signal_day"("uuid", date, "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."rollup_signal_day"("uuid", date, "text") TO "service_role";

NOTIFY pgrst, 'reload schema';
