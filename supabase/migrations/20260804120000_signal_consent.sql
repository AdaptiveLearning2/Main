-- Per-student consent over the three signal channels.
--
-- This replaces the viewer-side facial toggle (frontend/src/lib/facePref.js),
-- which was a localStorage read filter: it changed what a dashboard rendered,
-- did not stop anything being recorded, and did not travel with the student.
-- What is recorded about a child's body is not a per-viewer display preference,
-- and the switch that controls it has to outlive a browser profile.
--
-- Three channels, named for the SENSOR rather than the signal derived from it:
--
--   eeg               the headband's electrodes
--   headband_optical  the headband's optical sensor -- heart rate today, and on
--                     the Athena the same OPTICS packet also carries fNIRS, so a
--                     column called headband_ppg_enabled would be misnamed the
--                     moment a second signal came off the same emitter
--   camera            the webcam -- expression AND the rPPG heart-rate fallback.
--                     One device, one decision: a student who declines a camera
--                     has declined it for both, and a heart-rate switch that
--                     silently turned the webcam on when the headband dropped
--                     out would not be consent in any useful sense
--
-- Everything defaults to FALSE. A student records nothing until a linked parent
-- enables it, and an absent row means the same thing as a row of falses -- so
-- there is no backfill, and a student who has never been seen by this table is
-- off rather than silently on. Read the absence as denial everywhere.

CREATE TABLE IF NOT EXISTS "public"."signal_consent" (
    "user_id" "uuid" NOT NULL,

    "eeg_enabled" boolean DEFAULT false NOT NULL,
    "headband_optical_enabled" boolean DEFAULT false NOT NULL,
    "camera_enabled" boolean DEFAULT false NOT NULL,

    -- When the student turned each one off. Cleared when a parent turns it back
    -- on. These exist so a report can say "recording stopped on 14 Aug" instead
    -- of showing a channel that silently goes flat -- an absence with a date is
    -- a fact, an absence without one is indistinguishable from a broken query.
    "eeg_revoked_at" timestamp with time zone,
    "headband_optical_revoked_at" timestamp with time zone,
    "camera_revoked_at" timestamp with time zone,

    -- Who last wrote the row. Drives two things: the reason a teacher is shown
    -- ("student opted out" vs "parent opted out"), and whether the student gets
    -- told. Never surfaced as an identity to a teacher, only as a role.
    "updated_by" "uuid",
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,

    -- When the student dismissed the last parent-made change. A parent
    -- re-enabling a channel has to be visible to the student -- discovering it
    -- by noticing data reappear is not acceptable -- and this is the cheapest
    -- mechanism that does not mean building a notifications table for one
    -- message. Banner condition is: updated_by <> user_id AND (student_ack_at
    -- IS NULL OR student_ack_at < updated_at).
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

-- A revoked_at without the flag being off is a contradiction, and the pair is
-- read as a unit by every consumer. Cheaper to make it unrepresentable than to
-- teach five call sites which one wins.
ALTER TABLE ONLY "public"."signal_consent"
    ADD CONSTRAINT "signal_consent_revoked_at_matches_flag" CHECK (
        ("eeg_enabled" IS FALSE OR "eeg_revoked_at" IS NULL)
        AND ("headband_optical_enabled" IS FALSE OR "headband_optical_revoked_at" IS NULL)
        AND ("camera_enabled" IS FALSE OR "camera_revoked_at" IS NULL)
    );

ALTER TABLE "public"."signal_consent" ENABLE ROW LEVEL SECURITY;

-- Reads: the student, a teacher of a class they are enrolled in, and a linked
-- parent. Same relationships the signal tables already use -- access here is a
-- relationship, not a role claim.
CREATE POLICY "consent: own read" ON "public"."signal_consent"
    FOR SELECT USING (("auth"."uid"() = "user_id"));

CREATE POLICY "consent: parent read" ON "public"."signal_consent"
    FOR SELECT USING ((EXISTS ( SELECT 1
       FROM "public"."parent_child_links" "l"
      WHERE (("l"."child_id" = "signal_consent"."user_id")
        AND ("l"."parent_id" = "auth"."uid"())))));

CREATE POLICY "consent: teacher read" ON "public"."signal_consent"
    FOR SELECT USING ((EXISTS ( SELECT 1
       FROM ("public"."class_memberships" "cm"
         JOIN "public"."classes" "c" ON (("c"."id" = "cm"."class_id")))
      WHERE (("cm"."student_id" = "signal_consent"."user_id")
        AND ("c"."teacher_id" = "auth"."uid"())))));

-- Deliberately NO insert, update or delete policy, for anyone.
--
-- With RLS enabled and no permissive policy for a command, that command is
-- denied -- so the anon key that ships in the frontend bundle cannot write this
-- table through PostgREST at all, no matter whose JWT it carries. Every write
-- goes through the backend's service-role client, which bypasses RLS, and the
-- rules live in main.py where they can be expressed:
--
--   * a student may only ever move a flag true -> false
--   * only a linked parent may move one false -> true
--   * a teacher may not write at all, despite being able to read
--
-- The first of those is the reason this is not simply an own-row update policy.
-- RLS WITH CHECK cannot see the previous row, so "only in the off direction" is
-- not expressible as a policy; granting the student UPDATE would let them call
-- PostgREST directly and re-enable a channel a parent had control of, which is
-- the whole point of the parent-restore rule.
GRANT SELECT ON TABLE "public"."signal_consent" TO "anon";
GRANT SELECT ON TABLE "public"."signal_consent" TO "authenticated";
GRANT ALL ON TABLE "public"."signal_consent" TO "service_role";

NOTIFY pgrst, 'reload schema';
