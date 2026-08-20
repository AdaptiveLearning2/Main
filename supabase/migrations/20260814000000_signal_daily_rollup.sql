-- The daily rollup.
--
-- One row per student per day per channel, written **as sessions close**, not
-- generated at expiry. Writing it continuously means it is never a race against
-- the delete job, and it doubles as the read path for historical weeks long
-- before anything is deleted.
--
-- This is what makes deleting per-sample rows on `ends_on` survivable: what
-- goes is the detail, not the year. The delete job (a later migration) refuses
-- to delete a day that has no rollup row, because a bug in this writer would
-- otherwise become silent permanent data loss on a fixed date -- the one
-- failure mode here with no recovery.

CREATE TABLE IF NOT EXISTS "public"."signal_daily_rollup" (
    "user_id" "uuid" NOT NULL REFERENCES "public"."profiles"("id") ON DELETE CASCADE,
    -- The school's calendar day, resolved in `retention_window.timezone` -- the
    -- same day the weekly report buckets by. A rollup keyed on UTC days could
    -- not answer the report's questions without re-deriving a boundary that has
    -- already been decided once.
    "day" date NOT NULL,
    "channel" "text" NOT NULL,

    -- cognitive
    "avg_focus" double precision,
    "avg_stress" double precision,
    "avg_engagement" double precision,

    -- heart. Absolute units, unlike the 0..1 ratios above; a consumer scaling
    -- these as percentages draws a 72 bpm day at 7200%.
    "avg_heart_rate_bpm" double precision,
    "avg_rmssd_ms" double precision,
    "avg_stress_score" double precision,
    -- Which sensors contributed. Accuracy differs materially between the
    -- headband and the camera, so a reader comparing two weeks needs to see
    -- that the sensor changed -- and once the raw rows are gone this column is
    -- the only place that survives.
    "heart_sources" "text"[],

    -- The two pie distributions, kept as counts rather than as a winner: the
    -- shape of a day is not recoverable from its most frequent label.
    "emotion_counts" "jsonb",
    "stress_counts" "jsonb",

    -- How much was behind the averages, so a thin day stays visibly thin after
    -- the detail is gone. Without these a day with four samples and a day with
    -- four thousand are indistinguishable once the raw rows are deleted, and
    -- the first is not evidence of anything.
    "sample_count" integer NOT NULL DEFAULT 0,
    -- Rows that produced a usable measurement, which is defined per channel
    -- because the channels gate differently: `trusted` for heart,
    -- `emotion_trusted` for emotion, and for cognitive -- which has no trust
    -- flag -- a row whose measurements survived the contact check rather than
    -- arriving nulled. Never equal to sample_count by construction: a headband
    -- with poor contact writes rows with null measurements deliberately, so
    -- "recording but unable to measure" stays distinguishable from "no session".
    "trusted_sample_count" integer NOT NULL DEFAULT 0,

    "updated_at" timestamptz NOT NULL DEFAULT "now"(),

    PRIMARY KEY ("user_id", "day", "channel"),
    CONSTRAINT "signal_daily_rollup_channel" CHECK (
        "channel" IN ('cognitive', 'heart', 'emotion')),
    CONSTRAINT "signal_daily_rollup_counts" CHECK (
        "trusted_sample_count" <= "sample_count")
);

-- The delete job's completeness check asks "is there a rollup for this day",
-- and the report asks "give me these days for this student". Both are covered
-- by the primary key's leading columns; this index serves the job's other
-- shape, which sweeps by day across all students.
CREATE INDEX IF NOT EXISTS "signal_daily_rollup_day_idx"
    ON "public"."signal_daily_rollup" ("day");

-- ── access ──────────────────────────────────────────────────────────────────
--
-- Revoke before granting: Supabase's ALTER DEFAULT PRIVILEGES has already given
-- anon and authenticated every privilege by name, so a bare GRANT would read
-- like a restriction and change nothing (CLAUDE.md).

REVOKE ALL ON TABLE "public"."signal_daily_rollup" FROM "anon";
REVOKE ALL ON TABLE "public"."signal_daily_rollup" FROM "authenticated";
GRANT SELECT ON TABLE "public"."signal_daily_rollup" TO "authenticated";
GRANT ALL ON TABLE "public"."signal_daily_rollup" TO "service_role";

ALTER TABLE "public"."signal_daily_rollup" ENABLE ROW LEVEL SECURITY;

-- Read-your-own, matching the per-sample tables this summarises. Nothing in the
-- frontend reads it today -- the reporting endpoints do, through the
-- service-role client -- but a summary of a student's own data should not be
-- harder to reach than the samples it came from, and with no policy at all a
-- later direct read would fail in a way that looks like a bug rather than a
-- decision.
--
-- SELECT only. There is no insert/update/delete policy for anyone, so with RLS
-- on, PostgREST cannot write this table whatever JWT it carries: the rollup is
-- derived data and the only correct writer is the function below.
CREATE POLICY "own rollup readable" ON "public"."signal_daily_rollup"
    FOR SELECT TO "authenticated"
    USING ("auth"."uid"() = "user_id");


