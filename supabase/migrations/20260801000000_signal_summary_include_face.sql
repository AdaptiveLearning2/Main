-- Thread the facial-recognition opt-out through the headline-summary RPCs.
--
-- The opt-out already covers the weekly report and the teacher student list,
-- both of which skip the face_signals read outright rather than fetching and
-- hiding it. The parent dashboard was reading facial attention through
-- student_signal_summary_many, which had no way to be told -- so switching the
-- control off on a child's report and navigating back to the dashboard put
-- facial attention straight back on screen.
--
-- A control honoured on one surface and ignored on a sibling implies a
-- guarantee the app does not keep, so the parameter goes into the aggregate
-- itself: with p_include_face false, no face_signals row is read into the
-- aggregate at all, matching what the other two surfaces do.

-- DROP rather than CREATE OR REPLACE: adding a parameter changes the
-- signature, so CREATE OR REPLACE would leave the old two-argument function in
-- place as an overload -- still granted, still callable, and still blind to the
-- opt-out. Callers pass named arguments and p_include_face defaults to true, so
-- nothing outside this file needs to change in step.
DROP FUNCTION IF EXISTS "public"."student_signal_summary"("uuid", integer);
DROP FUNCTION IF EXISTS "public"."student_signal_summary_many"("uuid"[], integer);

-- Still SECURITY INVOKER (the default), for the reasons the original migration
-- gives: the backend calls this with the service-role key after its own
-- relationship check, and leaving it INVOKER means RLS still applies if it is
-- ever reached by a lower-privileged role.
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
  "face_samples" bigint
)
LANGUAGE "sql"
STABLE
AS $$
  WITH bounds AS (
    SELECT now() - (GREATEST(p_days, 1) || ' days')::interval AS since
  ),
  cog AS (
    -- count(c.focus), not count(*): a row with a NULL focus contributes
    -- nothing to avg(), and since #18/#20 the pipeline deliberately writes
    -- rows with NULL measurements when electrode contact is bad (the row is
    -- kept so the session timeline stays intact). Counting those would report
    -- a nonzero sample count beside a NULL average.
    SELECT avg(c.focus)      AS focus,
           avg(c.stress)     AS stress,
           avg(c.engagement) AS engagement,
           count(c.focus)    AS n
    FROM cognitive_signals c, bounds b
    WHERE c.user_id = p_student_id AND c.ts >= b.since
  ),
  fac AS (
    -- p_include_face leads the predicate: false excludes every row before any
    -- attention value reaches the aggregate, which is the point of the opt-out.
    -- avg() then yields NULL and count() 0 -- the same shape as a student with
    -- no facial readings, which is why the caller also gets face_included to
    -- tell the two apart.
    SELECT avg(f.attention)   AS attention,
           count(f.attention) AS n
    FROM face_signals f, bounds b
    WHERE p_include_face AND f.user_id = p_student_id AND f.ts >= b.since
  ),
  ses AS (
    SELECT count(*) AS n
    FROM sessions s, bounds b
    WHERE s.user_id = p_student_id AND s.started_at >= b.since
  )
  SELECT cog.focus, cog.stress, cog.engagement, fac.attention,
         ses.n, cog.n, fac.n
  FROM cog, fac, ses;
$$;

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
  -- Explicit CROSS JOIN, not a comma: comma binds looser than JOIN, so with
  -- "FROM ids, bounds b LEFT JOIN LATERAL (...)" the lateral subquery would
  -- only see b and could not reference ids.sid.
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
    -- See the single-student function above: p_include_face false reads no
    -- facial rows for any student in the batch.
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

-- Lock execution down to the service role the backend uses.
--
-- These are new signatures, so they carry fresh ACLs -- the revokes on the
-- two-argument versions do not follow them. REVOKE ... FROM PUBLIC alone is
-- NOT sufficient: Supabase ships ALTER DEFAULT PRIVILEGES granting EXECUTE on
-- new public-schema functions to anon and authenticated *explicitly*, and an
-- explicit grant survives a revoke aimed at the PUBLIC pseudo-role.
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean) FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean) TO "service_role";

REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean) FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean) TO "service_role";

NOTIFY pgrst, 'reload schema';
