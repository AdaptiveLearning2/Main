-- student_signal_summary reads a past day from its rollup row while that row is whole, and from raw
-- rows otherwise, back to the row once they have expired. The dominant emotion counts trusted
-- readings only, as rollup_signal_day and the weekly report do. Same signature; revokes restated.

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
  past_rows AS (
    -- A row is whole unless a session reaching its day is still open, or closed after the row was
    -- written: that close's rollup failed, and nothing retries it.
    SELECT r.*, d.day_start, d.day_end,
           NOT EXISTS (SELECT 1 FROM sessions s
                       WHERE s.user_id = p_student_id AND s.started_at < d.day_end
                         AND (s.ended_at IS NULL OR s.ended_at > r.updated_at)) AS whole
    FROM days d, today t, signal_daily_rollup r
    WHERE d.day < t.d AND r.user_id = p_student_id AND r.day = d.day
      AND (r.channel = 'cognitive'
           OR (r.channel = 'heart' AND p_include_heart)
           OR (r.channel = 'emotion' AND p_include_emotion))
  ),
  rolled AS (
    -- A day comes from its row while whole, and from raw rows otherwise until they have expired.
    SELECT p.* FROM past_rows p
    WHERE p.whole
       OR NOT EXISTS (
         SELECT 1 FROM cognitive_signals x
          WHERE p.channel = 'cognitive' AND x.user_id = p_student_id
            AND x.ts >= p.day_start AND x.ts < p.day_end
         UNION ALL
         SELECT 1 FROM heart_signals x
          WHERE p.channel = 'heart' AND x.user_id = p_student_id
            AND x.ts >= p.day_start AND x.ts < p.day_end
         UNION ALL
         SELECT 1 FROM face_signals x
          WHERE p.channel = 'emotion' AND x.user_id = p_student_id
            AND x.ts >= p.day_start AND x.ts < p.day_end)
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
    SELECT (SELECT avg(attention) FROM face_raw)                              AS attention,
           -- Every labelled reading, trusted or not: none trusted reads as Calibrating, not No sensor.
           (SELECT COALESCE(sum(sample_count), 0) FROM rolled WHERE channel = 'emotion')
             + (SELECT count(emotion) FROM face_raw)                          AS n,
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
