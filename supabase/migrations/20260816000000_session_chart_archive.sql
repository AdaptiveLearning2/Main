-- Archived session charts (SVG rendered at session close): the bucket and the
-- sessions column indexing it. They survive the end-of-year delete.

-- Here, not config.toml, which only reaches the local stack. Private: a public
-- URL once shared cannot be un-shared; access is a backend-signed URL.
INSERT INTO "storage"."buckets" ("id", "name", "public", "file_size_limit",
                                 "allowed_mime_types")
VALUES ('session-charts', 'session-charts', false, 262144,
        ARRAY['image/svg+xml'])
ON CONFLICT ("id") DO NOTHING;

-- No policies on storage.objects for this bucket: only service_role reads or writes.

-- Per chart: a path, null (channel drew nothing), or absent (not attempted).
-- No default: an empty object would claim every old session was archived.
ALTER TABLE "public"."sessions"
    ADD COLUMN IF NOT EXISTS "chart_paths" jsonb;

COMMENT ON COLUMN "public"."sessions"."chart_paths" IS
    'Archived chart objects in the private session-charts bucket, one key per '
    'chart_render.CHART_NAMES: a path, null for a channel that produced '
    'nothing, absent for a chart never attempted. NULL column = not archived.';

NOTIFY pgrst, 'reload schema';
