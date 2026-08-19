-- A parent is told when their child switches a sensor off.
--
-- The consent model already notifies in one direction: a parent re-enabling a
-- channel raises `needs_student_ack`, and `ParentRestoredBanner` is the surface
-- that clears it. The reverse had nothing. A student may withdraw at any time
-- and that decision stands until a parent restores it — so the withdrawal is
-- the event most worth telling a parent about, and the only way to find out was
-- to open Settings and read "Off since <date>" on a panel nobody had a reason
-- to visit.
--
-- **On `parent_child_links`, not on `signal_consent`.** The obvious mirror
-- would be a `parent_ack_at` beside `student_ack_at`, and it would be wrong:
-- `signal_consent` has one row per *student*, so with two parents linked, the
-- first to acknowledge would clear the notice for the second, who would never
-- learn their child had turned a camera off. The link row is already the
-- (parent, child) pair this has to be keyed on, and it is the row the notice is
-- about.
--
-- Notify, not gate — same call as the link notice above it. Nothing waits on
-- this, and a parent who never acknowledges loses nothing but the banner.

ALTER TABLE "public"."parent_child_links"
  ADD COLUMN IF NOT EXISTS "parent_ack_at" timestamp with time zone;

-- Backfilled to now(), so a parent does not open the dashboard tomorrow to a
-- notice about a withdrawal from last term. Unlike `student_ack_at` — which
-- backfills from `created_at`, because the question there is when the *link*
-- was made — the question here is when the parent was last considered
-- up to date, and nothing before this migration told them anything. `now()` is
-- the honest answer: everything already on screen in Settings counts as seen,
-- and everything after this is news.
UPDATE "public"."parent_child_links"
   SET "parent_ack_at" = "now"()
 WHERE "parent_ack_at" IS NULL;

-- Same reasoning as `student_ack_at` in 20260828000000: `pcl: own` is a FOR ALL
-- policy on `auth.uid() = parent_id` and `authenticated` holds UPDATE, so
-- without this a parent could stamp the column directly through PostgREST.
-- Harmless in intent — it is their own notice — but it leaves two writers for
-- one field, and the endpoint is the one that knows what "acknowledged" means.
-- Grants are per column for UPDATE and INSERT, so the rest of the row is
-- untouched and unlinking still works.
REVOKE UPDATE ("parent_ack_at") ON TABLE "public"."parent_child_links" FROM "anon";
REVOKE UPDATE ("parent_ack_at") ON TABLE "public"."parent_child_links" FROM "authenticated";
REVOKE INSERT ("parent_ack_at") ON TABLE "public"."parent_child_links" FROM "anon";
REVOKE INSERT ("parent_ack_at") ON TABLE "public"."parent_child_links" FROM "authenticated";

NOTIFY pgrst, 'reload schema';
