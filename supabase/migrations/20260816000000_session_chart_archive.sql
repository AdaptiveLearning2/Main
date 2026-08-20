-- Archived session charts: the bucket and the column that indexes it.
--
-- At session close the backend renders the session's charts to standalone SVG
-- (`chart_render.py`) and uploads them here. They are the human-readable
-- record of the year: when the end-of-year delete job removes per-sample rows
-- on `ends_on`, these objects and `signal_daily_rollup` are what survive.

-- ── the bucket ──────────────────────────────────────────────────────────────
--
-- Created here rather than in `supabase/config.toml`. That file configures the
-- *local* stack only, so declaring the bucket there would leave production
-- depending on someone having clicked the same settings into the dashboard --
-- and a bucket that exists locally and not in production fails at the first
-- upload, in the out-of-band path, where nothing is waiting on the result.
-- A migration is the one mechanism that reaches both.
--
-- **Private, and there is no second option.** A public bucket serves every
-- object to anyone holding the URL, and a URL travels: once one is pasted into
-- a message, no policy added later can un-share it. These objects are charts of
-- a named child's cognitive and physiological signals. Access is a short-lived
-- signed URL issued by the backend after `_verify_can_view_student`.
--
-- `file_size_limit` is a hard bound on a path with no other one. The renderer
-- produces a few kB and the read that feeds it is capped, but the limit is what
-- makes that a property of the bucket rather than of the current caller.
--
-- ON CONFLICT so re-running the migration is a no-op rather than an error, and
-- so a bucket someone created by hand first is adopted instead of colliding.
INSERT INTO "storage"."buckets" ("id", "name", "public", "file_size_limit",
                                 "allowed_mime_types")
VALUES ('session-charts', 'session-charts', false, 262144,
        ARRAY['image/svg+xml'])
ON CONFLICT ("id") DO NOTHING;

-- No policies on `storage.objects` for this bucket, deliberately -- the same
-- model as `retention_window`. RLS is already enabled on that table by Supabase,
-- and with no policy granting a role anything, every command is denied for
-- anyone who is not BYPASSRLS. `service_role` bypasses it, which is the backend,
-- which is the only writer and the only reader. Adding permissive policies for
-- `authenticated` would create a second access path that has to agree with
-- `_verify_can_view_student` forever, for no caller that exists: nothing in the
-- frontend touches storage directly.

-- ── the index ───────────────────────────────────────────────────────────────
--
-- Which objects a session has, so a reader never has to list the bucket to find
-- out -- and, more importantly, so **a chart that was never rendered is
-- distinguishable from one that failed to upload**. The shape is
-- `{"cognitive_timeline": "<path>" | null, ...}`, one key per chart in
-- `chart_render.CHART_NAMES`:
--
--   * a **path** -- rendered and uploaded.
--   * **null** -- the channel produced nothing to draw, because it was off,
--     unconsented, or recorded no usable window. The absence is the record; an
--     empty chart would assert the session had that channel and it read flat.
--   * a **missing key** -- not attempted. Every session closed before this
--     shipped, plus any close where the archive job never ran.
--
-- Column-null is a fourth state and means the same as an absent key: nothing
-- has been written for this session. That is why the column has no default --
-- `'{}'::jsonb` would claim every historical session was archived and found
-- nothing, which is the report-an-absence-as-data failure this project takes
-- care to avoid throughout.
ALTER TABLE "public"."sessions"
    ADD COLUMN IF NOT EXISTS "chart_paths" jsonb;

COMMENT ON COLUMN "public"."sessions"."chart_paths" IS
    'Archived chart objects in the private session-charts bucket, one key per '
    'chart_render.CHART_NAMES: a path, null for a channel that produced '
    'nothing, absent for a chart never attempted. NULL column = not archived.';

-- No grant changes. `sessions` already carries its own grants and policies, and
-- this column rides on them: a student reads their own row, so they can see
-- which charts exist without being able to fetch one -- the object itself still
-- needs a signed URL from the backend.

NOTIFY pgrst, 'reload schema';
