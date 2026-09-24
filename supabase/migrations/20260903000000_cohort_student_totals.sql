-- Per-student half of the cohort panels: class_signal_daily_trend's
-- aggregation grouped by student, on the same rollup so the two panels cannot
-- disagree after expiry. Like its sibling, the caller must bucket the roster
-- by consent flag pair.
CREATE OR REPLACE FUNCTION "public"."class_signal_student_totals"(
  "p_student_ids" "uuid"[],
  "p_days" integer DEFAULT 14,
  "p_include_heart" boolean DEFAULT true,
  "p_include_emotion" boolean DEFAULT true,
  "p_timezone" "text" DEFAULT 'UTC'
) RETURNS TABLE (
  "user_id" "uuid",
  "avg_focus" double precision,
  "avg_stress" double precision,
  "avg_engagement" double precision,
  "avg_heart_rate_bpm" double precision,
  "avg_rmssd_ms" double precision,
  "cognitive_samples" bigint,
  "heart_samples" bigint,
  "emotion_samples" bigint,
  "days_recorded" bigint
)
LANGUAGE "sql"
STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
  SELECT "r"."user_id",
         -- Weighted as in the trend. A metric is null outside its own channel,
         -- so the FILTER already restricts each sum.
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
         -- Counts need the channel filter; they tell "calibrating" from "no sensor".
         COALESCE(sum("r"."trusted_sample_count")
                    FILTER (WHERE "r"."channel" = 'cognitive'), 0)::bigint AS "cognitive_samples",
         COALESCE(sum("r"."trusted_sample_count")
                    FILTER (WHERE "r"."channel" = 'heart'), 0)::bigint     AS "heart_samples",
         COALESCE(sum("r"."trusted_sample_count")
                    FILTER (WHERE "r"."channel" = 'emotion'), 0)::bigint   AS "emotion_samples",
         -- Days, not sessions: `sessions` has a different lifetime.
         count(DISTINCT "r"."day")::bigint                                 AS "days_recorded"
    FROM "public"."signal_daily_rollup" "r"
   WHERE "r"."user_id" = ANY("p_student_ids")
     AND ("r"."channel" <> 'heart' OR "p_include_heart")
     AND ("r"."channel" <> 'emotion' OR "p_include_emotion")
     AND "r"."day" >= (("now"() AT TIME ZONE "p_timezone")::date
                        - (GREATEST("p_days", 1) - 1))
     AND "r"."day" <= ("now"() AT TIME ZONE "p_timezone")::date
   GROUP BY "r"."user_id";
$$;

REVOKE ALL ON FUNCTION "public"."class_signal_student_totals"("uuid"[], integer, boolean, boolean, "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."class_signal_student_totals"("uuid"[], integer, boolean, boolean, "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."class_signal_student_totals"("uuid"[], integer, boolean, boolean, "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."class_signal_student_totals"("uuid"[], integer, boolean, boolean, "text") TO "service_role";

NOTIFY pgrst, 'reload schema';
