-- A retried ingest batch must not double-count: one sample per window per
-- source, `ts` = window end, inserted ON CONFLICT DO NOTHING. `source` is in
-- the key because two sources can report the same instant.

CREATE UNIQUE INDEX IF NOT EXISTS "heart_session_source_ts_key"
    ON "public"."heart_signals" ("session_id", "source", "ts");

NOTIFY pgrst, 'reload schema';
