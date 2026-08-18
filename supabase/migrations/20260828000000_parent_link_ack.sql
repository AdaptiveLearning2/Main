-- A student is told when a parent links to them.
--
-- `POST /api/parent/link-child` needs nothing from the child: a parent who
-- knows their user id creates the link, and from then on
-- `_verify_can_view_student` reads it as entitlement to that child's reports
-- and `signal_consent` reads it as the right to switch a sensor back on after
-- the child switched it off. Nothing anywhere told the student it had happened.
--
-- Notify, not block. An acknowledgement *gate* would put a child between a
-- parent and the reports they are entitled to, and this product's students are
-- children -- some of whom would simply never clear it. The banner is the same
-- shape as the one a parent's re-enable already raises: it says what happened,
-- it is dismissible, and nothing waits on it.
--
-- One nullable column rather than a notifications table. There is no
-- notification system in this product -- no service worker, no VAPID, no
-- scheduled fan-out -- and inventing one for a single per-row flag would be a
-- system to maintain in place of a column. `signal_consent.student_ack_at`,
-- which this deliberately mirrors, is the precedent.
--
-- Per link, not per student: two parents linking a week apart are two things a
-- child should be told about, and one acknowledgement must not swallow the
-- second.

ALTER TABLE "public"."parent_child_links"
  ADD COLUMN IF NOT EXISTS "student_ack_at" timestamp with time zone;

-- Existing links are backfilled as already acknowledged. Without this, every
-- link ever made fires a notice on the next dashboard load -- telling a child
-- that a parent "has just linked" to them about a relationship that has been in
-- place for months. That is a false claim about recency, made at scale, on the
-- one surface whose whole job is to be trusted about what is happening to their
-- data.
--
-- `created_at` rather than `now()` so the stamp records when the child was
-- (implicitly) already aware, not when this migration ran.
UPDATE "public"."parent_child_links"
   SET "student_ack_at" = "created_at"
 WHERE "student_ack_at" IS NULL;

-- The student's own unacknowledged links, cheaply. The read runs on every
-- dashboard load and is almost always empty; without the index it is a scan of
-- the child's links to find nothing.
--
-- Partial on `student_ack_at IS NULL`, so the index holds only the rows the
-- query wants and stays near-empty in steady state -- an acknowledged link
-- leaves it rather than sitting in it for ever.
CREATE INDEX IF NOT EXISTS "pcl_child_unacked_idx"
  ON "public"."parent_child_links" ("child_id")
  WHERE "student_ack_at" IS NULL;

-- The column has to be taken away from the client, or it is decorative.
--
-- `pcl: own` is a FOR ALL policy on `auth.uid() = parent_id` and 20260805110000
-- left `authenticated` holding UPDATE, so without this a parent can PATCH their
-- own link row through PostgREST and stamp `student_ack_at` themselves --
-- silently clearing the notice the child was supposed to see, using the
-- column added to give it to them. RLS narrows *which rows* a command touches,
-- never which columns, and a policy cannot express "not this field": only the
-- grant can, and for UPDATE and INSERT grants are per column. Same shape as
-- `profiles.role` in 20260824010000, and the same class of hole as
-- `sessions.chart_paths` before 20260817000000.
--
-- The rest of the row is untouched, so unlinking through PostgREST still works
-- exactly as before. Nothing in `frontend/src` reads or writes this table at
-- all -- both paths go through the backend's service-role client -- so this
-- costs nothing today and closes the hole whether or not that stays true.
REVOKE UPDATE ("student_ack_at") ON TABLE "public"."parent_child_links" FROM "anon";
REVOKE UPDATE ("student_ack_at") ON TABLE "public"."parent_child_links" FROM "authenticated";
REVOKE INSERT ("student_ack_at") ON TABLE "public"."parent_child_links" FROM "anon";
REVOKE INSERT ("student_ack_at") ON TABLE "public"."parent_child_links" FROM "authenticated";

NOTIFY pgrst, 'reload schema';
