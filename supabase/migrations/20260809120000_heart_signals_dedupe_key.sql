-- A retried ingest batch must not double-count every average.
--
-- `heart_signals` deliberately left duplicate handling open, because the
-- answer depends on whether `ts` is genuinely unique per source and that was
-- not knowable before the producer existed. It is now: the derivation emits
-- one sample per analysis window per source, and `ts` is the window's end.
-- Two samples from the same source cannot legitimately share it.
--
-- So the key is (session_id, source, ts) and the endpoint inserts with
-- ON CONFLICT DO NOTHING. A retry is then idempotent rather than additive,
-- which matters because the failure is silent: nothing about a doubled row is
-- visible in a chart except that the average is wrong.
--
-- `source` is in the key rather than assumed away. Two sources may legitimately
-- report the same instant -- that is the whole point of a source-tagged stream
-- -- so a key without it would discard the second one as a duplicate.
--
-- Doing this now is cheap and doing it later is not. Adding a unique index over
-- existing rows means deduplicating them first, and the duplicates that would
-- need removing are exactly the ones nobody noticed. The table has no writer
-- yet, so it is provably empty and this cannot fail on existing data.

CREATE UNIQUE INDEX IF NOT EXISTS "heart_session_source_ts_key"
    ON "public"."heart_signals" ("session_id", "source", "ts");

-- `face_signals` has the same exposure and is deliberately NOT changed here.
--
-- It has had a live ingest endpoint for a while now, so unlike heart_signals it
-- may already hold rows -- possibly duplicates. A `CREATE UNIQUE INDEX` over
-- them would fail the migration in production while passing CI, which applies
-- migrations to an empty stack and would therefore prove nothing. Deleting the
-- duplicates from inside a migration is worse: it is destructive, it runs
-- automatically on merge, and no one would see what it removed.
--
-- The decision is made, only the execution is deferred, and this is the check
-- that gates it -- run it in the dashboard SQL editor against production:
--
--   SELECT session_id, ts, count(*)
--   FROM public.face_signals
--   GROUP BY session_id, ts HAVING count(*) > 1;
--
-- Empty result: add `CREATE UNIQUE INDEX ... ON face_signals (session_id, ts)`
-- in its own migration and switch `ingest_face` to ON CONFLICT DO NOTHING.
-- Non-empty: decide what the duplicates mean before removing anything, because
-- by then they are real recorded data about a real student.
--
-- (No `source` column on that table: `face_signals` is emotion only, and the
-- camera is the sole producer.)

NOTIFY pgrst, 'reload schema';
