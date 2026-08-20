-- A retried ingest batch must not double-count every average. Each derivation
-- emits one sample per analysis window per source, and `ts` is the window's
-- end, so two samples from the same source can't legitimately share one.
--
-- The key is (session_id, source, ts), and the endpoint inserts with
-- ON CONFLICT DO NOTHING, so a retry is idempotent rather than additive --
-- important because a duplicate row shows up nowhere except a slightly wrong
-- average, with nothing to flag it.
--
-- `source` is part of the key because two sources can legitimately report the
-- same instant; without it, the second source's row would look like a
-- duplicate and be dropped.
--
-- Added now while the table is still empty, since deduplicating existing rows
-- before adding a unique index is much harder once there's real data.

CREATE UNIQUE INDEX IF NOT EXISTS "heart_session_source_ts_key"
    ON "public"."heart_signals" ("session_id", "source", "ts");

-- `face_signals` has the same exposure and is deliberately not changed here.
-- Unlike heart_signals it already has a live ingest endpoint, so it may
-- already hold duplicate rows. A unique index would fail against production
-- data even though CI (an empty stack) would pass, and deleting duplicates
-- inside a migration is worse -- destructive, automatic on merge, and nobody
-- would see what it removed.
--
-- Check production first, in the dashboard SQL editor:
--
--   SELECT session_id, ts, count(*)
--   FROM public.face_signals
--   GROUP BY session_id, ts HAVING count(*) > 1;
--
-- Empty: add the unique index in its own migration and switch `ingest_face`
-- to ON CONFLICT DO NOTHING. Non-empty: decide what the duplicates mean
-- before removing anything -- they're real recorded data about a real
-- student by then.
--
-- (No `source` column here: face_signals is emotion only, and the camera is
-- the sole producer.)

NOTIFY pgrst, 'reload schema';
