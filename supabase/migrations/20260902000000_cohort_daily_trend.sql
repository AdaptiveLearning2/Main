-- The class-wide signal trend behind the cohort panels on ClassDetail.
--
-- Reads `signal_daily_rollup` and nothing else, for the same two reasons the
-- term trend does: the per-sample tables are capped on read and the cap trims
-- oldest-first, so the early days of a range would come back empty and read as
-- a quiet fortnight rather than as rows nobody fetched -- and the rollup is the
-- only copy that outlives `expire_signal_rows`, which is exactly when a term's
-- worth of trend is most likely to be read.
--
-- SECURITY INVOKER and service_role only, like the three class analytics
-- functions beside it. It makes no access decision: the backend resolves who
-- owns the class before it calls, and the roster arrives as a `uuid[]` the
-- caller assembled -- so this is only ever as safe as the check that ran first.
--
-- Consent is likewise not this function's job, and that division has teeth.
-- `p_include_heart` / `p_include_emotion` gate what is *read*, but the caller
-- must never hand it a roster with mixed consent and one flag pair: a class
-- where one student declined the headband and the rest permitted it has to
-- arrive as two calls, or the declining student's heart rows are read under a
-- classmate's permission. The backend buckets by flag pair for that reason --
-- the same shape `my_children` already uses.
CREATE OR REPLACE FUNCTION "public"."class_signal_daily_trend"(
  "p_student_ids" "uuid"[],
  "p_days" integer DEFAULT 14,
  "p_include_heart" boolean DEFAULT true,
  "p_include_emotion" boolean DEFAULT true,
  "p_timezone" "text" DEFAULT 'UTC'
) RETURNS TABLE (
  "day" "date",
  "channel" "text",
  "avg_focus" double precision,
  "avg_stress" double precision,
  "avg_engagement" double precision,
  "avg_heart_rate_bpm" double precision,
  "avg_rmssd_ms" double precision,
  "sample_count" bigint,
  "trusted_sample_count" bigint,
  "student_count" bigint
)
LANGUAGE "sql"
STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
  SELECT "r"."day",
         "r"."channel",
         -- Weighted by `trusted_sample_count`, and that is derived rather than
         -- chosen. `rollup_signal_day` writes `avg(focus)` for cognitive and
         -- `avg(...) FILTER (WHERE trusted)` for heart; Postgres `avg()` skips
         -- nulls, so the trusted count is already the denominator of every
         -- stored average. Weighting by `sample_count` would divide by rows
         -- the average never saw, and a plain `avg(avg_x)` across students
         -- would weight a four-sample student like a four-hundred-sample one.
         --
         -- The FILTER on the denominator is what keeps a null daily average
         -- contributing nothing rather than zero: a student whose electrode
         -- contact failed all day has a null `avg_focus` and a non-zero count,
         -- and including that count would drag the class mean down by exactly
         -- the students who measured nothing.
         sum("r"."avg_focus" * "r"."trusted_sample_count")
           / NULLIF(sum("r"."trusted_sample_count")
                      FILTER (WHERE "r"."avg_focus" IS NOT NULL), 0) AS "avg_focus",
         sum("r"."avg_stress" * "r"."trusted_sample_count")
           / NULLIF(sum("r"."trusted_sample_count")
                      FILTER (WHERE "r"."avg_stress" IS NOT NULL), 0) AS "avg_stress",
         sum("r"."avg_engagement" * "r"."trusted_sample_count")
           / NULLIF(sum("r"."trusted_sample_count")
                      FILTER (WHERE "r"."avg_engagement" IS NOT NULL), 0) AS "avg_engagement",
         sum("r"."avg_heart_rate_bpm" * "r"."trusted_sample_count")
           / NULLIF(sum("r"."trusted_sample_count")
                      FILTER (WHERE "r"."avg_heart_rate_bpm" IS NOT NULL), 0) AS "avg_heart_rate_bpm",
         sum("r"."avg_rmssd_ms" * "r"."trusted_sample_count")
           / NULLIF(sum("r"."trusted_sample_count")
                      FILTER (WHERE "r"."avg_rmssd_ms" IS NOT NULL), 0) AS "avg_rmssd_ms",
         sum("r"."sample_count")::bigint                              AS "sample_count",
         sum("r"."trusted_sample_count")::bigint                      AS "trusted_sample_count",
         -- How many students contributed to this day at all, which is what
         -- makes a one-student Tuesday readable as such beside a full class's
         -- Wednesday. Counted over rows present, so it never exceeds the
         -- roster and never implies a student who recorded nothing.
         count(DISTINCT "r"."user_id")::bigint                        AS "student_count"
    FROM "public"."signal_daily_rollup" "r"
   WHERE "r"."user_id" = ANY("p_student_ids")
     -- A declined channel is not read, rather than read and nulled on the way
     -- out. Same rule as `student_signal_summary_many` and the term trend:
     -- never fall back to a query that reads what the caller opted out of.
     AND ("r"."channel" <> 'heart' OR "p_include_heart")
     AND ("r"."channel" <> 'emotion' OR "p_include_emotion")
     -- Whole days back from today in the school's own timezone, inclusive of
     -- both ends. `p_days` is clamped by the caller; GREATEST here only stops
     -- a zero or negative reaching the arithmetic.
     AND "r"."day" >= (("now"() AT TIME ZONE "p_timezone")::date
                        - (GREATEST("p_days", 1) - 1))
     AND "r"."day" <= ("now"() AT TIME ZONE "p_timezone")::date
   GROUP BY 1, 2
   -- Days with nothing recorded are absent rather than zero-filled. The caller
   -- keeps them as gaps: a fortnight of half-term rendered as the days either
   -- side sitting adjacent is a claim the data does not support.
   ORDER BY 1, 2;
$$;

REVOKE ALL ON FUNCTION "public"."class_signal_daily_trend"("uuid"[], integer, boolean, boolean, "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."class_signal_daily_trend"("uuid"[], integer, boolean, boolean, "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."class_signal_daily_trend"("uuid"[], integer, boolean, boolean, "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."class_signal_daily_trend"("uuid"[], integer, boolean, boolean, "text") TO "service_role";

NOTIFY pgrst, 'reload schema';
