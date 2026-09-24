-- Per-student consent for the three signal channels, named for the SENSOR:
-- eeg, headband_optical, camera (expression AND the rPPG heart-rate fallback,
-- so declining the camera declines both).
-- Everything defaults to FALSE; an absent row means a row of falses.

CREATE TABLE IF NOT EXISTS "public"."signal_consent" (
    "user_id" "uuid" NOT NULL,

    "eeg_enabled" boolean DEFAULT false NOT NULL,
    "headband_optical_enabled" boolean DEFAULT false NOT NULL,
    "camera_enabled" boolean DEFAULT false NOT NULL,

    -- Per channel, cleared on re-enable. Not derived from updated_by: channels
    -- are revoked independently, so a later write would misattribute it.
    "eeg_revoked_at" timestamp with time zone,
    "eeg_revoked_by" "uuid",
    "headband_optical_revoked_at" timestamp with time zone,
    "headband_optical_revoked_by" "uuid",
    "camera_revoked_at" timestamp with time zone,
    "camera_revoked_by" "uuid",

    -- Surfaced to a teacher only as a role, never as an identity.
    "updated_by" "uuid",
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,

    -- Only a parent re-enable raises a notice. Banner shows while
    -- parent_enabled_at IS NOT NULL AND (student_ack_at IS NULL OR earlier).
    "parent_enabled_at" timestamp with time zone,
    "student_ack_at" timestamp with time zone
);

ALTER TABLE "public"."signal_consent" OWNER TO "postgres";

ALTER TABLE ONLY "public"."signal_consent"
    ADD CONSTRAINT "signal_consent_pkey" PRIMARY KEY ("user_id");

ALTER TABLE ONLY "public"."signal_consent"
    ADD CONSTRAINT "signal_consent_user_id_fkey" FOREIGN KEY ("user_id")
    REFERENCES "public"."profiles"("id") ON DELETE CASCADE;

ALTER TABLE ONLY "public"."signal_consent"
    ADD CONSTRAINT "signal_consent_updated_by_fkey" FOREIGN KEY ("updated_by")
    REFERENCES "public"."profiles"("id") ON DELETE SET NULL;

ALTER TABLE ONLY "public"."signal_consent"
    ADD CONSTRAINT "signal_consent_eeg_revoked_by_fkey" FOREIGN KEY ("eeg_revoked_by")
    REFERENCES "public"."profiles"("id") ON DELETE SET NULL;

ALTER TABLE ONLY "public"."signal_consent"
    ADD CONSTRAINT "signal_consent_headband_optical_revoked_by_fkey" FOREIGN KEY ("headband_optical_revoked_by")
    REFERENCES "public"."profiles"("id") ON DELETE SET NULL;

ALTER TABLE ONLY "public"."signal_consent"
    ADD CONSTRAINT "signal_consent_camera_revoked_by_fkey" FOREIGN KEY ("camera_revoked_by")
    REFERENCES "public"."profiles"("id") ON DELETE SET NULL;

-- A revocation on a channel that is on is unrepresentable.
ALTER TABLE ONLY "public"."signal_consent"
    ADD CONSTRAINT "signal_consent_revocation_matches_flag" CHECK (
        ("eeg_enabled" IS FALSE OR ("eeg_revoked_at" IS NULL AND "eeg_revoked_by" IS NULL))
        AND ("headband_optical_enabled" IS FALSE
             OR ("headband_optical_revoked_at" IS NULL AND "headband_optical_revoked_by" IS NULL))
        AND ("camera_enabled" IS FALSE
             OR ("camera_revoked_at" IS NULL AND "camera_revoked_by" IS NULL))
    );

ALTER TABLE "public"."signal_consent" ENABLE ROW LEVEL SECURITY;

CREATE POLICY "consent: own read" ON "public"."signal_consent"
    FOR SELECT USING (("auth"."uid"() = "user_id"));

CREATE POLICY "consent: parent read" ON "public"."signal_consent"
    FOR SELECT USING ((EXISTS ( SELECT 1
       FROM "public"."parent_child_links" "l"
      WHERE (("l"."child_id" = "signal_consent"."user_id")
        AND ("l"."parent_id" = "auth"."uid"())))));

-- Not is_teacher_of_class: that takes one class, this asks about any class.
CREATE POLICY "consent: teacher read" ON "public"."signal_consent"
    FOR SELECT USING ((EXISTS ( SELECT 1
       FROM ("public"."class_memberships" "cm"
         JOIN "public"."classes" "c" ON (("c"."id" = "cm"."class_id")))
      WHERE (("cm"."student_id" = "signal_consent"."user_id")
        AND ("c"."teacher_id" = "auth"."uid"())))));

-- No write policy: writes go through the backend, since WITH CHECK cannot see
-- the previous row to enforce "student may only turn a flag off".
GRANT SELECT ON TABLE "public"."signal_consent" TO "authenticated";
GRANT ALL ON TABLE "public"."signal_consent" TO "service_role";

NOTIFY pgrst, 'reload schema';
