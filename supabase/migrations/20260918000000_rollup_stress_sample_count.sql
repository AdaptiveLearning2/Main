-- The daily rollup records how many rows carried a stress value, so the
-- weighted averages over days and students divide by the rows the stored
-- average actually saw.
--
-- `trusted_sample_count` for the cognitive channel is `count(*) FILTER
-- (WHERE focus IS NOT NULL)`, and every rollup-backed average is weighted on
-- it. `avg_stress` was already an approximation under that weight -- focus
-- and calm are derived independently and `contact_poor` is the only thing
-- that nulls both together -- but the local calm's hold rule makes stress
-- absent while focus stands the *ordinary* case: a placeholder calm (the
-- buffer filling, every gap) and a stale one (carried past the hold cap)
-- both null `stress` and keep `focus`. A day of 4000 focus rows with 200
-- fresh calm readings then weighed its stress average as 4000 in the term
-- trend, reading 0.32 against a true 0.70.
--
-- Nullable with no default: a row rolled before this column has no count,
-- and the readers fall back to `trusted_sample_count` for it -- the old
-- approximation, on the rows it was always applied to -- rather than a
-- fabricated zero that would drop the day from every stress average.

ALTER TABLE "public"."signal_daily_rollup"
    ADD COLUMN IF NOT EXISTS "stress_sample_count" bigint;

COMMENT ON COLUMN "public"."signal_daily_rollup"."stress_sample_count" IS
    'cognitive rows with a stress value; NULL on rows rolled before 20260918.';

-- rollup_signal_day: signature unchanged, so a genuine CREATE OR REPLACE.
-- The cognitive INSERT gains the count; heart and emotion are unchanged
-- from 20260917000000.
CREATE OR REPLACE FUNCTION "public"."rollup_signal_day"(
    "p_user_id" "uuid",
    "p_day" date,
    "p_timezone" "text" DEFAULT 'UTC'
) RETURNS void
LANGUAGE "plpgsql"
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
DECLARE
    day_start timestamptz;
    day_end   timestamptz;
