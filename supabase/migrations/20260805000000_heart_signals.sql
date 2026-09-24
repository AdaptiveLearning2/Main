-- Heart-rate signals from either sensor: headband optics (primary) or camera
-- rPPG (fallback). Gated by headband_optical_enabled or camera_enabled per
-- `source`, so it stays out of face_signals.

CREATE TABLE IF NOT EXISTS "public"."heart_signals" (
    "id" bigint NOT NULL,
    "session_id" "uuid" NOT NULL,
    "user_id" "uuid" NOT NULL,
    "ts" timestamp with time zone DEFAULT "now"() NOT NULL,

    "source" "text" NOT NULL,

    "heart_rate_bpm" double precision,
    "rmssd_ms" double precision,

    -- Optical trace quality, 0..1; not certainty about the derived heart rate.
    "sqi" double precision,

    -- 0..100, neutral at 50, against this session's own baseline.
    "stress_score" double precision,
    "stress_category" "text",

    -- Quality gate. Aggregates must filter on it.
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

ALTER TABLE ONLY "public"."heart_signals"
    ADD CONSTRAINT "heart_signals_source_check"
    CHECK (("source" = ANY (ARRAY['muse_optics'::"text", 'muse_ppg'::"text", 'rppg'::"text"])));

-- calibrating is a real state (no baseline for ~80 s), distinct from low.
ALTER TABLE ONLY "public"."heart_signals"
    ADD CONSTRAINT "heart_signals_stress_category_check"
    CHECK (("stress_category" IS NULL OR "stress_category" = ANY (ARRAY[
        'calibrating'::"text", 'low'::"text", 'moderate'::"text",
        'high'::"text", 'unknown'::"text"])));

-- One range check per column, so a violation names the column.
ALTER TABLE ONLY "public"."heart_signals"
    ADD CONSTRAINT "heart_signals_sqi_range"
    CHECK (("sqi" IS NULL OR ("sqi" >= 0 AND "sqi" <= 1)));

ALTER TABLE ONLY "public"."heart_signals"
    ADD CONSTRAINT "heart_signals_stress_score_range"
    CHECK (("stress_score" IS NULL OR ("stress_score" >= 0 AND "stress_score" <= 100)));

-- Sanity gate: a losing-lock rPPG can report hundreds, and `trusted` comes
-- from the same derivation, so it cannot catch that.
ALTER TABLE ONLY "public"."heart_signals"
    ADD CONSTRAINT "heart_signals_heart_rate_bpm_range"
    CHECK (("heart_rate_bpm" IS NULL OR ("heart_rate_bpm" >= 20 AND "heart_rate_bpm" <= 250)));

ALTER TABLE ONLY "public"."heart_signals"
    ADD CONSTRAINT "heart_signals_rmssd_ms_range"
    CHECK (("rmssd_ms" IS NULL OR ("rmssd_ms" >= 0 AND "rmssd_ms" <= 1000)));

CREATE INDEX "heart_session_idx" ON "public"."heart_signals" USING "btree" ("session_id");
CREATE INDEX "heart_ts_idx" ON "public"."heart_signals" USING "btree" ("ts" DESC);

-- Built against an empty table: CONCURRENTLY is unavailable in a migration.
CREATE INDEX "heart_user_ts_idx" ON "public"."heart_signals" USING "btree" ("user_id", "ts" DESC);

-- Session review needs to see where the source changed.
CREATE INDEX "heart_session_source_ts_idx"
    ON "public"."heart_signals" USING "btree" ("session_id", "source", "ts");

ALTER TABLE "public"."heart_signals" ENABLE ROW LEVEL SECURITY;

-- FOR SELECT explicitly: with no FOR clause, USING doubles as a write check.
-- Parents read through the backend, so no parent policy.
CREATE POLICY "heart: own" ON "public"."heart_signals"
    FOR SELECT USING (("auth"."uid"() = "user_id"));

CREATE POLICY "heart: teacher read" ON "public"."heart_signals"
    FOR SELECT USING ((EXISTS ( SELECT 1
       FROM ("public"."class_memberships" "cm"
         JOIN "public"."classes" "c" ON (("c"."id" = "cm"."class_id")))
      WHERE (("cm"."student_id" = "heart_signals"."user_id")
        AND ("c"."teacher_id" = "auth"."uid"())))));

-- REVOKE first: a new table arrives fully granted, and RLS never filters TRUNCATE.
REVOKE ALL ON TABLE "public"."heart_signals" FROM "anon";
REVOKE ALL ON TABLE "public"."heart_signals" FROM "authenticated";
REVOKE ALL ON SEQUENCE "public"."heart_signals_id_seq" FROM "anon";
REVOKE ALL ON SEQUENCE "public"."heart_signals_id_seq" FROM "authenticated";

-- Ingestion is backend-only, so authenticated gets SELECT and anon nothing.
GRANT SELECT ON TABLE "public"."heart_signals" TO "authenticated";
GRANT ALL ON TABLE "public"."heart_signals" TO "service_role";
GRANT USAGE, SELECT ON SEQUENCE "public"."heart_signals_id_seq" TO "service_role";


ALTER TABLE "public"."face_signals"
    ADD COLUMN IF NOT EXISTS "emotion_confidence" double precision;

-- Emotion label passed its confidence gate; keeps "unsure" distinct from
-- "no frame analysed".
ALTER TABLE "public"."face_signals"
    ADD COLUMN IF NOT EXISTS "emotion_trusted" boolean;

NOTIFY pgrst, 'reload schema';
