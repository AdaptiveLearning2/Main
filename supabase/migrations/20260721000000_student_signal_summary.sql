-- Lightweight per-student signal averages, aggregated in Postgres.
--
-- The parent dashboard needs four numbers per child (avg focus, avg stress,
-- avg face attention, session count). Computing those by pulling the raw
-- signal rows into the application costs ~10k rows per child on a busy week,
-- on a page that loads every visit. Postgres can return the same four numbers
-- without transferring any rows.

-- 1. Composite indexes -------------------------------------------------------
-- These tables have separate user_id and ts indexes, but every query here
-- filters on both at once ("this student, since this date"). A composite index
-- lets that be a single range scan rather than combining two indexes.
CREATE INDEX IF NOT EXISTS "cog_user_ts_idx"
  ON "public"."cognitive_signals" USING "btree" ("user_id", "ts" DESC);

CREATE INDEX IF NOT EXISTS "face_user_ts_idx"
  ON "public"."face_signals" USING "btree" ("user_id", "ts" DESC);

CREATE INDEX IF NOT EXISTS "sessions_user_started_idx"
  ON "public"."sessions" USING "btree" ("user_id", "started_at" DESC);

-- 2. Aggregate function ------------------------------------------------------
-- Deliberately SECURITY INVOKER (the default), not DEFINER: the backend calls
-- this with the service-role key and does its own relationship check before
-- doing so, and leaving it as INVOKER means RLS still applies if it is ever
-- reached by a lower-privileged role. A DEFINER function here would be a
-- ready-made way to read any student's data.
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
    SELECT avg(c.focus)      AS focus,
           avg(c.stress)     AS stress,
           avg(c.engagement) AS engagement,
           count(*)          AS n
    FROM cognitive_signals c, bounds b
    WHERE c.user_id = p_student_id AND c.ts >= b.since
  ),
  fac AS (
    SELECT avg(f.attention) AS attention,
           count(*)         AS n
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

-- Lock execution down to the service role the backend uses.
--
-- REVOKE ... FROM PUBLIC alone is NOT sufficient here: Supabase ships
-- ALTER DEFAULT PRIVILEGES that grant EXECUTE on new public-schema functions
-- to anon and authenticated *explicitly*, and explicit grants survive a revoke
-- aimed at the PUBLIC pseudo-role. Verified on a local instance -- without the
-- two REVOKEs below, pg_proc.proacl comes back as
--   {postgres=X/postgres,anon=X/postgres,authenticated=X/postgres,...}
--
-- Being SECURITY INVOKER means RLS would still filter such a caller to rows
-- they can already see, so this is defence in depth rather than the only
-- thing standing in the way -- but a function that returns aggregates over a
-- whole table is not something to leave callable by anon.
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer) FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary"("uuid", integer) TO "service_role";

NOTIFY pgrst, 'reload schema';
