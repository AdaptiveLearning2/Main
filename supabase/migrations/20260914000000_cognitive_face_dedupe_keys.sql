-- `cognitive_signals` and `face_signals` get the dedupe key `heart_signals`
-- has had since 20260809120000, which left instructions for exactly this and
-- is worth reading alongside.
--
-- Why it matters, and it is not hypothetical: two writers can be live at once.
-- `eeg_poller` writes `cognitive_signals` with the service-role client under
-- `INGEST_MODE=pull`, and the sidecar's push client posts to
-- `/api/signals/cognitive`. CLAUDE.md states the consequence outright -- both
-- running means every EEG sample lands twice with no error -- and
-- `/api/v1/push/start` refuses under `pull` specifically because there was no
-- key to make the overlap harmless. The same is true of a push batch re-sent
-- after its POST committed but before the client saw the response, which
-- `push_client` is explicit about being unable to rule out.
--
-- A duplicate row surfaces nowhere. It is not an error, not a log line, and
-- not visible on any dashboard -- it is a slightly wrong average, and the
-- rollup that outlives the raw rows carries it forward.
--
-- THE KEY IS (session_id, ts), AND IT ONLY WORKS BECAUSE `ts` IS THE SAMPLE'S.
-- Both mappers take it from the sidecar's own `timestamp` (`signal_mapping`,
-- `"ts": eeg.get("timestamp")` and `payload.get("timestamp")`), not from
-- insertion time -- so a replayed batch carries the same stamps, and a poller
-- and a pusher reading one sidecar snapshot produce the same stamp. Keyed on
-- an insertion-time default instead, this index would be satisfied by every
-- duplicate it exists to stop.
--
-- No `source` column on either, unlike heart: `cognitive_signals` has one
-- producer per session (the device id rides inside `raw`, not as a column),
-- and `face_signals` is the camera alone.
--
-- CHECKED AGAINST PRODUCTION FIRST, which 20260809120000 insists on and is
-- right to: a unique index passes CI against an empty stack and fails against
-- real data, and deduplicating inside a migration would be destructive,
-- automatic on merge, and invisible. Run 2026-09-02 in the dashboard:
--
--     cognitive_signals   3691 rows, 3691 distinct (session_id, ts)
--     face_signals         961 rows,  961 distinct (session_id, ts)
--
-- Zero duplicates in either, so this adds a constraint the data already
-- satisfies. Re-run that count before applying to any other deployment --
-- being clean here is a fact about this database on that date, not a property
-- of the schema.
--
-- Every writer moves to `upsert(..., ignore_duplicates=True)` in the same
-- change, and that pairing is the load-bearing part: without it the index
-- turns a silent double insert into a *raised* error, so the overlap that
-- used to corrupt data quietly would start 500ing a student's session
-- instead. Three writers -- `eeg_poller`, `/api/signals/cognitive` and
-- `/api/signals/face`.

CREATE UNIQUE INDEX IF NOT EXISTS "cog_session_ts_key"
    ON "public"."cognitive_signals" ("session_id", "ts");

CREATE UNIQUE INDEX IF NOT EXISTS "face_session_ts_key"
    ON "public"."face_signals" ("session_id", "ts");

-- The non-unique composites stay. `cog_session_ts_idx` (20260831000000) and
-- `face_session_ts_idx` (20260913000000) are `(session_id, ts)` too, so each
-- is now redundant against the unique index above -- but dropping an index is
-- the operation `20260905020000` requires `pg_stat_user_indexes.idx_scan`
-- evidence for, and nothing here has that. Left for whoever has the numbers;
-- the cost meanwhile is write amplification on two tables, not a wrong answer.

NOTIFY pgrst, 'reload schema';
