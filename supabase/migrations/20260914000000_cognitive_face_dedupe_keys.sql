-- Dedupe key (session_id, ts) on cognitive and face signals, so a retried or
-- overlapping write cannot double-count; `ts` is the sample's own timestamp.
-- A unique index passes CI against an empty stack and fails against real data:
-- 0 duplicates in production on 2026-09-02 (3691 cog, 961 face rows); re-check
-- elsewhere. Writers must upsert(ignore_duplicates=True), or this raises.

CREATE UNIQUE INDEX IF NOT EXISTS "cog_session_ts_key"
    ON "public"."cognitive_signals" ("session_id", "ts");

CREATE UNIQUE INDEX IF NOT EXISTS "face_session_ts_key"
    ON "public"."face_signals" ("session_id", "ts");

-- The now-redundant non-unique composites stay until idx_scan evidence exists.

NOTIFY pgrst, 'reload schema';
