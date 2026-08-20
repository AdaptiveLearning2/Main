-- Per-student consent for the three signal channels. Recording what a child's
-- body is doing is not a display preference, so this replaces the old
-- viewer-side facial toggle, which only hid data in the browser and never
-- stopped anything from being recorded.
--
-- Channels are named for the SENSOR, not the signal derived from it:
--
--   eeg               the headband's electrodes
--   headband_optical  the headband's optical sensor -- heart rate today, and
--                     it may carry other signals later, so it's named for the
--                     sensor rather than one reading off it
--   camera            the webcam -- expression AND the rPPG heart-rate
--                     fallback. One device, one decision: declining the
--                     camera declines both, so a dropped headband can never
--                     silently turn the webcam on instead
--
-- Everything defaults to FALSE, and an absent row means the same as a row of
-- falses. A student who has never been seen by this table records nothing.

CREATE TABLE IF NOT EXISTS "public"."signal_consent" (
    "user_id" "uuid" NOT NULL,

    "eeg_enabled" boolean DEFAULT false NOT NULL,
    "headband_optical_enabled" boolean DEFAULT false NOT NULL,
    "camera_enabled" boolean DEFAULT false NOT NULL,

    -- When each channel was turned off, and by whom, cleared when it goes back
    -- on. A report can then say "recording stopped on 14 Aug" instead of
    -- showing a channel that just goes flat with no explanation.
    --
    -- Stored per channel rather than read off the row's single updated_by,
    -- because channels are revoked independently: if a student turns the
    -- camera off and a parent later turns EEG on, updated_by would point at
    -- the parent and misattribute the camera revocation to them.
    "eeg_revoked_at" timestamp with time zone,
    "eeg_revoked_by" "uuid",
    "headband_optical_revoked_at" timestamp with time zone,
    "headband_optical_revoked_by" "uuid",
    "camera_revoked_at" timestamp with time zone,
    "camera_revoked_by" "uuid",

    -- Who last wrote the row. Used to tell a teacher whether the student or a
    -- parent made the change, and whether the student needs to be notified.
    -- Surfaced to a teacher only as a role, never as an identity.
    "updated_by" "uuid",
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,

    -- When a parent last turned a channel back ON, and when the student
    -- dismissed the notice about it. Only the re-enable is tracked, not every
    -- parent write: turning a channel off needs no notice, since the student
    -- only sees a new restriction, not a resumed one. Tracking the enable
    -- separately from updated_at also stops an unrelated later write from
    -- re-raising a notice the student already dismissed.
    --
    -- Banner condition: parent_enabled_at IS NOT NULL AND (student_ack_at IS
    -- NULL OR student_ack_at < parent_enabled_at).
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

-- A revocation date on a channel that's switched on is a contradiction. Make
-- it unrepresentable rather than trust every reader to handle it consistently.
ALTER TABLE ONLY "public"."signal_consent"
    ADD CONSTRAINT "signal_consent_revocation_matches_flag" CHECK (
        ("eeg_enabled" IS FALSE OR ("eeg_revoked_at" IS NULL AND "eeg_revoked_by" IS NULL))
        AND ("headband_optical_enabled" IS FALSE
             OR ("headband_optical_revoked_at" IS NULL AND "headband_optical_revoked_by" IS NULL))
        AND ("camera_enabled" IS FALSE
             OR ("camera_revoked_at" IS NULL AND "camera_revoked_by" IS NULL))
    );

ALTER TABLE "public"."signal_consent" ENABLE ROW LEVEL SECURITY;

-- Reads: the student, a teacher of a class they're in, and a linked parent --
-- the same relationships the signal tables use.
CREATE POLICY "consent: own read" ON "public"."signal_consent"
    FOR SELECT USING (("auth"."uid"() = "user_id"));

CREATE POLICY "consent: parent read" ON "public"."signal_consent"
    FOR SELECT USING ((EXISTS ( SELECT 1
       FROM "public"."parent_child_links" "l"
      WHERE (("l"."child_id" = "signal_consent"."user_id")
        AND ("l"."parent_id" = "auth"."uid"())))));

-- Written directly rather than through is_teacher_of_class, which answers "am
-- I the teacher of this one class" -- here the question is "am I the teacher
-- of any class this student is in", which needs its own query.
CREATE POLICY "consent: teacher read" ON "public"."signal_consent"
    FOR SELECT USING ((EXISTS ( SELECT 1
       FROM ("public"."class_memberships" "cm"
         JOIN "public"."classes" "c" ON (("c"."id" = "cm"."class_id")))
      WHERE (("cm"."student_id" = "signal_consent"."user_id")
        AND ("c"."teacher_id" = "auth"."uid"())))));

-- No insert, update or delete policy for anyone. With RLS on, no policy for a
-- command means that command is denied -- so the frontend's anon key can never
-- write this table through PostgREST. All writes go through the backend's
-- service-role client, where the real rules live:
--
--   * a student may only ever move a flag true -> false
--   * only a linked parent may move one false -> true
--   * a teacher may read but not write
--
-- That "off-direction only" rule is why this isn't a plain own-row update
-- policy: RLS WITH CHECK can't see the previous row's value, so it can't
-- express "only in this direction" -- granting the student UPDATE would let
-- them re-enable a channel a parent controls.
--
-- No grant to anon either. Every policy above is scoped to auth.uid(), which
-- is null for anon, so a grant would return nothing anyway -- but leaving it
-- out says that plainly instead of relying on a reader to work it out.
GRANT SELECT ON TABLE "public"."signal_consent" TO "authenticated";
GRANT ALL ON TABLE "public"."signal_consent" TO "service_role";

NOTIFY pgrst, 'reload schema';
