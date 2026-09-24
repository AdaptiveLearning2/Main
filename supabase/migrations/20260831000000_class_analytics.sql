-- Teacher analytics aggregates, in Postgres because backend reads are capped
-- oldest-first by _REPORT_ROW_CAP. SECURITY INVOKER, service_role only; they
-- make no access decision, so they are only as safe as the caller's check on
-- the roster it passes.

-- ── indexes the three functions below depend on ────────────────────────────
-- Both access patterns are (key, time-range). On a large table, build
-- CONCURRENTLY by hand first; IF NOT EXISTS then no-ops.
CREATE INDEX IF NOT EXISTS "cog_session_ts_idx"
  ON "public"."cognitive_signals" USING "btree" ("session_id", "ts");

CREATE INDEX IF NOT EXISTS "answers_user_answered_idx"
  ON "public"."session_answers" USING "btree" ("user_id", "answered_at");

-- The composites above make these prefixes redundant; neither backs a constraint.
DROP INDEX IF EXISTS "public"."cog_session_idx";
DROP INDEX IF EXISTS "public"."answers_user_idx";


-- ── 1. answers per school day and hour, for a roster ───────────────────────
-- Serves the accuracy trend and the hour heatmap. Bucketed in p_timezone, never
-- UTC; bounded by the range. Empty buckets are absent and the caller fills them.
CREATE OR REPLACE FUNCTION "public"."class_answer_buckets"(
  "p_user_ids" "uuid"[],
  "p_from" timestamp with time zone,
  "p_to" timestamp with time zone,
  "p_timezone" "text"
) RETURNS TABLE (
  "day" "date",
  "hour" integer,
  "attempted" bigint,
  "correct" bigint
)
LANGUAGE "sql"
STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
  SELECT ("a"."answered_at" AT TIME ZONE "p_timezone")::date         AS "day",
         EXTRACT(HOUR FROM ("a"."answered_at" AT TIME ZONE "p_timezone"))::int AS "hour",
         count(*)                                                    AS "attempted",
         count(*) FILTER (WHERE "a"."correct")                       AS "correct"
    FROM "public"."session_answers" "a"
   WHERE "a"."user_id" = ANY("p_user_ids")
     AND "a"."answered_at" >= "p_from"
     AND "a"."answered_at" < "p_to"
   GROUP BY 1, 2
   ORDER BY 1, 2;
$$;

REVOKE ALL ON FUNCTION "public"."class_answer_buckets"("uuid"[], timestamp with time zone, timestamp with time zone, "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."class_answer_buckets"("uuid"[], timestamp with time zone, timestamp with time zone, "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."class_answer_buckets"("uuid"[], timestamp with time zone, timestamp with time zone, "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."class_answer_buckets"("uuid"[], timestamp with time zone, timestamp with time zone, "text") TO "service_role";


-- ── 2. last active, per student, for a roster ──────────────────────────────
-- "Top 1 per group" has no PostgREST form. Greatest of session end (or start,
-- if open) and last answer. NULL means never active, distinct from a failed read.
CREATE OR REPLACE FUNCTION "public"."last_active_for_users"(
  "p_user_ids" "uuid"[]
) RETURNS TABLE (
  "user_id" "uuid",
  "last_active" timestamp with time zone
)
LANGUAGE "sql"
STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
  SELECT "u"."id" AS "user_id",
         GREATEST(
           (SELECT max(COALESCE("s"."ended_at", "s"."started_at"))
              FROM "public"."sessions" "s"
             WHERE "s"."user_id" = "u"."id"),
           (SELECT max("a"."answered_at")
              FROM "public"."session_answers" "a"
             WHERE "a"."user_id" = "u"."id")
         ) AS "last_active"
    FROM unnest("p_user_ids") AS "u"("id");
$$;

REVOKE ALL ON FUNCTION "public"."last_active_for_users"("uuid"[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."last_active_for_users"("uuid"[]) FROM "anon";
REVOKE ALL ON FUNCTION "public"."last_active_for_users"("uuid"[]) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."last_active_for_users"("uuid"[]) TO "service_role";


-- ── 3. focus against accuracy, for one student ─────────────────────────────
-- Each answer pairs with the nearest same-session focus reading within
-- p_match_seconds; unmatched answers are dropped, never focus 0.
CREATE OR REPLACE FUNCTION "public"."focus_accuracy_for_user"(
  "p_user_id" "uuid",
  "p_from" timestamp with time zone,
  "p_bucket_count" integer,
  "p_match_seconds" integer
) RETURNS "jsonb"
LANGUAGE "sql"
STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
  WITH "pairs" AS (
    SELECT "a"."correct", "c"."focus"
      FROM "public"."session_answers" "a"
      CROSS JOIN LATERAL (
        SELECT "s"."focus"
          FROM "public"."cognitive_signals" "s"
         WHERE "s"."session_id" = "a"."session_id"
           AND "s"."focus" IS NOT NULL
           AND "s"."ts" >= "a"."answered_at" - make_interval(secs => "p_match_seconds")
           AND "s"."ts" <= "a"."answered_at" + make_interval(secs => "p_match_seconds")
         ORDER BY abs(extract(epoch FROM ("s"."ts" - "a"."answered_at")))
         LIMIT 1
      ) "c"
     WHERE "a"."user_id" = "p_user_id"
       AND "a"."answered_at" >= "p_from"
  ),
  "binned" AS (
    -- Clamped: width_bucket puts focus 1.0 in bucket n+1.
    SELECT LEAST(GREATEST(width_bucket("focus", 0, 1, "p_bucket_count"), 1),
                 "p_bucket_count") AS "bucket",
           "correct"
      FROM "pairs"
  )
  SELECT "jsonb_build_object"(
    'n',       (SELECT count(*) FROM "pairs"),
    'r',       (SELECT corr("focus", "correct"::int::double precision) FROM "pairs"),
    -- Order by the bucket number; ordering on the jsonb key sorts as text.
    'buckets', COALESCE((
                 SELECT "jsonb_agg"("g"."b" ORDER BY "g"."bucket")
                   FROM (
                     SELECT "bucket",
                            "jsonb_build_object"(
                              'bucket',    "bucket",
                              'focus_low',  ("bucket" - 1)::double precision / "p_bucket_count",
                              'focus_high', "bucket"::double precision / "p_bucket_count",
                              'answered',  count(*),
                              'correct',   count(*) FILTER (WHERE "correct")
                            ) AS "b"
                       FROM "binned"
                      GROUP BY "bucket"
                   ) "g"
               ), '[]'::"jsonb")
  );
$$;

REVOKE ALL ON FUNCTION "public"."focus_accuracy_for_user"("uuid", timestamp with time zone, integer, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."focus_accuracy_for_user"("uuid", timestamp with time zone, integer, integer) FROM "anon";
REVOKE ALL ON FUNCTION "public"."focus_accuracy_for_user"("uuid", timestamp with time zone, integer, integer) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."focus_accuracy_for_user"("uuid", timestamp with time zone, integer, integer) TO "service_role";

NOTIFY pgrst, 'reload schema';
