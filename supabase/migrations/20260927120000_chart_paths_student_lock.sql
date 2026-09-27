-- One per-student advisory lock between an erasure and the archive's chart_paths write: the
-- archive writes, then re-reads signal_erasure, so unlocked it could do both between the
-- erasure's UPDATE of sessions and its commit. Advisory, not a row lock, so answers, /end and
-- ingestion never wait on an erasure. erase_signals also stamps with clock_timestamp().
CREATE OR REPLACE FUNCTION "public"."erase_signals"(
    "p_user_id" "uuid",
    "p_channel" "text",
    "p_erased_by" "uuid" DEFAULT NULL,
    "p_timezone" "text" DEFAULT 'UTC'
) RETURNS "jsonb"
LANGUAGE "plpgsql"
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
DECLARE
    n_cognitive int := 0;
    n_face      int := 0;
    n_heart     int := 0;
    n_rollup    int := 0;
    d           date;
    charts      "text"[];
    objects     "text"[] := ARRAY[]::"text"[];
BEGIN
    IF p_channel NOT IN ('eeg', 'headband_optical', 'camera') THEN
        RAISE EXCEPTION 'unknown channel %', p_channel;
    END IF;

    -- Exclusive: a record_chart_paths now waits for this commit, then its caller's re-read
    -- sees the tombstone; one already written is seen by the UPDATE below. Same key there.
    PERFORM pg_advisory_xact_lock(hashtextextended('chart_paths:' || p_user_id::text, 0));

    -- Not batched: one transaction, so a half-finished erasure cannot report success.

    IF p_channel = 'eeg' THEN
        DELETE FROM cognitive_signals WHERE user_id = p_user_id;
        GET DIAGNOSTICS n_cognitive = ROW_COUNT;
    END IF;

    IF p_channel = 'camera' THEN
        DELETE FROM face_signals WHERE user_id = p_user_id;
        GET DIAGNOSTICS n_face = ROW_COUNT;
    END IF;

    -- Keyed on `source`: erasing the camera leaves headband heart rows.
    IF p_channel IN ('camera', 'headband_optical') THEN
        DELETE FROM heart_signals
         WHERE user_id = p_user_id
           AND source = CASE p_channel WHEN 'camera' THEN 'rppg'
                                       ELSE 'muse_optics' END;
        GET DIAGNOSTICS n_heart = ROW_COUNT;
    END IF;

    -- Delete before rebuilding: rollup_signal_day's HAVING count(*) > 0 would
    -- otherwise leave the old averages standing.
    DELETE FROM signal_daily_rollup
     WHERE user_id = p_user_id
       AND channel IN (SELECT unnest(CASE p_channel
                                     WHEN 'eeg' THEN ARRAY['cognitive']
                                     WHEN 'headband_optical' THEN ARRAY['heart']
                                     ELSE ARRAY['emotion', 'heart'] END));
    GET DIAGNOSTICS n_rollup = ROW_COUNT;

    -- Rebuild for the surviving heart source; expire_signal_rows refuses a
    -- day with no rollup row.
    IF p_channel IN ('camera', 'headband_optical') THEN
        FOR d IN
            SELECT DISTINCT (ts AT TIME ZONE p_timezone)::date
              FROM heart_signals WHERE user_id = p_user_id
        LOOP
            PERFORM rollup_signal_day(p_user_id, d, p_timezone);
        END LOOP;
    END IF;

    -- A chart goes if it draws on the channel at all; heart charts mix both
    -- sensors, so over-deletion is deliberate.
    charts := CASE p_channel
              WHEN 'eeg' THEN ARRAY['cognitive_timeline']
              WHEN 'headband_optical' THEN ARRAY['heart_rate', 'stress_pie']
              ELSE ARRAY['emotion_pie', 'heart_rate', 'stress_pie'] END;

    -- Paths derived, never read from chart_paths: this is a delete list.
    SELECT COALESCE(array_agg(p_user_id || '/' || s.id || '/' || c || '.svg'), ARRAY[]::text[])
      INTO objects
      FROM sessions s CROSS JOIN unnest(charts) c
     WHERE s.user_id = p_user_id
       AND s.chart_paths IS NOT NULL
       AND s.chart_paths->>c IS NOT NULL;

    -- Null, not drop the key: "existed and erased" differs from "never
    -- attempted". The caller removes the storage objects.
    UPDATE sessions s
       SET chart_paths = s.chart_paths || (
               SELECT COALESCE(jsonb_object_agg(c, NULL), '{}'::jsonb)
                 FROM unnest(charts) c WHERE s.chart_paths ? c)
     WHERE s.user_id = p_user_id AND s.chart_paths IS NOT NULL;

    -- Tombstone last: it claims the work above is done. clock_timestamp(), not now(): now() is
    -- the transaction's start, which a slow erasure puts before an archive's re-check window.
    INSERT INTO signal_erasure (user_id, channel, erased_at, erased_by, rows_deleted)
    VALUES (p_user_id, p_channel, clock_timestamp(), p_erased_by,
            jsonb_build_object('cognitive_signals', n_cognitive,
                               'face_signals', n_face,
                               'heart_signals', n_heart,
                               'signal_daily_rollup', n_rollup,
                               'charts', coalesce(array_length(objects, 1), 0)))
    ON CONFLICT (user_id, channel) DO UPDATE SET
        erased_at = EXCLUDED.erased_at,
        erased_by = EXCLUDED.erased_by,
        -- Summed across passes.
        rows_deleted = (
            SELECT jsonb_object_agg(k, COALESCE((signal_erasure.rows_deleted->>k)::int, 0)
                                       + COALESCE((EXCLUDED.rows_deleted->>k)::int, 0))
              FROM jsonb_object_keys(EXCLUDED.rows_deleted) k);

    RETURN jsonb_build_object(
        'channel', p_channel,
        'cognitive_signals', n_cognitive,
        'face_signals', n_face,
        'heart_signals', n_heart,
        'signal_daily_rollup', n_rollup,
        -- Caller's storage work list; already unservable once chart_paths is nulled.
        'object_paths', to_jsonb(objects));
END;
$$;

-- Same signature, so CREATE OR REPLACE keeps the ACL; restated so this file stands alone.
REVOKE ALL ON FUNCTION "public"."erase_signals"("uuid", "text", "uuid", "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."erase_signals"("uuid", "text", "uuid", "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."erase_signals"("uuid", "text", "uuid", "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."erase_signals"("uuid", "text", "uuid", "text") TO "service_role";


-- The archive's write of a session's chart_paths, under the shared form of erase_signals' lock.
-- The student comes from the row, never the caller, so the key cannot be the wrong one.
CREATE OR REPLACE FUNCTION "public"."record_chart_paths"(
    "p_session_id" "uuid",
    "p_paths" "jsonb"
) RETURNS "void"
LANGUAGE "plpgsql"
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
DECLARE
    owner "uuid";
BEGIN
    SELECT user_id INTO owner FROM sessions WHERE id = p_session_id;
    IF owner IS NULL THEN
        RETURN;
    END IF;
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('chart_paths:' || owner::text, 0));
    UPDATE sessions SET chart_paths = p_paths WHERE id = p_session_id;
END;
$$;

-- Writes any session's chart_paths, so service_role only.
REVOKE ALL ON FUNCTION "public"."record_chart_paths"("uuid", "jsonb") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."record_chart_paths"("uuid", "jsonb") FROM "anon";
REVOKE ALL ON FUNCTION "public"."record_chart_paths"("uuid", "jsonb") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."record_chart_paths"("uuid", "jsonb") TO "service_role";

NOTIFY pgrst, 'reload schema';
