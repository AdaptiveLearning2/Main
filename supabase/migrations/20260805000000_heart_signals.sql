-- Heart-rate signals, from whichever sensor produced them.
--
-- A separate table rather than more columns on face_signals, because heart rate
-- is about to have two producers and only one of them is facial. The headband's
-- optical sensor is the primary source; the camera's rPPG is the fallback for
-- when the headband is not being worn or has lost contact. Putting
-- headband-derived heart rate in a table called face_signals would be wrong on
-- its face, and would tie the heart channel to camera consent -- which is the
-- one thing the consent model exists to keep separate.
--
-- The split lines the schema up with signal_consent: heart_signals is gated by
-- headband_optical_enabled or camera_enabled depending on `source`, and
-- face_signals by camera_enabled alone.

CREATE TABLE IF NOT EXISTS "public"."heart_signals" (
    "id" bigint NOT NULL,
    "session_id" "uuid" NOT NULL,
    "user_id" "uuid" NOT NULL,
    "ts" timestamp with time zone DEFAULT "now"() NOT NULL,

    -- Which sensor produced this sample. Not cosmetic: the two headband paths
    -- are different packet types on different hardware (the 2025 Athena carries
    -- PPG inside its OPTICS packet and emits no PPG packet at all), and rPPG is
    -- a camera looking at a face. They have different failure modes, different
    -- quality characteristics and different baselines, so a reader that cannot
    -- tell them apart cannot interpret a mid-session change in either.
    "source" "text" NOT NULL,

    "heart_rate_bpm" double precision,
    "rmssd_ms" double precision,

    -- Signal quality, 0..1. Kept distinct from any field named "confidence":
    -- the reference implementation returned SQI *as* confidence, and a consumer
    -- reading that as a generic confidence score would be reading the quality
    -- of the optical trace as certainty about the derived value.
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

-- Constrained rather than free text. A typo in a source name would not fail --
-- it would quietly create a fourth sensor that no reader knows how to weight,
-- and the failure would surface as a chart with a gap in it weeks later.
ALTER TABLE ONLY "public"."heart_signals"
    ADD CONSTRAINT "heart_signals_source_check"
    CHECK (("source" = ANY (ARRAY['muse_optics'::"text", 'muse_ppg'::"text", 'rppg'::"text"])));

-- calibrating is a real state, not a missing value: the stress score is
-- baseline-relative and there is no baseline for roughly the first 80 seconds
-- of a session. It has to be distinguishable from "not stressed", or a student
-- reads as calm for the part of the session nobody measured.
ALTER TABLE ONLY "public"."heart_signals"
    ADD CONSTRAINT "heart_signals_stress_category_check"
    CHECK (("stress_category" IS NULL OR "stress_category" = ANY (ARRAY[
        'calibrating'::"text", 'low'::"text", 'moderate'::"text",
        'high'::"text", 'unknown'::"text"])));

-- The same argument as the source constraint above, applied to the numbers.
-- These ranges are already stated in the column comments, which means readers
-- will assume them; a derivation bug then writes a silently wrong value instead
-- of failing, and the wrongness surfaces as an implausible chart weeks later.
--
-- The bpm bounds are a sanity gate, not a physiological model: human maximum is
-- around 220 and a child at full exertion sits below that, so 250 is safely
-- above anything real. It exists because an rPPG pipeline losing lock reports
-- values in the hundreds, and `trusted` cannot be relied on to catch that -- it
-- is set by the same derivation that produced the bad number.
ALTER TABLE ONLY "public"."heart_signals"
    ADD CONSTRAINT "heart_signals_ranges_check" CHECK (
        ("sqi" IS NULL OR ("sqi" >= 0 AND "sqi" <= 1))
        AND ("stress_score" IS NULL OR ("stress_score" >= 0 AND "stress_score" <= 100))
        AND ("heart_rate_bpm" IS NULL OR ("heart_rate_bpm" >= 20 AND "heart_rate_bpm" <= 250))
        AND ("rmssd_ms" IS NULL OR ("rmssd_ms" >= 0 AND "rmssd_ms" <= 1000))
    );

CREATE INDEX "heart_session_idx" ON "public"."heart_signals" USING "btree" ("session_id");
CREATE INDEX "heart_ts_idx" ON "public"."heart_signals" USING "btree" ("ts" DESC);

-- (user_id, ts DESC) rather than user_id alone, matching face_user_ts_idx from
-- 20260721000000: the summary aggregate reads one student over a window, which
-- is exactly this shape. A plain user_id index would be a redundant prefix of
-- it, so it is not created at all.
--
-- Built now, against an empty table, because it cannot be built cheaply later.
-- CREATE INDEX CONCURRENTLY is unavailable inside a migration -- Supabase wraps
-- each one in a transaction -- so adding this once the table holds weeks of
-- samples means an ACCESS EXCLUSIVE lock for the duration of the build, or a
-- manual out-of-band CONCURRENTLY run before the migration no-ops.
CREATE INDEX "heart_user_ts_idx" ON "public"."heart_signals" USING "btree" ("user_id", "ts" DESC);

-- The failover view: "what did this session look like, per source, in order".
-- Session review renders exactly that, and marking where the source changed is
-- the difference between one continuous trace and two sensors' calibrations
-- spliced together.
CREATE INDEX "heart_session_source_ts_idx"
    ON "public"."heart_signals" USING "btree" ("session_id", "source", "ts");

ALTER TABLE "public"."heart_signals" ENABLE ROW LEVEL SECURITY;

-- Mirrors face_signals: the student, and a teacher of a class they are enrolled
-- in. Parent reads go through the backend's service-role client like every
-- other reporting surface, so there is no parent policy here either.
-- FOR SELECT explicitly. A policy with no FOR clause covers every command, and
-- with no WITH CHECK Postgres reuses the USING expression as the insert/update
-- check -- so the bare form grants a student a write path to their own
-- biometric rows the moment anyone widens the table grant below. Inert today,
-- since authenticated holds SELECT only and no sequence grant, but the grant
-- and the policy should say the same thing rather than relying on one to
-- contain the other. face_signals carries the bare form; its GRANT ALL is
-- precisely what this table is deliberately not copying.
CREATE POLICY "heart: own" ON "public"."heart_signals"
    FOR SELECT USING (("auth"."uid"() = "user_id"));

CREATE POLICY "heart: teacher read" ON "public"."heart_signals"
    FOR SELECT USING ((EXISTS ( SELECT 1
       FROM ("public"."class_memberships" "cm"
         JOIN "public"."classes" "c" ON (("c"."id" = "cm"."class_id")))
      WHERE (("cm"."student_id" = "heart_signals"."user_id")
        AND ("c"."teacher_id" = "auth"."uid"())))));

-- Narrower than the GRANT ALL the older signal tables carry. Nothing writes
-- these through PostgREST -- ingestion is a backend endpoint using the
-- service-role client -- so write privileges for anon and authenticated would
-- be granting something no caller needs. No grant to anon at all: both policies
-- are auth.uid()-scoped and auth.uid() is null for anon, so it would return
-- nothing while reading as a deliberate anonymous path.
GRANT SELECT ON TABLE "public"."heart_signals" TO "authenticated";
GRANT ALL ON TABLE "public"."heart_signals" TO "service_role";
GRANT USAGE, SELECT ON SEQUENCE "public"."heart_signals_id_seq" TO "service_role";


-- ── face_signals keeps expression, and gains the two fields it was missing ──
--
-- Heart rate, HRV, SQI and the stress score deliberately do NOT go here; they
-- are in heart_signals above, whichever sensor produced them.

ALTER TABLE "public"."face_signals"
    ADD COLUMN IF NOT EXISTS "emotion_confidence" double precision;

-- Whether the emotion label passed its confidence gate. The reference
-- implementation dropped the label entirely when it did not, which loses the
-- distinction between "the model was unsure" and "no frame was analysed" --
-- and those are different sentences to put under a pie chart.
--
-- emotion_trusted rather than a bare `trusted`: this table already carries
-- identity_confidence and now emotion_confidence, so an unqualified name leaves
-- a reader a 50/50 guess about which one it gates, and leaves no room for an
-- identity gate later. heart_signals keeps the bare name -- one signal, no
-- ambiguity -- which does mean the two tables spell the same idea differently.
-- That is the lesser problem: a joined query showing two columns called
-- `trusted` meaning different things is worse than two names.
ALTER TABLE "public"."face_signals"
    ADD COLUMN IF NOT EXISTS "emotion_trusted" boolean;

NOTIFY pgrst, 'reload schema';
