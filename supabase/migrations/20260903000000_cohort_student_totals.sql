-- The per-student half of the cohort panels, read from the same table as the
-- trend beside it.
--
-- It used to come from `student_signal_summary_many`, which reads the
-- per-sample tables. That is the right source for the parent dashboard and the
-- weekly report, and the wrong one *here* -- because this roster sits directly
-- beside a chart built on `signal_daily_rollup`, and the two age differently.
-- `expire_signal_rows` deletes the per-sample rows at the end of a school year
-- and leaves the rollup standing, so the panel pair would have shown a full
-- term of class averages above a table reading "No sensor" for every student in
-- it. Two true readings of one class, on one screen, disagreeing -- and the
-- disagreement arrives on a fixed date rather than when anything breaks.
--
-- So this is the same aggregation as `class_signal_daily_trend`, grouped by
-- student rather than by day. Sharing the source is the whole point: the two
-- panels now cannot answer differently, because there is only one set of rows
-- under both.
--
-- SECURITY INVOKER and service_role only, and consent-agnostic for the same
-- reason its sibling is: the backend resolves who owns the class and buckets
-- the roster by consent before calling, and a mixed roster passed under one
-- flag pair would read a declining student's rows under a classmate's
-- permission.
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
         -- Weighted on `trusted_sample_count`, identically to the trend: that
         -- count is already the denominator of every stored average, since
         -- `rollup_signal_day` writes `avg(...)` and Postgres `avg()` skips
         -- nulls. A day that measured nothing has a real count and a null
         -- average, so the FILTER keeps it out of the denominator rather than
         -- letting it pull the student toward zero.
         --
         -- No channel filter is needed on these five: a metric column is null
         -- on every row but its own channel's, so the FILTER already restricts
         -- each sum to the rows that carry it.
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
         -- The counts *do* need the channel, since `trusted_sample_count` is
         -- per row whatever the row measures. These are what tell a tile
         -- "calibrating" (readings arrived, none usable) from "no sensor".
         COALESCE(sum("r"."trusted_sample_count")
                    FILTER (WHERE "r"."channel" = 'cognitive'), 0)::bigint AS "cognitive_samples",
         COALESCE(sum("r"."trusted_sample_count")
                    FILTER (WHERE "r"."channel" = 'heart'), 0)::bigint     AS "heart_samples",
         COALESCE(sum("r"."trusted_sample_count")
                    FILTER (WHERE "r"."channel" = 'emotion'), 0)::bigint   AS "emotion_samples",
         -- Days rather than sessions, and that is deliberate. A session count
         -- would have to come from `sessions`, which is a second table with a
         -- different lifetime -- exactly the split this function exists to
         -- remove. Days recorded comes from the rows the averages were computed
         -- over, so the whole row survives expiry together.
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
