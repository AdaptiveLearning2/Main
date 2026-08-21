-- A parent is told when their child switches a sensor off.
--
-- The consent model already notifies in one direction: a parent re-enabling
-- a channel raises `needs_student_ack`. The reverse had nothing, even though
-- a withdrawal stands until a parent restores it -- the only way to find out
-- was opening Settings.
--
-- On `parent_child_links`, not `signal_consent`: that table has one row per
-- student, so with two parents linked, the first to acknowledge would clear
-- the notice for the second, who'd never learn their child turned a camera
-- off. The link row is already the (parent, child) pair this needs.
--
-- Notify, not gate -- same as the link notice. Nothing waits on this, and a
-- parent who never acknowledges loses nothing but the banner.

ALTER TABLE "public"."parent_child_links"
  ADD COLUMN IF NOT EXISTS "parent_ack_at" timestamp with time zone;

-- Backfilled to now(), so a parent doesn't open the dashboard tomorrow to a
-- notice about a withdrawal from last term. Unlike `student_ack_at`, which
-- backfills from `created_at` (when the link was made), the question here is
-- when the parent was last considered up to date -- everything already on
-- screen in Settings counts as seen, and everything after this is news.
UPDATE "public"."parent_child_links"
   SET "parent_ack_at" = "now"()
 WHERE "parent_ack_at" IS NULL;

-- Same reasoning as `student_ack_at`: `pcl: own` is a FOR ALL policy and
-- `authenticated` holds UPDATE, so without this a parent could stamp the
-- column directly through PostgREST -- harmless in intent, but it would
-- leave two writers for one field. Revoked per column, so the rest of the
-- row is untouched and unlinking still works.
REVOKE UPDATE ("parent_ack_at") ON TABLE "public"."parent_child_links" FROM "anon";
REVOKE UPDATE ("parent_ack_at") ON TABLE "public"."parent_child_links" FROM "authenticated";
REVOKE INSERT ("parent_ack_at") ON TABLE "public"."parent_child_links" FROM "anon";
REVOKE INSERT ("parent_ack_at") ON TABLE "public"."parent_child_links" FROM "authenticated";

NOTIFY pgrst, 'reload schema';