BEGIN
    day_start := (("p_day")::timestamp AT TIME ZONE "p_timezone");
    day_end   := (("p_day" + 1)::timestamp AT TIME ZONE "p_timezone");

    INSERT INTO signal_daily_rollup AS r (
        user_id, day, channel, avg_focus, avg_stress, avg_engagement,
        sample_count, trusted_sample_count, stress_sample_count,
        score_scale_min, score_scale_max, updated_at)
    SELECT p_user_id, p_day, 'cognitive',
           avg(focus), avg(stress), avg(engagement),
           count(*), count(*) FILTER (WHERE focus IS NOT NULL),
           count(*) FILTER (WHERE stress IS NOT NULL),
           -- Scale 3 is scale 2 with calm from the local spectrum: it moves
           -- stress and not focus. A row on it whose stress is NULL (a
           -- placeholder or a held calm) contributed only a focus, which is
           -- on scale 2, and reads as 2 -- unless the day also holds a
           -- scale-3 row *with* a stress, in which case 3 already covers it
           -- and reading it as 2 would fabricate a split: every local
           -- session's first ticks hold calm while the buffer fills, and
           -- mapped to 2 unconditionally every such session read 2..3 on its
           -- own. Read as 2 only when no local stress was scored, a day of
           -- placeholder calms beside an sdk day reads 2..2 (no caption --
           -- the local source scored no stress), and a day of pre-label
           -- rows beside held local rows reads 1..2 (a caption -- the focus
           -- average did mix two scales). Excluding the held rows instead
           -- gave that last day 1..1.
           min(CASE WHEN sc = 3 AND stress IS NULL AND NOT scored_local THEN 2 ELSE sc END)
               FILTER (WHERE focus IS NOT NULL),
           max(CASE WHEN sc = 3 AND stress IS NULL AND NOT scored_local THEN 2 ELSE sc END)
               FILTER (WHERE focus IS NOT NULL),
           now()
    FROM (SELECT focus, stress, engagement, sc,
                 COALESCE(bool_or(sc = 3 AND stress IS NOT NULL) OVER (), false) AS scored_local
            FROM (SELECT focus, stress, engagement,
                         -- A row with no key, and a row with no `raw` at
                         -- all, predates the label: scale 1.
                         -- `raw ? 'score_scale'` is NULL on a NULL raw, so
                         -- the null test comes first.
                         CASE WHEN raw IS NULL OR NOT (raw ? 'score_scale') THEN 1
                              ELSE public.score_scale_of(raw) END AS sc
                    FROM cognitive_signals
                   WHERE user_id = p_user_id AND ts >= day_start AND ts < day_end) y) x
    HAVING count(*) > 0
    ON CONFLICT (user_id, day, channel) DO UPDATE SET
        avg_focus = EXCLUDED.avg_focus,
        avg_stress = EXCLUDED.avg_stress,
        avg_engagement = EXCLUDED.avg_engagement,
        sample_count = EXCLUDED.sample_count,
        trusted_sample_count = EXCLUDED.trusted_sample_count,
        stress_sample_count = EXCLUDED.stress_sample_count,
        score_scale_min = EXCLUDED.score_scale_min,
        score_scale_max = EXCLUDED.score_scale_max,
        updated_at = EXCLUDED.updated_at;

    INSERT INTO signal_daily_rollup AS r (
        user_id, day, channel, avg_heart_rate_bpm, avg_rmssd_ms,
        avg_stress_score, heart_sources, stress_counts,
        sample_count, trusted_sample_count, updated_at)
    SELECT p_user_id, p_day, 'heart',
           avg(heart_rate_bpm) FILTER (WHERE trusted),
           avg(rmssd_ms)       FILTER (WHERE trusted),
           avg(stress_score)   FILTER (WHERE trusted),
           (SELECT array_agg(DISTINCT h2.source)
            FROM heart_signals h2
            WHERE h2.user_id = p_user_id
              AND h2.ts >= day_start AND h2.ts < day_end
              AND h2.source IS NOT NULL),
           (SELECT jsonb_object_agg(c.stress_category, c.n)
            FROM (SELECT stress_category, count(*) AS n
                  FROM heart_signals
                  WHERE user_id = p_user_id
                    AND ts >= day_start AND ts < day_end
                    AND trusted AND stress_category IS NOT NULL
                  GROUP BY stress_category) c),
           count(*), count(*) FILTER (WHERE trusted), now()
    FROM heart_signals
    WHERE user_id = p_user_id AND ts >= day_start AND ts < day_end
    HAVING count(*) > 0
    ON CONFLICT (user_id, day, channel) DO UPDATE SET
        avg_heart_rate_bpm = EXCLUDED.avg_heart_rate_bpm,
        avg_rmssd_ms = EXCLUDED.avg_rmssd_ms,
        avg_stress_score = EXCLUDED.avg_stress_score,
        heart_sources = EXCLUDED.heart_sources,
        stress_counts = EXCLUDED.stress_counts,
        sample_count = EXCLUDED.sample_count,
        trusted_sample_count = EXCLUDED.trusted_sample_count,
        updated_at = EXCLUDED.updated_at;

    INSERT INTO signal_daily_rollup AS r (
        user_id, day, channel, emotion_counts,
        sample_count, trusted_sample_count, updated_at)
    SELECT p_user_id, p_day, 'emotion',
           (SELECT jsonb_object_agg(e.emotion, e.n)
            FROM (SELECT emotion, count(*) AS n
                  FROM face_signals
                  WHERE user_id = p_user_id
                    AND ts >= day_start AND ts < day_end
                    AND emotion_trusted AND emotion IS NOT NULL
                  GROUP BY emotion) e),
           count(*) FILTER (WHERE emotion IS NOT NULL),
           count(*) FILTER (WHERE emotion_trusted), now()
    FROM face_signals
    WHERE user_id = p_user_id AND ts >= day_start AND ts < day_end
    HAVING count(*) > 0
    ON CONFLICT (user_id, day, channel) DO UPDATE SET
        emotion_counts = EXCLUDED.emotion_counts,
        sample_count = EXCLUDED.sample_count,
        trusted_sample_count = EXCLUDED.trusted_sample_count,
        updated_at = EXCLUDED.updated_at;
