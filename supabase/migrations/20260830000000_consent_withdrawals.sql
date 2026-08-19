-- Withdrawals, recorded as events rather than inferred from current state.
--
-- `20260829000000` derived the parent's notice from `signal_consent`'s
-- `*_revoked_at` columns, and that is not durable: `set_consent` writes
-- `fields[f"{c}_revoked_at"] = None if requested else now`, so **a parent
-- re-enabling the channel nulls the column and erases the notice** — for every
-- linked parent at once, with nothing left to show any of them. A withdraw and
-- restore inside one dashboard-load interval made the whole feature silently
-- no-op, which is the shape of failure this product's reporting rules exist to
-- prevent: an absence that reads as "nothing happened".
--
-- `*_revoked_at` is right for what it does — it answers "is this off, and since
-- when", and nulling it on re-enable is exactly correct for that question. It
-- is simply the wrong source for "what happened", and no amount of care at the
-- read end fixes a column that is meant to be cleared.
--
-- So: one row per withdrawal, never updated, never deleted except by cascade.
-- Same shape as `signal_erasure` and `feature_flag_changes`, which are the two
-- other places here that had to record that something happened rather than what
-- is currently true.

CREATE TABLE IF NOT EXISTS "public"."consent_withdrawals" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    -- The same three the `CONSENT_CHANNELS` tuple holds. Checked here as well,
    -- because this table outlives any one deployment of the backend.
    "channel" "text" NOT NULL,
    "withdrawn_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    -- Who made the change. A student withdrawing and a parent switching a
    -- channel off are both withdrawals and both worth recording, but they are
    -- not the same event to a parent reading the notice.
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

-- RLS on with **no policies at all**, and the client roles revoked outright.
-- Nothing in the frontend reads this; the notice endpoint serves it through the
-- service-role client after resolving the parent-child link, which is where the
-- relationship check belongs. Both halves are needed: RLS never filters
-- TRUNCATE, and a `GRANT` on top of Supabase's default `ALTER DEFAULT
-- PRIVILEGES` narrows nothing.
ALTER TABLE "public"."consent_withdrawals" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."consent_withdrawals" FROM "anon";
REVOKE ALL ON TABLE "public"."consent_withdrawals" FROM "authenticated";
GRANT ALL ON TABLE "public"."consent_withdrawals" TO "service_role";

-- Backfilled from the withdrawals still visible in `signal_consent`. Those are
-- the ones a parent could currently be notified about, so not carrying them
-- across would drop every live notice on deploy. Withdrawals already undone by
-- a re-enable are gone and unrecoverable — that is the bug, and this migration
-- stops it continuing rather than reconstructing what it already cost.
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
