-- student_signal_summary reads settled past days from the daily rollup, and raw rows only for
-- today and for days with no rollup row or a still-open session. Emotion counts trusted readings
-- only, as rollup_signal_day and the weekly report do. Same signature; revokes restated.

CREATE OR REPLACE FUNCTION "public"."student_signal_summary"(
  "p_student_id" "uuid",
  "p_days" integer DEFAULT 7,
  "p_include_heart" boolean DEFAULT true,
  "p_include_emotion" boolean DEFAULT true,
  "p_timezone" "text" DEFAULT 'UTC'
)
RETURNS TABLE (
  "focus" double precision,
  "stress" double precision,
  "engagement" double precision,
  "face_attention" double precision,
  "heart_rate_bpm" double precision,
  "rmssd_ms" double precision,
  "sessions" bigint,
  "cognitive_samples" bigint,
  "face_samples" bigint,
  "heart_samples" bigint,
  "dominant_emotion" "text"
)
LANGUAGE "sql"
STABLE
SET "search_path" TO 'public'
AS $$
  WITH today AS (
    SELECT (now() AT TIME ZONE p_timezone)::date AS d  -- an unknown zone raises
  ),
  days AS (
    -- Each school day in the window, bounded as rollup_signal_day bounds it.
    SELECT t.d - i AS day,
           (t.d - i)::timestamp AT TIME ZONE p_timezone     AS day_start,
           (t.d - i + 1)::timestamp AT TIME ZONE p_timezone AS day_end
    FROM today t, generate_series(0, GREATEST(p_days, 1) - 1) AS i
  ),
  settled AS (
    -- Past days no still-open session reaches: their rollup rows hold every reading.
    SELECT d.day
    FROM days d, today t
    WHERE d.day < t.d
      AND NOT EXISTS (SELECT 1 FROM sessions s
                      WHERE s.user_id = p_student_id AND s.ended_at IS NULL
                        AND s.started_at < d.day_end)
  ),
  rolled AS (
    SELECT r.*
    FROM settled s
    JOIN signal_daily_rollup r ON r.user_id = p_student_id AND r.day = s.day
    WHERE r.channel = 'cognitive'
       OR (r.channel = 'heart' AND p_include_heart)
       OR (r.channel = 'emotion' AND p_include_emotion)
  ),
  cog_parts AS (
    -- A rolled day adds avg × count; engagement is the focus index, so it shares focus's count.
    SELECT avg_focus * trusted_sample_count AS focus_sum,
           CASE WHEN avg_focus IS NULL THEN 0 ELSE trusted_sample_count END AS focus_n,
           avg_stress * COALESCE(stress_sample_count, trusted_sample_count) AS stress_sum,
           CASE WHEN avg_stress IS NULL THEN 0
                ELSE COALESCE(stress_sample_count, trusted_sample_count) END AS stress_n,
           avg_engagement * trusted_sample_count AS engagement_sum,
           CASE WHEN avg_engagement IS NULL THEN 0 ELSE trusted_sample_count END AS engagement_n
    FROM rolled WHERE channel = 'cognitive'
    UNION ALL
    SELECT sum(c.focus), count(c.focus), sum(c.stress), count(c.stress),
           sum(c.engagement), count(c.engagement)
    FROM days d
    CROSS JOIN LATERAL (SELECT focus, stress, engagement FROM cognitive_signals
                        WHERE user_id = p_student_id
                          AND ts >= d.day_start AND ts < d.day_end) c
    WHERE NOT EXISTS (SELECT 1 FROM rolled r WHERE r.channel = 'cognitive' AND r.day = d.day)
  ),
  cog AS (
    SELECT sum(focus_sum) / NULLIF(sum(focus_n), 0)           AS focus,
           sum(stress_sum) / NULLIF(sum(stress_n), 0)         AS stress,
           sum(engagement_sum) / NULLIF(sum(engagement_n), 0) AS engagement,
           COALESCE(sum(focus_n), 0)::bigint                  AS n
    FROM cog_parts
  ),
  face_raw AS (
    SELECT f.emotion, f.emotion_trusted, f.attention
    FROM days d
    CROSS JOIN LATERAL (SELECT emotion, emotion_trusted, attention FROM face_signals
                        WHERE user_id = p_student_id
                          AND ts >= d.day_start AND ts < d.day_end) f
    WHERE p_include_emotion
      AND NOT EXISTS (SELECT 1 FROM rolled r WHERE r.channel = 'emotion' AND r.day = d.day)
  ),
  labels AS (
    -- Trusted readings per label, counted as rollup_signal_day counts them.
    SELECT e.key AS emotion, e.value::bigint AS n
    FROM rolled r, jsonb_each_text(COALESCE(r.emotion_counts, '{}'::jsonb)) AS e
    WHERE r.channel = 'emotion'
    UNION ALL
    SELECT emotion, count(*) FROM face_raw
    WHERE emotion_trusted AND emotion IS NOT NULL
    GROUP BY emotion
  ),
  fac AS (
    SELECT (SELECT avg(attention) FROM face_raw)               AS attention,
           (SELECT COALESCE(sum(n), 0)::bigint FROM labels)    AS n,
           -- Ties go to the first label in sort order, as mode() chose.
           (SELECT emotion FROM labels GROUP BY emotion
             ORDER BY sum(n) DESC, emotion LIMIT 1)            AS emotion
  ),
  hrt_parts AS (
    -- RMSSD is gated out of some trusted windows, so its rolled weight approximates.
    SELECT avg_heart_rate_bpm * trusted_sample_count AS bpm_sum,
           CASE WHEN avg_heart_rate_bpm IS NULL THEN 0 ELSE trusted_sample_count END AS bpm_n,
           avg_rmssd_ms * trusted_sample_count AS rmssd_sum,
           CASE WHEN avg_rmssd_ms IS NULL THEN 0 ELSE trusted_sample_count END AS rmssd_n
    FROM rolled WHERE channel = 'heart'
    UNION ALL
    SELECT sum(h.heart_rate_bpm), count(h.heart_rate_bpm), sum(h.rmssd_ms), count(h.rmssd_ms)
    FROM days d
    CROSS JOIN LATERAL (SELECT heart_rate_bpm, rmssd_ms FROM heart_signals
                        WHERE user_id = p_student_id AND trusted IS TRUE
                          AND ts >= d.day_start AND ts < d.day_end) h
    WHERE p_include_heart
      AND NOT EXISTS (SELECT 1 FROM rolled r WHERE r.channel = 'heart' AND r.day = d.day)
  ),
  hrt AS (
    SELECT sum(bpm_sum) / NULLIF(sum(bpm_n), 0)     AS bpm,
           sum(rmssd_sum) / NULLIF(sum(rmssd_n), 0) AS rmssd,
           COALESCE(sum(bpm_n), 0)::bigint          AS n
    FROM hrt_parts
  ),
  ses AS (
    SELECT count(*) AS n
    FROM sessions s
    WHERE s.user_id = p_student_id AND s.started_at >= (SELECT min(day_start) FROM days)
  )
  SELECT cog.focus, cog.stress, cog.engagement, fac.attention,
         hrt.bpm, hrt.rmssd,
         ses.n, cog.n, fac.n, hrt.n, fac.emotion
  FROM cog, fac, hrt, ses;
$$;

REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean, boolean, "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean, boolean, "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean, boolean, "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean, boolean, "text") TO "service_role";

NOTIFY pgrst, 'reload schema';
