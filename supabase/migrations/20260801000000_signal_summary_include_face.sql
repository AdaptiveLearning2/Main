-- p_include_face on the summary RPCs: false reads no face_signals row at all.
-- The single-student function also gains dominant_emotion.

-- DROP, not CREATE OR REPLACE: a new parameter would leave the old signature
-- behind as a still-granted overload blind to the opt-out.
DROP FUNCTION IF EXISTS "public"."student_signal_summary"("uuid", integer);
DROP FUNCTION IF EXISTS "public"."student_signal_summary_many"("uuid"[], integer);
-- CREATE OR REPLACE cannot change a return type.
DROP FUNCTION IF EXISTS "public"."student_signal_summary"("uuid", integer, boolean);

-- SECURITY INVOKER: RLS still applies if a lower-privileged role reaches it.
CREATE OR REPLACE FUNCTION "public"."student_signal_summary"(
  "p_student_id" "uuid",
  "p_days" integer DEFAULT 7,
  "p_include_face" boolean DEFAULT true
)
RETURNS TABLE (
  "focus" double precision,
  "stress" double precision,
  "engagement" double precision,
  "face_attention" double precision,
  "sessions" bigint,
  "cognitive_samples" bigint,
  "face_samples" bigint,
  "dominant_emotion" "text"
)
LANGUAGE "sql"
STABLE
AS $$
  WITH bounds AS (
    SELECT now() - (GREATEST(p_days, 1) || ' days')::interval AS since
  ),
  cog AS (
    -- count(c.focus), not count(*): poor-contact rows carry NULL measurements.
    SELECT avg(c.focus)      AS focus,
           avg(c.stress)     AS stress,
           avg(c.engagement) AS engagement,
           count(c.focus)    AS n
    FROM cognitive_signals c, bounds b
    WHERE c.user_id = p_student_id AND c.ts >= b.since
  ),
  fac AS (
    -- Off yields the same shape as no readings; callers use face_included to
    -- tell the two apart.
    SELECT avg(f.attention)   AS attention,
           count(f.attention) AS n,
           -- FILTERed so a NULL emotion cannot win the vote.
           mode() WITHIN GROUP (ORDER BY f.emotion)
             FILTER (WHERE f.emotion IS NOT NULL) AS emotion
    FROM face_signals f, bounds b
    WHERE p_include_face AND f.user_id = p_student_id AND f.ts >= b.since
  ),
  ses AS (
    SELECT count(*) AS n
    FROM sessions s, bounds b
    WHERE s.user_id = p_student_id AND s.started_at >= b.since
  )
  SELECT cog.focus, cog.stress, cog.engagement, fac.attention,
         ses.n, cog.n, fac.n, fac.emotion
  FROM cog, fac, ses;
$$;

-- No dominant_emotion: the parent dashboard has no emotion tile.
CREATE OR REPLACE FUNCTION "public"."student_signal_summary_many"(
  "p_student_ids" "uuid"[],
  "p_days" integer DEFAULT 7,
  "p_include_face" boolean DEFAULT true
)
RETURNS TABLE (
  "student_id" "uuid",
  "focus" double precision,
  "stress" double precision,
  "engagement" double precision,
  "face_attention" double precision,
  "sessions" bigint,
  "cognitive_samples" bigint,
  "face_samples" bigint
)
LANGUAGE "sql"
STABLE
AS $$
  -- Explicit CROSS JOIN, not a comma: a comma binds looser than JOIN, so the
  -- lateral subquery would only see bounds and couldn't reference ids.sid.
  SELECT ids.sid,
         cog.focus, cog.stress, cog.engagement, fac.attention,
         ses.n, cog.n, fac.n
  FROM (SELECT DISTINCT u.sid FROM unnest(p_student_ids) AS u(sid)) ids
  CROSS JOIN (
    SELECT now() - (GREATEST(p_days, 1) || ' days')::interval AS since
  ) b
  LEFT JOIN LATERAL (
    SELECT avg(c.focus)      AS focus,
           avg(c.stress)     AS stress,
           avg(c.engagement) AS engagement,
           count(c.focus)    AS n
    FROM cognitive_signals c
    WHERE c.user_id = ids.sid AND c.ts >= b.since
  ) cog ON true
  LEFT JOIN LATERAL (
    SELECT avg(f.attention)   AS attention,
           count(f.attention) AS n
    FROM face_signals f
    WHERE p_include_face AND f.user_id = ids.sid AND f.ts >= b.since
  ) fac ON true
  LEFT JOIN LATERAL (
    SELECT count(*) AS n
    FROM sessions s
    WHERE s.user_id = ids.sid AND s.started_at >= b.since
  ) ses ON true;
$$;

-- New signatures carry fresh ACLs; anon/authenticated are revoked by name.
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean) FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean) TO "service_role";

REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean) FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean) TO "service_role";

NOTIFY pgrst, 'reload schema';
