-- The emotion channel's sample_count stops counting gaze-only rows.
--
-- `face_signals` gained a second producer: the face-mesh landmarker writes
-- `gaze_x`/`gaze_y`, and a row is enqueued when either measurement succeeds.
-- So a window where the landmarker read a gaze and FER+ refused is a real
-- face row with a null emotion, and `sample_count` -- previously `count(*)`
-- over the whole table -- was counting it as an emotion sample.
--
-- `emotion_counts` and `trusted_sample_count` were always filtered and stayed
-- correct. `sample_count` is the one the weekly report surfaces as
-- `face_samples`, so enabling gaze would have read as emotion coverage
-- improving. Nothing is wrong in stored data today, since gaze is currently
-- off and no gaze-only row has ever been written.
--
-- Signature unchanged, so this is a genuine CREATE OR REPLACE with nothing to
-- drop. The revokes are still repeated, since the grant check matches by
-- function name.
--
-- Recomputes rather than accumulates, as before, so a day re-rolled after
-- this lands converges on the new definition.

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
           -- `emotion IS NOT NULL` for the sample count, unlike the other two
           -- channels which count every row: `face_signals` now has two
           -- producers, so `count(*)` here would mean "face rows" rather
           -- than "emotion samples", inflated by every window where the
           -- landmarker succeeded and FER+ refused.
           count(*) FILTER (WHERE emotion IS NOT NULL),
           count(*) FILTER (WHERE emotion_trusted), now()
    FROM face_signals
    WHERE user_id = p_user_id AND ts >= day_start AND ts < day_end
    -- Still `count(*) > 0`, deliberately: the day is summarised if any face
    -- row exists. Gating on emotion instead would leave a gaze-only day's raw
    -- rows undeletable forever, since `expire_signal_rows` refuses to delete
    -- a day with no rollup row.
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
