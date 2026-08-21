-- `face_samples` counted a column with no producer, so it was always zero.
--
-- Both summary RPCs computed it as `count(f.attention)`, but `attention` has
-- never had a producer -- the column is always null -- so the count was
-- structurally 0 however much facial data existed. Every facial subtitle on
-- the teacher and parent dashboards quoted this number, reading "no face
-- data" directly beneath a Dominant Emotion the same query had just computed
-- correctly.
--
-- Now `count(f.emotion)`, matching what `rollup_signal_day` already counts
-- for the emotion channel, so the live summary and the one that outlives the
-- raw rows agree about what a facial sample is.
--
-- `avg(f.attention)` is left alone: it's also always null, but as a value
-- rather than a count, null correctly reads as "not measured" -- a zero
-- count incorrectly read as "nothing was recorded".
--
-- Same signatures, so CREATE OR REPLACE keeps the existing ACLs and creates
-- no overload; the revokes below are restated anyway.

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
AS $$
  WITH bounds AS (
    -- Local midnight `p_days` days ago, expressed as the UTC instant it is --
    -- matches the boundary `_school_timezone`/`_school_day` compute in
    -- Python. An unrecognised zone name raises, so the RPC fails loudly
    -- rather than silently using the wrong cutoff.
    SELECT (date_trunc('day', now() AT TIME ZONE p_timezone)
            - (GREATEST(p_days, 1) - 1) * interval '1 day') AT TIME ZONE p_timezone AS since
  ),
  cog AS (
    SELECT avg(c.focus)      AS focus,
           avg(c.stress)     AS stress,
           avg(c.engagement) AS engagement,
           count(c.focus)    AS n
    FROM cognitive_signals c, bounds b
    WHERE c.user_id = p_student_id AND c.ts >= b.since
  ),
  fac AS (
    SELECT avg(f.attention)   AS attention,
           count(f.emotion) AS n,
           mode() WITHIN GROUP (ORDER BY f.emotion)
             FILTER (WHERE f.emotion IS NOT NULL) AS emotion
    FROM face_signals f, bounds b
    WHERE p_include_emotion AND f.user_id = p_student_id AND f.ts >= b.since
  ),
  hrt AS (
    SELECT avg(h.heart_rate_bpm)   AS bpm,
           avg(h.rmssd_ms)         AS rmssd,
           count(h.heart_rate_bpm) AS n
    FROM heart_signals h, bounds b
    WHERE p_include_heart AND h.user_id = p_student_id AND h.ts >= b.since
      AND h.trusted IS TRUE
  ),
  ses AS (
    SELECT count(*) AS n
    FROM sessions s, bounds b
    WHERE s.user_id = p_student_id AND s.started_at >= b.since
  )
  SELECT cog.focus, cog.stress, cog.engagement, fac.attention,
         hrt.bpm, hrt.rmssd,
         ses.n, cog.n, fac.n, hrt.n, fac.emotion
  FROM cog, fac, hrt, ses;
$$;

CREATE OR REPLACE FUNCTION "public"."student_signal_summary_many"(
  "p_student_ids" "uuid"[],
  "p_days" integer DEFAULT 7,
  "p_include_heart" boolean DEFAULT true,
  "p_include_emotion" boolean DEFAULT true,
  "p_timezone" "text" DEFAULT 'UTC'
)
RETURNS TABLE (
  "student_id" "uuid",
  "focus" double precision,
  "stress" double precision,
  "engagement" double precision,
  "face_attention" double precision,
  "heart_rate_bpm" double precision,
  "rmssd_ms" double precision,
  "sessions" bigint,
  "cognitive_samples" bigint,
  "face_samples" bigint,
  "heart_samples" bigint
)
LANGUAGE "sql"
STABLE
AS $$
  WITH bounds AS (
    SELECT (date_trunc('day', now() AT TIME ZONE p_timezone)
            - (GREATEST(p_days, 1) - 1) * interval '1 day') AT TIME ZONE p_timezone AS since
  ),
  ids AS (
    SELECT unnest(p_student_ids) AS sid
  )
  SELECT ids.sid,
         (SELECT avg(c.focus)      FROM cognitive_signals c, bounds b
           WHERE c.user_id = ids.sid AND c.ts >= b.since),
         (SELECT avg(c.stress)     FROM cognitive_signals c, bounds b
           WHERE c.user_id = ids.sid AND c.ts >= b.since),
         (SELECT avg(c.engagement) FROM cognitive_signals c, bounds b
           WHERE c.user_id = ids.sid AND c.ts >= b.since),
         (SELECT avg(f.attention)  FROM face_signals f, bounds b
           WHERE p_include_emotion AND f.user_id = ids.sid AND f.ts >= b.since),
         (SELECT avg(h.heart_rate_bpm) FROM heart_signals h, bounds b
           WHERE p_include_heart AND h.user_id = ids.sid AND h.ts >= b.since
             AND h.trusted IS TRUE),
         (SELECT avg(h.rmssd_ms)   FROM heart_signals h, bounds b
           WHERE p_include_heart AND h.user_id = ids.sid AND h.ts >= b.since
             AND h.trusted IS TRUE),
         (SELECT count(*)          FROM sessions s, bounds b
           WHERE s.user_id = ids.sid AND s.started_at >= b.since),
         (SELECT count(c.focus)    FROM cognitive_signals c, bounds b
           WHERE c.user_id = ids.sid AND c.ts >= b.since),
         (SELECT count(f.emotion) FROM face_signals f, bounds b
           WHERE p_include_emotion AND f.user_id = ids.sid AND f.ts >= b.since),
         (SELECT count(h.heart_rate_bpm) FROM heart_signals h, bounds b
           WHERE p_include_heart AND h.user_id = ids.sid AND h.ts >= b.since
             AND h.trusted IS TRUE)
  FROM ids;
$$;

-- New signatures carry fresh ACLs, so the revokes are repeated rather than
-- inherited.
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean, boolean, "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean, boolean, "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean, boolean, "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean, boolean, "text") TO "service_role";

REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean, "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean, "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean, "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean, "text") TO "service_role";

NOTIFY pgrst, 'reload schema';
