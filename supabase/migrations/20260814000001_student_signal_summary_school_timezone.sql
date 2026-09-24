-- Summary RPC cutoff in the school's timezone, matching _weekly_signal_report.

-- Drop the old signature, or it survives as a granted overload.
DROP FUNCTION IF EXISTS "public"."student_signal_summary"("uuid", integer, boolean, boolean);
DROP FUNCTION IF EXISTS "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean);

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
    -- Local midnight `p_days` days ago, as in `_school_day`. An unknown zone raises.
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
           count(f.attention) AS n,
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
         (SELECT count(f.attention) FROM face_signals f, bounds b
           WHERE p_include_emotion AND f.user_id = ids.sid AND f.ts >= b.since),
         (SELECT count(h.heart_rate_bpm) FROM heart_signals h, bounds b
           WHERE p_include_heart AND h.user_id = ids.sid AND h.ts >= b.since
             AND h.trusted IS TRUE)
  FROM ids;
$$;

-- New signatures carry fresh ACLs.
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean, boolean, "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean, boolean, "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean, boolean, "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean, boolean, "text") TO "service_role";

REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean, "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean, "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean, "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean, "text") TO "service_role";

NOTIFY pgrst, 'reload schema';
