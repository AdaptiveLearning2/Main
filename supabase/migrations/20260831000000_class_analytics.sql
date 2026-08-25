-- Aggregates for the teacher analytics panels (class accuracy trend, the
-- time-of-day heatmap, a real last-active column, and focus-vs-accuracy).
--
-- All three aggregate in Postgres rather than in the backend, for the reason
-- `rollup_signal_day` already does: the reporting path caps its raw reads at
-- `_REPORT_ROW_CAP`, and that cap trims oldest-first. A class of thirty
-- answering fifty questions a day is 45,000 rows a month -- far past the cap --
-- so a Python-side average would silently describe only the recent tail while
-- the early days read as a quiet term. That is exactly the trap the term-trend
-- endpoint was built to avoid, one surface over.
--
-- All three are SECURITY INVOKER and granted to service_role only. They make
-- no access decision: the backend resolves who owns the class, or who may view
-- the student, before it calls them. Note in particular that the roster
-- arrives as a `uuid[]` the caller assembled -- that is deliberate (a class of
-- thirty is one call, not thirty) and it means these functions are only ever
-- as safe as the check that ran before them.

-- ── indexes the three functions below depend on ────────────────────────────
--
-- `cognitive_signals` has `session_id` and `ts` indexed separately, and
-- `session_answers` has `user_id` alone. Both new access patterns are
-- (key, time-range), which a single-column index serves by scanning the whole
-- key's rows and filtering. For `focus_accuracy_for_user` that is a nested
-- scan per answer over an hour-long session's ~14,000 cognitive rows.
--
-- `CREATE INDEX CONCURRENTLY` is not available here -- Supabase wraps each
-- migration in a transaction -- so this takes an ACCESS EXCLUSIVE lock while
-- building. On a deployment where these tables are already large, build both
-- by hand with CONCURRENTLY outside a transaction first; the IF NOT EXISTS
-- then makes this a no-op.
CREATE INDEX IF NOT EXISTS "cog_session_ts_idx"
  ON "public"."cognitive_signals" USING "btree" ("session_id", "ts");

CREATE INDEX IF NOT EXISTS "answers_user_answered_idx"
  ON "public"."session_answers" USING "btree" ("user_id", "answered_at");


-- ── 1. answers per school day and hour, for a roster ───────────────────────
--
-- One function serves both the accuracy trend and the time-of-day heatmap.
-- They are two readings of the same grouping -- sum the hours for a day, or
-- sum the days for an hour-of-week -- and splitting them into two functions
-- would be two scans of the same rows to answer one question each.
--
-- Bucketing is done at `p_timezone`, never UTC. The school day is what a
-- teacher means by "Tuesday", and against a UTC clock a late-afternoon lesson
-- in Los Angeles lands on the following day. The hour matters even more here:
-- the whole point of a time-of-day chart is which hour of the school day it
-- was, so a UTC hour would shift every column by the offset.
--
-- The result is bounded by the *range*, not by the number of answers: 30 days
-- is at most 720 rows however busy the class, so no cap is needed and none is
-- applied. Days and hours with no answers are simply absent -- the caller
-- fills the empty ones, because a day dropped from a series renders as the
-- days either side sitting adjacent.
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
--
-- "Top 1 per group" has no PostgREST form -- one `in_` query ordered by time
-- returns the newest rows overall, which is one busy student's -- so the
-- roster column this backs was simply absent rather than wrong. Same reason
-- `my_children` still reads five sessions per child in a loop.
--
-- The greatest of two clocks, because neither alone is "last active":
--   * a session records `started_at` and, once closed, `ended_at`. An open
--     session has a null `ended_at`, so `coalesce` falls back to its start
--     rather than dropping a student who is working right now.
--   * an answer records `answered_at`, which is the finer signal -- a student
--     mid-lesson answered more recently than their session began.
--
-- NULL for a student who has never started a session, which the caller must
-- keep apart from a failed read. "Never active" is a real fact about a
-- roster; "we could not find out" is not the same claim.
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
--
-- Each answer is paired with the focus reading nearest it in time, within
-- `p_match_seconds`, from the same session. Same session rather than same
-- student because a session is one student's sitting: a reading from a
-- different session is a different lesson on a different day, and joining on
-- user_id alone would pair an answer with whatever happened to be closest
-- across the whole term.
--
-- An answer with no reading in range is dropped, not counted as focus 0.
-- Sessions run with the headband off, and a zero would drag exactly the
-- unmeasured answers to the bottom of the correlation.
--
-- `corr()` returns NULL below two pairs, which is the honest answer and is
-- passed straight out. The caller applies its own, much higher, minimum
-- before showing a number at all -- a correlation over a handful of answers is
-- noise, and this one renders to a teacher as an objective-looking figure.
--
-- Returned as one jsonb object rather than a row set because it is two shapes
-- at once (a scalar summary and a series of buckets), and two functions would
-- be two scans of the same lateral join.
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
    -- width_bucket answers 0 below the range and n+1 at or above the top, so
    -- a focus of exactly 1.0 would land in a bucket of its own. Clamped into
    -- the intended range instead.
    SELECT LEAST(GREATEST(width_bucket("focus", 0, 1, "p_bucket_count"), 1),
                 "p_bucket_count") AS "bucket",
           "correct"
      FROM "pairs"
  )
  SELECT "jsonb_build_object"(
    'n',       (SELECT count(*) FROM "pairs"),
    'r',       (SELECT corr("focus", "correct"::int::double precision) FROM "pairs"),
    -- Ordered by the bucket *number*, not by the rendered object: aggregating
    -- with `ORDER BY b->>'bucket'` sorts the key as text, which puts bucket 10
    -- between 1 and 2 and draws the series folded back on itself.
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
