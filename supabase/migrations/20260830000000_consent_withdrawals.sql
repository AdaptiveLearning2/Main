-- Withdrawals as append-only events: signal_consent's *_revoked_at says "off
-- since when" and is nulled on re-enable, so it cannot say what happened.

CREATE TABLE IF NOT EXISTS "public"."consent_withdrawals" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    -- Same three as CONSENT_CHANNELS.
    "channel" "text" NOT NULL,
    "withdrawn_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    -- Student or parent: different events to a parent reading the notice.
    "withdrawn_by" "uuid",
    CONSTRAINT "consent_withdrawals_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "consent_withdrawals_channel_check"
        CHECK ("channel" = ANY (ARRAY['eeg'::"text", 'headband_optical'::"text", 'camera'::"text"]))
);

ALTER TABLE "public"."consent_withdrawals" OWNER TO "postgres";

ALTER TABLE ONLY "public"."consent_withdrawals"
    ADD CONSTRAINT "consent_withdrawals_user_id_fkey"
    FOREIGN KEY ("user_id") REFERENCES "public"."profiles"("id") ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS "consent_withdrawals_user_at_idx"
  ON "public"."consent_withdrawals" ("user_id", "withdrawn_at" DESC);

-- RLS with no policies plus revokes (RLS never filters TRUNCATE). Served only
-- through the backend after the parent-child check.
ALTER TABLE "public"."consent_withdrawals" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."consent_withdrawals" FROM "anon";
REVOKE ALL ON TABLE "public"."consent_withdrawals" FROM "authenticated";
GRANT ALL ON TABLE "public"."consent_withdrawals" TO "service_role";

-- Backfilled from withdrawals still visible in signal_consent.
INSERT INTO "public"."consent_withdrawals" ("user_id", "channel", "withdrawn_at", "withdrawn_by")
SELECT "user_id", "channel", "at", "by" FROM (
    SELECT "user_id", 'eeg'::"text" AS "channel",
           "eeg_revoked_at" AS "at", "eeg_revoked_by" AS "by"
      FROM "public"."signal_consent" WHERE "eeg_revoked_at" IS NOT NULL
    UNION ALL
    SELECT "user_id", 'headband_optical'::"text",
           "headband_optical_revoked_at", "headband_optical_revoked_by"
      FROM "public"."signal_consent" WHERE "headband_optical_revoked_at" IS NOT NULL
    UNION ALL
    SELECT "user_id", 'camera'::"text",
           "camera_revoked_at", "camera_revoked_by"
      FROM "public"."signal_consent" WHERE "camera_revoked_at" IS NOT NULL
) AS "live";

NOTIFY pgrst, 'reload schema';