-- ── the writer ──────────────────────────────────────────────────────────────
--
-- A function rather than backend code, because the aggregation has to happen
-- where the rows are. A day holds thousands of samples at 1-4Hz, and the
-- reporting path already caps its reads at _REPORT_ROW_CAP -- pulling rows into
-- Python to average them would either be slow or silently average a capped
-- subset, and a rollup that is quietly wrong is worse than none at all, because
-- it is what survives the delete.
--
-- **Recomputes rather than accumulates.** Idempotent by construction: closing
-- two sessions on the same day, or replaying a close, converges on the same
-- answer. An incremental writer would have to be exactly-once, which nothing
-- here can promise.
--
-- SECURITY INVOKER (the default, stated for emphasis): called by the backend's
-- service-role client, which already reads these tables. A definer function
-- aggregating across every student's signals is a ready-made way to read
-- anyone's data if it is ever reachable by a lesser role.
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
    -- Local wall-clock midnight to local wall-clock midnight. `AT TIME ZONE`
    -- on a naive timestamp reads it *as* that zone and yields the instant,
    -- which is the conversion wanted here; a UTC-based range would slice the
    -- day several hours off and disagree with the report's own buckets.
    day_start := (("p_day")::timestamp AT TIME ZONE "p_timezone");
    day_end   := (("p_day" + 1)::timestamp AT TIME ZONE "p_timezone");

    -- ── cognitive ──
    -- `focus IS NOT NULL` is the usable count: a headband with poor contact
    -- writes a row with every measurement nulled on purpose, so that the
    -- session stays visible as "recording, unable to measure". Counting those
    -- as trusted would report a day of bad contact as a day of good data.
    INSERT INTO signal_daily_rollup AS r (
        user_id, day, channel, avg_focus, avg_stress, avg_engagement,
        sample_count, trusted_sample_count, updated_at)
    SELECT p_user_id, p_day, 'cognitive',
           avg(focus), avg(stress), avg(engagement),
           count(*), count(*) FILTER (WHERE focus IS NOT NULL), now()
    FROM cognitive_signals
    WHERE user_id = p_user_id AND ts >= day_start AND ts < day_end
    HAVING count(*) > 0
    ON CONFLICT (user_id, day, channel) DO UPDATE SET
        avg_focus = EXCLUDED.avg_focus,
        avg_stress = EXCLUDED.avg_stress,
        avg_engagement = EXCLUDED.avg_engagement,
        sample_count = EXCLUDED.sample_count,
        trusted_sample_count = EXCLUDED.trusted_sample_count,
        updated_at = EXCLUDED.updated_at;

    -- ── heart ──
    -- Averages over **trusted rows only**, matching every heart figure the
    -- weekly report already publishes. An untrusted reading is one the quality
    -- gate rejected, and averaging it in here would smuggle it past that gate
    -- permanently -- the raw row it came from is the thing that gets deleted.
    INSERT INTO signal_daily_rollup AS r (
        user_id, day, channel, avg_heart_rate_bpm, avg_rmssd_ms,
        avg_stress_score, heart_sources, stress_counts,
        sample_count, trusted_sample_count, updated_at)
    SELECT p_user_id, p_day, 'heart',
           avg(heart_rate_bpm) FILTER (WHERE trusted),
           avg(rmssd_ms)       FILTER (WHERE trusted),
           avg(stress_score)   FILTER (WHERE trusted),
           -- Every source seen that day, trusted or not: the point of this
           -- column is to explain a change in the numbers, and a sensor whose
           -- readings were all rejected is exactly such an explanation.
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

    -- ── emotion ──
    -- Counts rather than a dominant label, and trusted only: FER+ is the
    -- weakest signal in the system and an untrusted label is one the confidence
    -- gate already refused.
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
           count(*), count(*) FILTER (WHERE emotion_trusted), now()
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

-- Postgres grants EXECUTE on new functions to PUBLIC automatically, and
-- Supabase additionally grants it to anon and authenticated **by name** --
-- explicit grants that a revoke aimed at PUBLIC does not touch. All three are
-- needed. This one writes derived data about every student, so it is not a
-- function to leave ambiently callable.
REVOKE ALL ON FUNCTION "public"."rollup_signal_day"("uuid", date, "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."rollup_signal_day"("uuid", date, "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."rollup_signal_day"("uuid", date, "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."rollup_signal_day"("uuid", date, "text") TO "service_role";

NOTIFY pgrst, 'reload schema';
