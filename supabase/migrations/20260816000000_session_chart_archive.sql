-- Archived session charts: the bucket and the column that indexes it. At
-- session close the backend renders the session's charts to standalone SVG
-- and uploads them here -- the human-readable record of the year, since these
-- objects and `signal_daily_rollup` are what survive the end-of-year delete.

-- Created here rather than in `supabase/config.toml`, which only configures
-- the local stack -- a migration is the one mechanism that reaches both local
-- and production.
--
-- Private, with no second option: a public bucket serves every object to
-- anyone with the URL, and once a URL is shared, no later policy can un-share
-- it. These are charts of a named child's signals. Access is a short-lived
-- signed URL issued by the backend after `_verify_can_view_student`.
--
-- `file_size_limit` is a hard bound on a path with no other one; the renderer
-- produces only a few kB, but this makes that a property of the bucket
-- rather than of the current caller.
--
-- ON CONFLICT so re-running the migration is a no-op, and so a bucket someone
-- created by hand is adopted instead of colliding.
INSERT INTO "storage"."buckets" ("id", "name", "public", "file_size_limit",
                                 "allowed_mime_types")
VALUES ('session-charts', 'session-charts', false, 262144,
        ARRAY['image/svg+xml'])
ON CONFLICT ("id") DO NOTHING;

-- No policies on `storage.objects` for this bucket, deliberately. RLS is
-- already enabled by Supabase, and with no policy granting a role anything,
-- every command is denied for anyone who isn't BYPASSRLS -- `service_role`,
-- i.e. the backend, is the only reader and writer. Nothing in the frontend
-- touches storage directly, so a permissive policy for `authenticated` would
-- only be a second access path with nothing using it.

-- Which objects a session has, so a reader never has to list the bucket --
-- and so a chart that was never rendered is distinguishable from one that
-- failed to upload. Shape: `{"cognitive_timeline": "<path>" | null, ...}`,
-- one key per chart:
--
--   * a path -- rendered and uploaded.
--   * null -- the channel produced nothing to draw (off, unconsented, or no
--     usable window). An empty chart would falsely assert the channel read
--     flat, so this stays a distinct state.
--   * a missing key -- not attempted (a session closed before this shipped,
--     or a close where the archive job never ran).
--
-- Column-null means the same as a missing key: nothing written for this
-- session. No default, since `'{}'::jsonb` would falsely claim every
-- historical session was archived and found nothing.
ALTER TABLE "public"."sessions"
    ADD COLUMN IF NOT EXISTS "chart_paths" jsonb;

COMMENT ON COLUMN "public"."sessions"."chart_paths" IS
    'Archived chart objects in the private session-charts bucket, one key per '
    'chart_render.CHART_NAMES: a path, null for a channel that produced '
    'nothing, absent for a chart never attempted. NULL column = not archived.';

-- No grant changes: this column rides on the existing `sessions` grants and
-- policies, so a student can see which charts exist without being able to
-- fetch one -- the object itself still needs a signed URL from the backend.

NOTIFY pgrst, 'reload schema';
