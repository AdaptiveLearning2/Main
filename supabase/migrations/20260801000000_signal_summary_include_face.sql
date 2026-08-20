-- Threads the facial-recognition opt-out through the headline-summary RPCs.
--
-- The weekly report and the teacher student list already skip the
-- face_signals read outright when the opt-out is on, rather than fetching and
-- hiding it. The parent dashboard read facial attention through
-- student_signal_summary_many, which had no way to be told, so turning the
-- control off on a child's report and going back to the dashboard put facial
-- attention straight back on screen. The fix is to pass the flag into the
-- aggregate itself: with p_include_face false, no face_signals row is read at
-- all, matching the other two surfaces.

-- The single-student function also gains dominant_emotion, so the teacher
-- student list can read its facial tiles from this aggregate instead of
-- pulling raw face_signals rows into the browser (a row cap on that read
-- silently turned "last 7 days" into "the last few minutes").
--
-- DROP rather than CREATE OR REPLACE: adding a parameter changes the
-- signature, so CREATE OR REPLACE would leave the old two-argument function
-- in place as an overload -- still granted, still callable, and still blind
-- to the opt-out. Callers use named arguments and p_include_face defaults to
-- true, so nothing outside this file needs to change in step.
DROP FUNCTION IF EXISTS "public"."student_signal_summary"("uuid", integer);
DROP FUNCTION IF EXISTS "public"."student_signal_summary_many"("uuid"[], integer);
-- The three-argument signature too: CREATE OR REPLACE cannot change a
-- function's return type, so a database that already has this signature (with
-- an older return type) needs the drop to pick up dominant_emotion.
DROP FUNCTION IF EXISTS "public"."student_signal_summary"("uuid", integer, boolean);

-- Still SECURITY INVOKER (the default): the backend calls this with the
-- service-role key after its own relationship check, and INVOKER means RLS
-- still applies if it's ever reached by a lower-privileged role.
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
    -- count(c.focus), not count(*): a row with a NULL focus contributes
    -- nothing to avg(). The pipeline deliberately writes rows with NULL
    -- measurements when electrode contact is bad, keeping the row so the
    -- session timeline stays intact -- counting those would report a nonzero
    -- sample count beside a NULL average.
    SELECT avg(c.focus)      AS focus,
           avg(c.stress)     AS stress,
           avg(c.engagement) AS engagement,
           count(c.focus)    AS n
    FROM cognitive_signals c, bounds b
    WHERE c.user_id = p_student_id AND c.ts >= b.since
  ),
  fac AS (
    -- p_include_face leads the predicate: false excludes every row before any
    -- attention value reaches the aggregate. avg() then yields NULL and
    -- count() 0 -- the same shape as a student with no facial readings, which
    -- is why the caller also gets face_included to tell the two apart.
    SELECT avg(f.attention)   AS attention,
           count(f.attention) AS n,
           -- The most frequent recorded emotion over the window. FILTERed
           -- explicitly so a NULL emotion (nothing read) can't win the vote
           -- and render as a mood.
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

-- Deliberately without the single-student function's dominant_emotion: this
-- one feeds the parent dashboard, which has no emotion tile, so the column
-- would be a mode() per child computed on every load for nothing.
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
    -- Same as the single-student function above: p_include_face false reads
    -- no facial rows for any student in the batch.
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
-- two-argument versions don't follow them. REVOKE ... FROM PUBLIC alone is
-- not enough: Supabase grants EXECUTE on new public-schema functions to anon
-- and authenticated by name, and an explicit grant survives a revoke aimed at
-- the PUBLIC pseudo-role.
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean) FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean) TO "service_role";

REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean) FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean) TO "service_role";

NOTIFY pgrst, 'reload schema';
