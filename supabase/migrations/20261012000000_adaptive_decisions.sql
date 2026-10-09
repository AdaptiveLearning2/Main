-- Why each served adaptive question was eased, raised or held, written by `_record_adaptive_decision`.
-- Signal-derived: it expires with the per-sample rows, goes with any erasure, and admins read aggregates only.

CREATE TABLE IF NOT EXISTS "public"."adaptive_decisions" (
    "id"         bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    "session_id" uuid        NOT NULL REFERENCES "public"."sessions"("id") ON DELETE CASCADE,
    "user_id"    uuid        NOT NULL,
    "served_at"  timestamptz NOT NULL DEFAULT now(),
    "served_from" text       CHECK ("served_from" IN ('inline', 'queue')),
    "difficulty" text,
    "bias"       smallint    NOT NULL CHECK ("bias" BETWEEN -1 AND 1),
    -- Must equal LLM_topic_decider.BIAS_WHYS.
    "why"        text        NOT NULL CHECK ("why" IN ('stressed', 'manual', 'facial_veto', 'recent_misses',
                                                       'focused', 'correct_run', 'nothing_to_act_on')),
    -- The fused label, a fixed vocabulary (signal_fusion.FusedState); never a reading.
    "label"      text        NOT NULL CHECK ("label" IN ('focused', 'stressed', 'neutral',
                                                         'insufficient_signal', 'no_eeg')),
    -- Consent channels whose sensor had an opinion; drives the withdrawn-channel exclusion.
    "opinions"   text[]      NOT NULL DEFAULT '{}'
        CHECK ("opinions" <@ ARRAY['eeg', 'headband_optical', 'camera']),
    "increase_withheld" boolean NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS "adaptive_decisions_served_at_idx" ON "public"."adaptive_decisions" ("served_at");
CREATE INDEX IF NOT EXISTS "adaptive_decisions_user_idx" ON "public"."adaptive_decisions" ("user_id");

REVOKE ALL ON TABLE "public"."adaptive_decisions" FROM "anon";
REVOKE ALL ON TABLE "public"."adaptive_decisions" FROM "authenticated";
GRANT ALL ON TABLE "public"."adaptive_decisions" TO "service_role";
REVOKE ALL ON SEQUENCE "public"."adaptive_decisions_id_seq" FROM "anon";
REVOKE ALL ON SEQUENCE "public"."adaptive_decisions_id_seq" FROM "authenticated";
GRANT ALL ON SEQUENCE "public"."adaptive_decisions_id_seq" TO "service_role";

-- RLS with no policies; the revokes cover TRUNCATE.
ALTER TABLE "public"."adaptive_decisions" ENABLE ROW LEVEL SECURITY;


-- ─── erasure: any channel takes every decision, since a label mixes channels ──
-- A trigger on the erasure record, so it runs inside `erase_signals`' transaction without copying it.
CREATE OR REPLACE FUNCTION "public"."erase_adaptive_decisions"()
RETURNS trigger
LANGUAGE "plpgsql"
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
BEGIN
    DELETE FROM adaptive_decisions WHERE user_id = NEW.user_id;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION "public"."erase_adaptive_decisions"() FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."erase_adaptive_decisions"() FROM "anon";
REVOKE ALL ON FUNCTION "public"."erase_adaptive_decisions"() FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."erase_adaptive_decisions"() TO "service_role";

DROP TRIGGER IF EXISTS "signal_erasure_takes_adaptive_decisions" ON "public"."signal_erasure";
CREATE TRIGGER "signal_erasure_takes_adaptive_decisions"
    AFTER INSERT OR UPDATE ON "public"."signal_erasure"
    FOR EACH ROW EXECUTE FUNCTION "public"."erase_adaptive_decisions"();


-- ─── expiry: the per-sample cutoff, with no rollup guard (nothing summarises these) ──
CREATE OR REPLACE FUNCTION "public"."expire_adaptive_decisions"()
RETURNS "jsonb"
LANGUAGE "plpgsql"
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
DECLARE
    cutoff date;
    tz     text;
    n      integer;
BEGIN
    cutoff := expired_signal_cutoff();
    IF cutoff IS NULL THEN
        RETURN jsonb_build_object('deleted', 0, 'skipped_no_window', true);
    END IF;
    SELECT w.timezone INTO tz FROM retention_window w LIMIT 1;
    tz := COALESCE(tz, 'UTC');
    DELETE FROM adaptive_decisions WHERE ("served_at" AT TIME ZONE tz)::date <= cutoff;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN jsonb_build_object('deleted', n);
END;
$$;

REVOKE ALL ON FUNCTION "public"."expire_adaptive_decisions"() FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."expire_adaptive_decisions"() FROM "anon";
REVOKE ALL ON FUNCTION "public"."expire_adaptive_decisions"() FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."expire_adaptive_decisions"() TO "service_role";

-- After the nightly jobs at 03:30-03:50. cron.schedule upserts on the job name.
SELECT cron.schedule('expire-adaptive-decisions', '55 3 * * *',
                     $job$SELECT public.expire_adaptive_decisions();$job$);


-- ─── the admin read: per school day, decisions by direction and reason ──
-- A decision drawing on a heart or camera channel the student has since withdrawn is left out; EEG is always read.
-- A day with fewer than `p_min_students` students is withheld with no figure.
CREATE OR REPLACE FUNCTION "public"."admin_adaptive_reasons"("p_since" date, "p_tz" text, "p_min_students" integer)
RETURNS "jsonb"
LANGUAGE "sql"
STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
    WITH d AS (
        SELECT (a.served_at AT TIME ZONE p_tz)::date AS day, a.user_id, a.bias, a.why, a.label,
               a.increase_withheld
          FROM adaptive_decisions a
          LEFT JOIN signal_consent c ON c.user_id = a.user_id
         WHERE a.served_at >= (p_since::timestamp AT TIME ZONE p_tz)
           AND NOT ('headband_optical' = ANY (a.opinions) AND NOT coalesce(c.headband_optical_enabled, false))
           AND NOT ('camera' = ANY (a.opinions) AND NOT coalesce(c.camera_enabled, false))
    ),
    days AS (
        SELECT day, count(DISTINCT user_id) AS students, count(*) AS decisions,
               count(*) FILTER (WHERE bias < 0) AS eased, count(*) FILTER (WHERE bias > 0) AS raised,
               count(*) FILTER (WHERE bias = 0) AS held, count(*) FILTER (WHERE increase_withheld) AS withheld_increase
          FROM d GROUP BY day
    ),
    whys AS (
        SELECT day, jsonb_object_agg(why, n) AS by_why
          FROM (SELECT day, why, count(*) AS n FROM d GROUP BY day, why) w GROUP BY day
    ),
    labels AS (
        SELECT day, jsonb_object_agg(label, n) AS by_label
          FROM (SELECT day, label, count(*) AS n FROM d GROUP BY day, label) l GROUP BY day
    )
    SELECT coalesce(jsonb_agg(
               CASE WHEN days.students < p_min_students THEN jsonb_build_object('day', days.day, 'withheld', true)
                    ELSE jsonb_build_object('day', days.day, 'withheld', false, 'students', days.students,
                                            'decisions', days.decisions, 'eased', days.eased,
                                            'raised', days.raised, 'held', days.held,
                                            'withheld_increase', days.withheld_increase,
                                            'by_why', whys.by_why, 'by_label', labels.by_label) END
               ORDER BY days.day), '[]'::jsonb)
      FROM days
      JOIN whys ON whys.day = days.day
      JOIN labels ON labels.day = days.day;
$$;

REVOKE ALL ON FUNCTION "public"."admin_adaptive_reasons"(date, text, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."admin_adaptive_reasons"(date, text, integer) FROM "anon";
REVOKE ALL ON FUNCTION "public"."admin_adaptive_reasons"(date, text, integer) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."admin_adaptive_reasons"(date, text, integer) TO "service_role";

NOTIFY pgrst, 'reload schema';
