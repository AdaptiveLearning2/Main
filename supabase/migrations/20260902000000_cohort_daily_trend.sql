-- Class-wide signal trend for the cohort panels, from signal_daily_rollup
-- only (uncapped, and it outlives expiry). No access or consent decision:
-- the caller must bucket the roster by consent flag pair, or a declining
-- student's rows are read under a classmate's permission.
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
         -- Weighted by trusted_sample_count, each stored average's denominator.
         -- The FILTER keeps a null daily average out of the denominator.
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
         -- Students with a row that day.
         count(DISTINCT "r"."user_id")::bigint                        AS "student_count"
    FROM "public"."signal_daily_rollup" "r"
   WHERE "r"."user_id" = ANY("p_student_ids")
     -- A declined channel is not read.
     AND ("r"."channel" <> 'heart' OR "p_include_heart")
     AND ("r"."channel" <> 'emotion' OR "p_include_emotion")
     -- Whole school-timezone days back from today, inclusive.
     AND "r"."day" >= (("now"() AT TIME ZONE "p_timezone")::date
                        - (GREATEST("p_days", 1) - 1))
     AND "r"."day" <= ("now"() AT TIME ZONE "p_timezone")::date
   GROUP BY 1, 2
   -- Empty days are absent, not zero; the caller keeps them as gaps.
   ORDER BY 1, 2;
$$;

REVOKE ALL ON FUNCTION "public"."class_signal_daily_trend"("uuid"[], integer, boolean, boolean, "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."class_signal_daily_trend"("uuid"[], integer, boolean, boolean, "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."class_signal_daily_trend"("uuid"[], integer, boolean, boolean, "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."class_signal_daily_trend"("uuid"[], integer, boolean, boolean, "text") TO "service_role";

NOTIFY pgrst, 'reload schema';
