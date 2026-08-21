-- A student is told when a parent links to them.
--
-- `POST /api/parent/link-child` needs nothing from the child: a parent who
-- knows their user id creates the link, and from then on it's read as
-- entitlement to that child's reports and consent settings. Nothing told the
-- student it had happened.
--
-- Notify, not block. An acknowledgement gate would put a child between a
-- parent and the reports they're entitled to, and some children would simply
-- never clear it. The banner mirrors the one a parent's re-enable already
-- raises: it says what happened, it's dismissible, and nothing waits on it.
--
-- One nullable column rather than a notifications table -- there's no
-- notification system in this product, and inventing one for a single
-- per-row flag would be a system to maintain in place of a column.
--
-- Per link, not per student: two parents linking a week apart are two things
-- a child should be told about, and one acknowledgement must not swallow the
-- second.

ALTER TABLE "public"."parent_child_links"
  ADD COLUMN IF NOT EXISTS "student_ack_at" timestamp with time zone;

-- Existing links are backfilled as already acknowledged, or every link ever
-- made would fire a notice on the next dashboard load, falsely telling a
-- child a parent "has just linked" to a relationship that's months old.
--
-- `created_at` rather than `now()`, so the stamp records when the child was
-- implicitly already aware, not when this migration ran.
UPDATE "public"."parent_child_links"
   SET "student_ack_at" = "created_at"
 WHERE "student_ack_at" IS NULL;

-- The student's own unacknowledged links, cheaply -- this read runs on every
-- dashboard load and is almost always empty.
--
-- Partial on `student_ack_at IS NULL`, so the index holds only the rows the
-- query wants and stays near-empty in steady state.
CREATE INDEX IF NOT EXISTS "pcl_child_unacked_idx"
  ON "public"."parent_child_links" ("child_id")
  WHERE "student_ack_at" IS NULL;

-- The column has to be taken away from the client, or it's decorative.
--
-- `pcl: own` is a FOR ALL policy on `auth.uid() = parent_id`, and
-- `authenticated` still holds UPDATE, so without this a parent could PATCH
-- their own link row through PostgREST and stamp `student_ack_at`
-- themselves -- silently clearing the notice using the column added to give
-- it to the child. RLS narrows which rows a command touches, never which
-- columns, and only a grant can express "not this field".
--
-- The rest of the row is untouched, so unlinking through PostgREST still
-- works as before. Nothing in `frontend/src` reads or writes this table at
-- all -- both paths go through the backend's service-role client -- so this
-- costs nothing today and closes the hole regardless.
REVOKE UPDATE ("student_ack_at") ON TABLE "public"."parent_child_links" FROM "anon";
REVOKE UPDATE ("student_ack_at") ON TABLE "public"."parent_child_links" FROM "authenticated";
REVOKE INSERT ("student_ack_at") ON TABLE "public"."parent_child_links" FROM "anon";
REVOKE INSERT ("student_ack_at") ON TABLE "public"."parent_child_links" FROM "authenticated";

NOTIFY pgrst, 'reload schema';
