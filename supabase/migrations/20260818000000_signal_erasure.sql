-- Erasure on explicit parent request: destroy one channel's stored signals
-- (and derived rollups and charts) for one student, leaving a tombstone.
-- Never triggered by a consent change; withdrawal keeps history.

CREATE TABLE IF NOT EXISTS "public"."signal_erasure" (
    "user_id"      "uuid" NOT NULL
                   REFERENCES "public"."profiles"("id") ON DELETE CASCADE,
    -- Sensor names, matching signal_consent.
    "channel"      "text" NOT NULL
                   CHECK ("channel" IN ('eeg', 'headband_optical', 'camera')),
    "erased_at"    timestamptz NOT NULL DEFAULT now(),
    -- SET NULL so the record outlives the parent's account.
    "erased_by"    "uuid" REFERENCES "public"."profiles"("id") ON DELETE SET NULL,
    -- Rows deleted per table, cumulative.
    "rows_deleted" "jsonb" NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY ("user_id", "channel")
);

COMMENT ON TABLE "public"."signal_erasure" IS
    'One row per student per channel whose stored signals have been erased on '
    'request. Withdrawal of consent does NOT write here -- that keeps history.';

-- Re-erasing updates the row in place.

ALTER TABLE "public"."signal_erasure" ENABLE ROW LEVEL SECURITY;

-- Read-only to clients: the only writer is erase_signals.
CREATE POLICY "signal_erasure: own" ON "public"."signal_erasure"
    FOR SELECT TO "authenticated"
    USING ("auth"."uid"() = "user_id");

CREATE POLICY "signal_erasure: linked parent" ON "public"."signal_erasure"
    FOR SELECT TO "authenticated"
    USING (EXISTS (SELECT 1 FROM "public"."parent_child_links" l
                    WHERE l."child_id" = "signal_erasure"."user_id"
                      AND l."parent_id" = "auth"."uid"()));

REVOKE ALL ON TABLE "public"."signal_erasure" FROM "anon";
REVOKE ALL ON TABLE "public"."signal_erasure" FROM "authenticated";
GRANT SELECT ON TABLE "public"."signal_erasure" TO "authenticated";
GRANT ALL ON TABLE "public"."signal_erasure" TO "service_role";

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

    -- Tombstone last: it claims the work above is done.
    INSERT INTO signal_erasure (user_id, channel, erased_at, erased_by, rows_deleted)
    VALUES (p_user_id, p_channel, now(), p_erased_by,
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

-- Takes the subject as a parameter, so service_role only.
REVOKE ALL ON FUNCTION "public"."erase_signals"("uuid", "text", "uuid", "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."erase_signals"("uuid", "text", "uuid", "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."erase_signals"("uuid", "text", "uuid", "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."erase_signals"("uuid", "text", "uuid", "text") TO "service_role";

NOTIFY pgrst, 'reload schema';