END;
$$;

REVOKE ALL ON FUNCTION "public"."rollup_signal_day"("uuid", date, "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."rollup_signal_day"("uuid", date, "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."rollup_signal_day"("uuid", date, "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."rollup_signal_day"("uuid", date, "text") TO "service_role";

-- class_signal_daily_trend: the return table gains `stress_sample_count`,
-- which is a new signature, so the old one is dropped rather than left as
-- an overload. `avg_stress` is weighted on the stress count, falling back
-- per row to `trusted_sample_count` where the row predates the column.
DROP FUNCTION IF EXISTS "public"."class_signal_daily_trend"("uuid"[], integer, boolean, boolean, "text");

CREATE FUNCTION "public"."class_signal_daily_trend"(
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
  "stress_sample_count" bigint,
  "student_count" bigint
)
LANGUAGE "sql"
STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
  SELECT "r"."day",
         "r"."channel",
         sum("r"."avg_focus" * "r"."trusted_sample_count")
           / NULLIF(sum("r"."trusted_sample_count")
                      FILTER (WHERE "r"."avg_focus" IS NOT NULL), 0) AS "avg_focus",
         sum("r"."avg_stress" * COALESCE("r"."stress_sample_count", "r"."trusted_sample_count"))
           / NULLIF(sum(COALESCE("r"."stress_sample_count", "r"."trusted_sample_count"))
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
         -- Summed over the rows that carry a stress average, so the caller
         -- can re-weight across consent buckets on the same denominator.
         sum(COALESCE("r"."stress_sample_count", "r"."trusted_sample_count"))
           FILTER (WHERE "r"."avg_stress" IS NOT NULL)::bigint         AS "stress_sample_count",
         count(DISTINCT "r"."user_id")::bigint                        AS "student_count"
    FROM "public"."signal_daily_rollup" "r"
   WHERE "r"."user_id" = ANY("p_student_ids")
     AND ("r"."channel" <> 'heart' OR "p_include_heart")
     AND ("r"."channel" <> 'emotion' OR "p_include_emotion")
     AND "r"."day" >= (("now"() AT TIME ZONE "p_timezone")::date
                        - (GREATEST("p_days", 1) - 1))
     AND "r"."day" <= ("now"() AT TIME ZONE "p_timezone")::date
   GROUP BY 1, 2
   ORDER BY 1, 2;
$$;

REVOKE ALL ON FUNCTION "public"."class_signal_daily_trend"("uuid"[], integer, boolean, boolean, "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."class_signal_daily_trend"("uuid"[], integer, boolean, boolean, "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."class_signal_daily_trend"("uuid"[], integer, boolean, boolean, "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."class_signal_daily_trend"("uuid"[], integer, boolean, boolean, "text") TO "service_role";

-- class_signal_student_totals: signature unchanged; only the stress weight.
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
         sum("r"."avg_focus" * "r"."trusted_sample_count")
           / NULLIF(sum("r"."trusted_sample_count")
                      FILTER (WHERE "r"."avg_focus" IS NOT NULL), 0) AS "avg_focus",
         sum("r"."avg_stress" * COALESCE("r"."stress_sample_count", "r"."trusted_sample_count"))
           / NULLIF(sum(COALESCE("r"."stress_sample_count", "r"."trusted_sample_count"))
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
         COALESCE(sum("r"."trusted_sample_count")
                    FILTER (WHERE "r"."channel" = 'cognitive'), 0)::bigint AS "cognitive_samples",
         COALESCE(sum("r"."trusted_sample_count")
                    FILTER (WHERE "r"."channel" = 'heart'), 0)::bigint     AS "heart_samples",
         COALESCE(sum("r"."trusted_sample_count")
                    FILTER (WHERE "r"."channel" = 'emotion'), 0)::bigint   AS "emotion_samples",
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
