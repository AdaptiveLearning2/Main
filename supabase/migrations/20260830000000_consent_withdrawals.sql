-- Withdrawals, recorded as events rather than inferred from current state.
--
-- The parent's notice used to be derived from `signal_consent`'s
-- `*_revoked_at` columns, which isn't durable: a parent re-enabling a channel
-- nulls that column and erases the notice for every linked parent at once. A
-- withdraw and restore inside one dashboard-load interval made the whole
-- feature silently no-op.
--
-- `*_revoked_at` is right for what it does -- it answers "is this off, and
-- since when" -- but it's the wrong source for "what happened", since nulling
-- it on re-enable is exactly correct for the first question and wrong for
-- the second.
--
-- So: one row per withdrawal, never updated, never deleted except by
-- cascade, the same shape as `signal_erasure` and `feature_flag_changes`.

CREATE TABLE IF NOT EXISTS "public"."consent_withdrawals" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    -- The same three the `CONSENT_CHANNELS` tuple holds. Checked here too,
    -- since this table outlives any one deployment of the backend.
    "channel" "text" NOT NULL,
    "withdrawn_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    -- Who made the change. A student withdrawing and a parent switching a
    -- channel off are both withdrawals worth recording, but not the same
    -- event to a parent reading the notice.
    "withdrawn_by" "uuid",
    CONSTRAINT "consent_withdrawals_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "consent_withdrawals_channel_check"
        CHECK ("channel" = ANY (ARRAY['eeg'::"text", 'headband_optical'::"text", 'camera'::"text"]))
);

ALTER TABLE "public"."consent_withdrawals" OWNER TO "postgres";

ALTER TABLE ONLY "public"."consent_withdrawals"
    ADD CONSTRAINT "consent_withdrawals_user_id_fkey"
    FOREIGN KEY ("user_id") REFERENCES "public"."profiles"("id") ON DELETE CASCADE;

-- The notice's only query: this student's withdrawals since a timestamp.
CREATE INDEX IF NOT EXISTS "consent_withdrawals_user_at_idx"
  ON "public"."consent_withdrawals" ("user_id", "withdrawn_at" DESC);

-- RLS on with no policies at all, and the client roles revoked outright.
-- Nothing in the frontend reads this; the notice endpoint serves it through
-- the service-role client after resolving the parent-child link, which is
-- where the relationship check belongs. Both halves are needed: RLS never
-- filters TRUNCATE, and a bare GRANT on top of Supabase's default privileges
-- narrows nothing.
ALTER TABLE "public"."consent_withdrawals" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."consent_withdrawals" FROM "anon";
REVOKE ALL ON TABLE "public"."consent_withdrawals" FROM "authenticated";
GRANT ALL ON TABLE "public"."consent_withdrawals" TO "service_role";

-- Backfilled from the withdrawals still visible in `signal_consent` -- the
-- ones a parent could currently be notified about, so skipping this would
-- drop every live notice on deploy. Withdrawals already undone by a
-- re-enable are gone and unrecoverable; this migration stops that continuing
-- rather than reconstructing what it already cost.
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
