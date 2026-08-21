-- Heart-rate signals, from whichever sensor produced them. Separate from
-- face_signals because heart rate has two producers and only one is facial:
-- the headband's optical sensor is primary, the camera's rPPG is the fallback
-- when the headband isn't worn or has lost contact. Keeping it out of
-- face_signals also keeps headband-derived heart rate from being tied to
-- camera consent, which the consent model needs to keep separate.
--
-- The split matches signal_consent: heart_signals is gated by
-- headband_optical_enabled or camera_enabled depending on `source`, and
-- face_signals by camera_enabled alone.

CREATE TABLE IF NOT EXISTS "public"."heart_signals" (
    "id" bigint NOT NULL,
    "session_id" "uuid" NOT NULL,
    "user_id" "uuid" NOT NULL,
    "ts" timestamp with time zone DEFAULT "now"() NOT NULL,

    -- Which sensor produced this sample. The headband's optical path and the
    -- camera's rPPG have different failure modes, quality, and baselines, so a
    -- reader needs to know which one produced a given row to interpret it.
    "source" "text" NOT NULL,

    "heart_rate_bpm" double precision,
    "rmssd_ms" double precision,

    -- Signal quality, 0..1. Kept separate from any "confidence" field, since
    -- it measures the optical trace's quality, not certainty about the
    -- derived heart rate.
    "sqi" double precision,

    -- 0..100, neutral at 50, derived from heart rate and RMSSD against this
    -- session's own baseline. Named for what it measures rather than for the
    -- sensor, because the same rule runs over either source.
    "stress_score" double precision,
    "stress_category" "text",

    -- Whether the sample passed its quality gate. Aggregates must filter on
    -- this: an untrusted sample carries a heart rate, it is just not one worth
    -- averaging.
    "trusted" boolean,

    "raw" "jsonb"
);

ALTER TABLE "public"."heart_signals" OWNER TO "postgres";

CREATE SEQUENCE IF NOT EXISTS "public"."heart_signals_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE "public"."heart_signals_id_seq" OWNER TO "postgres";
ALTER SEQUENCE "public"."heart_signals_id_seq" OWNED BY "public"."heart_signals"."id";

ALTER TABLE ONLY "public"."heart_signals"
    ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."heart_signals_id_seq"'::"regclass");

ALTER TABLE ONLY "public"."heart_signals"
    ADD CONSTRAINT "heart_signals_pkey" PRIMARY KEY ("id");

ALTER TABLE ONLY "public"."heart_signals"
    ADD CONSTRAINT "heart_signals_session_id_fkey" FOREIGN KEY ("session_id")
    REFERENCES "public"."sessions"("id") ON DELETE CASCADE;

ALTER TABLE ONLY "public"."heart_signals"
    ADD CONSTRAINT "heart_signals_user_id_fkey" FOREIGN KEY ("user_id")
    REFERENCES "public"."profiles"("id") ON DELETE CASCADE;

-- Constrained rather than free text, so a typo can't quietly create a fourth
-- "sensor" that nothing knows how to weight.
ALTER TABLE ONLY "public"."heart_signals"
    ADD CONSTRAINT "heart_signals_source_check"
    CHECK (("source" = ANY (ARRAY['muse_optics'::"text", 'muse_ppg'::"text", 'rppg'::"text"])));

-- calibrating is a real state, not a missing value: the stress score needs a
-- baseline, and there is none for roughly the first 80 seconds of a session.
-- It must read differently from "not stressed".
ALTER TABLE ONLY "public"."heart_signals"
    ADD CONSTRAINT "heart_signals_stress_category_check"
    CHECK (("stress_category" IS NULL OR "stress_category" = ANY (ARRAY[
        'calibrating'::"text", 'low'::"text", 'moderate'::"text",
        'high'::"text", 'unknown'::"text"])));

-- One range check per column rather than one combined check, so a violation
-- names the specific column instead of just "some number was out of range".
ALTER TABLE ONLY "public"."heart_signals"
    ADD CONSTRAINT "heart_signals_sqi_range"
    CHECK (("sqi" IS NULL OR ("sqi" >= 0 AND "sqi" <= 1)));

ALTER TABLE ONLY "public"."heart_signals"
    ADD CONSTRAINT "heart_signals_stress_score_range"
    CHECK (("stress_score" IS NULL OR ("stress_score" >= 0 AND "stress_score" <= 100)));

-- A sanity gate, not a physiological model: human max is around 220, so 250
-- clears anything real. Needed because a losing-lock rPPG pipeline can report
-- values in the hundreds, and `trusted` can't catch that -- it's set by the
-- same derivation that produced the bad number.
ALTER TABLE ONLY "public"."heart_signals"
    ADD CONSTRAINT "heart_signals_heart_rate_bpm_range"
    CHECK (("heart_rate_bpm" IS NULL OR ("heart_rate_bpm" >= 20 AND "heart_rate_bpm" <= 250)));

