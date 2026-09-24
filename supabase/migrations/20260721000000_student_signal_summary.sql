-- Per-student signal averages computed in Postgres, so the parent dashboard
-- does not pull ~10k raw rows per child.

-- 1. Composite indexes -------------------------------------------------------
-- Every query filters on (user_id, ts). On a large table, build CONCURRENTLY
-- by hand first; IF NOT EXISTS then no-ops.
CREATE INDEX IF NOT EXISTS "cog_user_ts_idx"
  ON "public"."cognitive_signals" USING "btree" ("user_id", "ts" DESC);

CREATE INDEX IF NOT EXISTS "face_user_ts_idx"
  ON "public"."face_signals" USING "btree" ("user_id", "ts" DESC);

CREATE INDEX IF NOT EXISTS "sessions_user_started_idx"
  ON "public"."sessions" USING "btree" ("user_id", "started_at" DESC);

-- 2. Aggregate function ------------------------------------------------------
-- SECURITY INVOKER: the backend checks the relationship first, and RLS still
-- applies if a lower-privileged role ever reaches it.
CREATE OR REPLACE FUNCTION "public"."student_signal_summary"(
  "p_student_id" "uuid",
  "p_days" integer DEFAULT 7
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
    -- count(c.focus), not count(*): poor-contact rows carry NULL measurements
    -- and must not count as samples beside a NULL average.
    SELECT avg(c.focus)      AS focus,
           avg(c.stress)     AS stress,
           avg(c.engagement) AS engagement,
           count(c.focus)    AS n
    FROM cognitive_signals c, bounds b
    WHERE c.user_id = p_student_id AND c.ts >= b.since
  ),
  fac AS (
    SELECT avg(f.attention)  AS attention,
           count(f.attention) AS n
    FROM face_signals f, bounds b
    WHERE f.user_id = p_student_id AND f.ts >= b.since
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

-- 3. Batch variant -----------------------------------------------------------
-- Same aggregate for many students in one round-trip.
CREATE OR REPLACE FUNCTION "public"."student_signal_summary_many"(
  "p_student_ids" "uuid"[],
  "p_days" integer DEFAULT 7
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
    WHERE f.user_id = ids.sid AND f.ts >= b.since
  ) fac ON true
  LEFT JOIN LATERAL (
    SELECT count(*) AS n
    FROM sessions s
    WHERE s.user_id = ids.sid AND s.started_at >= b.since
  ) ses ON true;
$$;

-- service_role only. anon/authenticated hold named EXECUTE grants that a
-- revoke FROM PUBLIC does not remove, so each is revoked by name.
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer) FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary"("uuid", integer) TO "service_role";

REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer) FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer) TO "service_role";

NOTIFY pgrst, 'reload schema';
