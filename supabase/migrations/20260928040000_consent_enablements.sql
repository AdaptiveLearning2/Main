-- Parent turn-ons as append-only events, so every other linked parent is told:
-- a second parent account can otherwise re-enable a channel with nobody but the child knowing.

CREATE TABLE IF NOT EXISTS "public"."consent_enablements" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    -- Same three as CONSENT_CHANNELS.
    "channel" "text" NOT NULL,
    "enabled_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    -- No foreign key: a deleted account's turn-on must still reach the other parents.
    "enabled_by" "uuid" NOT NULL,
    CONSTRAINT "consent_enablements_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "consent_enablements_channel_check"
        CHECK ("channel" = ANY (ARRAY['eeg'::"text", 'headband_optical'::"text", 'camera'::"text"]))
);

ALTER TABLE "public"."consent_enablements" OWNER TO "postgres";

ALTER TABLE ONLY "public"."consent_enablements"
    ADD CONSTRAINT "consent_enablements_user_id_fkey"
    FOREIGN KEY ("user_id") REFERENCES "public"."profiles"("id") ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS "consent_enablements_user_at_idx"
  ON "public"."consent_enablements" ("user_id", "enabled_at" DESC);

-- RLS with no policies plus revokes (RLS never filters TRUNCATE). Backend only.
ALTER TABLE "public"."consent_enablements" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."consent_enablements" FROM "anon";
REVOKE ALL ON TABLE "public"."consent_enablements" FROM "authenticated";
GRANT ALL ON TABLE "public"."consent_enablements" TO "service_role";

NOTIFY pgrst, 'reload schema';