ALTER TABLE ONLY "public"."heart_signals"
    ADD CONSTRAINT "heart_signals_rmssd_ms_range"
    CHECK (("rmssd_ms" IS NULL OR ("rmssd_ms" >= 0 AND "rmssd_ms" <= 1000)));

CREATE INDEX "heart_session_idx" ON "public"."heart_signals" USING "btree" ("session_id");
CREATE INDEX "heart_ts_idx" ON "public"."heart_signals" USING "btree" ("ts" DESC);

-- (user_id, ts DESC) matches how the summary aggregate reads one student over
-- a window; a plain user_id index would be a redundant prefix of it.
--
-- Built now, against an empty table, because CREATE INDEX CONCURRENTLY isn't
-- available inside a migration -- building this later against a full table
-- would need an ACCESS EXCLUSIVE lock for the whole build.
CREATE INDEX "heart_user_ts_idx" ON "public"."heart_signals" USING "btree" ("user_id", "ts" DESC);

-- Supports "what did this session look like, per source, in order" -- session
-- review needs to know where the source changed, or two sensors' readings
-- look like one continuous trace.
CREATE INDEX "heart_session_source_ts_idx"
    ON "public"."heart_signals" USING "btree" ("session_id", "source", "ts");

ALTER TABLE "public"."heart_signals" ENABLE ROW LEVEL SECURITY;

-- Mirrors face_signals: the student, and a teacher of a class they're in.
-- Parent reads go through the backend's service-role client, like every other
-- reporting surface, so there's no parent policy here.
--
-- FOR SELECT is explicit here. A policy with no FOR clause covers every
-- command, and with no WITH CHECK, Postgres reuses USING as the write check --
-- so an unscoped policy would give a student a write path to their own
-- biometric rows the moment the table grant below is ever widened.
CREATE POLICY "heart: own" ON "public"."heart_signals"
    FOR SELECT USING (("auth"."uid"() = "user_id"));

CREATE POLICY "heart: teacher read" ON "public"."heart_signals"
    FOR SELECT USING ((EXISTS ( SELECT 1
       FROM ("public"."class_memberships" "cm"
         JOIN "public"."classes" "c" ON (("c"."id" = "cm"."class_id")))
      WHERE (("cm"."student_id" = "heart_signals"."user_id")
        AND ("c"."teacher_id" = "auth"."uid"())))));

-- REVOKE first, because a narrow GRANT alone doesn't narrow anything: Supabase
-- grants every table privilege to anon and authenticated by name on a new
-- table, so it arrives wide open before this file grants anything.
--
-- RLS denies INSERT/UPDATE/DELETE by having no policy for them, but RLS does
-- not filter TRUNCATE. That needs a direct Postgres connection rather than
-- PostgREST, so the frontend's anon key can't reach it -- but that's a weaker
-- guarantee than "revoked".
REVOKE ALL ON TABLE "public"."heart_signals" FROM "anon";
REVOKE ALL ON TABLE "public"."heart_signals" FROM "authenticated";
REVOKE ALL ON SEQUENCE "public"."heart_signals_id_seq" FROM "anon";
REVOKE ALL ON SEQUENCE "public"."heart_signals_id_seq" FROM "authenticated";

-- Nothing writes this table through PostgREST -- ingestion goes through a
-- backend endpoint on the service-role client -- so authenticated needs
-- SELECT only, and anon needs nothing.
GRANT SELECT ON TABLE "public"."heart_signals" TO "authenticated";
GRANT ALL ON TABLE "public"."heart_signals" TO "service_role";
GRANT USAGE, SELECT ON SEQUENCE "public"."heart_signals_id_seq" TO "service_role";


-- face_signals keeps expression and gains two new fields. Heart rate, HRV,
-- SQI and the stress score live in heart_signals above instead, whichever
-- sensor produced them.

ALTER TABLE "public"."face_signals"
    ADD COLUMN IF NOT EXISTS "emotion_confidence" double precision;

-- Whether the emotion label passed its confidence gate. Kept as its own
-- boolean rather than dropping the label outright, so "the model was unsure"
-- stays distinguishable from "no frame was analysed".
--
-- Named emotion_trusted rather than a bare `trusted` because this table also
-- carries identity_confidence, so an unqualified name would be ambiguous.
ALTER TABLE "public"."face_signals"
    ADD COLUMN IF NOT EXISTS "emotion_trusted" boolean;

NOTIFY pgrst, 'reload schema';
