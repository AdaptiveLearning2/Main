-- A parent is told when their child switches a sensor off. Notify, not gate.
-- On parent_child_links, so each linked parent acknowledges separately.

ALTER TABLE "public"."parent_child_links"
  ADD COLUMN IF NOT EXISTS "parent_ack_at" timestamp with time zone;

-- now(): withdrawals before this migration count as seen.
UPDATE "public"."parent_child_links"
   SET "parent_ack_at" = "now"()
 WHERE "parent_ack_at" IS NULL;

-- Column revoke, as for student_ack_at: one writer for the field.
REVOKE UPDATE ("parent_ack_at") ON TABLE "public"."parent_child_links" FROM "anon";
REVOKE UPDATE ("parent_ack_at") ON TABLE "public"."parent_child_links" FROM "authenticated";
REVOKE INSERT ("parent_ack_at") ON TABLE "public"."parent_child_links" FROM "anon";
REVOKE INSERT ("parent_ack_at") ON TABLE "public"."parent_child_links" FROM "authenticated";

NOTIFY pgrst, 'reload schema';
