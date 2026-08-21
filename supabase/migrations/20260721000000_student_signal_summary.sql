-- Per-student signal averages, computed in Postgres instead of the app.
--
-- The parent dashboard needs four numbers per child (avg focus, avg stress,
-- avg face attention, session count). Pulling the raw signal rows into the
-- app to compute those costs ~10k rows per child on a busy week, on a page
-- that loads every visit. Postgres can return the same four numbers without
-- transferring any rows.

-- 1. Composite indexes -------------------------------------------------------
-- Every query here filters on user_id and ts together ("this student, since
-- this date"), so a composite index gives a single range scan instead of
-- combining two separate indexes.
--
-- Plain CREATE INDEX takes a brief lock that blocks writes while it builds.
-- CONCURRENTLY would avoid that but cannot run inside a migration's
-- transaction. Fine at current table sizes; if these tables grow large before
-- this reaches production, build the indexes manually with CONCURRENTLY
-- first and this will no-op.
CREATE INDEX IF NOT EXISTS "cog_user_ts_idx"
  ON "public"."cognitive_signals" USING "btree" ("user_id", "ts" DESC);

CREATE INDEX IF NOT EXISTS "face_user_ts_idx"
  ON "public"."face_signals" USING "btree" ("user_id", "ts" DESC);

CREATE INDEX IF NOT EXISTS "sessions_user_started_idx"
  ON "public"."sessions" USING "btree" ("user_id", "started_at" DESC);

-- 2. Aggregate function ------------------------------------------------------
-- SECURITY INVOKER (the default), not DEFINER: the backend already does its
-- own relationship check before calling this, and INVOKER means RLS still
-- applies if a lower-privileged role ever reaches it. A DEFINER function here
-- would be a ready-made way to read any student's data.
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
-- Same aggregate for many students in one round-trip, so a parent dashboard
-- with several children doesn't call the single-student function once per
-- child.
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

-- Lock execution down to the service role the backend uses.
--
-- REVOKE ... FROM PUBLIC alone is not enough: Supabase grants EXECUTE on new
-- public-schema functions to anon and authenticated by name, and an explicit
-- grant survives a revoke aimed at the PUBLIC pseudo-role -- both roles have
-- to be revoked individually.
--
-- SECURITY INVOKER means RLS would still filter such a caller to rows they
-- can already see, so this is defence in depth rather than the only thing
-- standing in the way -- but a function returning aggregates over a whole
-- table shouldn't be callable by anon.
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer) FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary"("uuid", integer) TO "service_role";

-- Same treatment for the batch variant.
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer) FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer) TO "service_role";

NOTIFY pgrst, 'reload schema';
