-- A student is told when a parent links to them. Notify, not block. Per link,
-- so a second parent's link raises its own notice.

ALTER TABLE "public"."parent_child_links"
  ADD COLUMN IF NOT EXISTS "student_ack_at" timestamp with time zone;

-- Existing links count as acknowledged, as of when they were made.
UPDATE "public"."parent_child_links"
   SET "student_ack_at" = "created_at"
 WHERE "student_ack_at" IS NULL;

-- Partial: read on every dashboard load, near-empty in steady state.
CREATE INDEX IF NOT EXISTS "pcl_child_unacked_idx"
  ON "public"."parent_child_links" ("child_id")
  WHERE "student_ack_at" IS NULL;

-- `pcl: own` is FOR ALL, so without a column revoke a parent could clear the
-- child's notice. RLS narrows rows, never columns.
REVOKE UPDATE ("student_ack_at") ON TABLE "public"."parent_child_links" FROM "anon";
REVOKE UPDATE ("student_ack_at") ON TABLE "public"."parent_child_links" FROM "authenticated";
REVOKE INSERT ("student_ack_at") ON TABLE "public"."parent_child_links" FROM "anon";
REVOKE INSERT ("student_ack_at") ON TABLE "public"."parent_child_links" FROM "authenticated";

NOTIFY pgrst, 'reload schema';
