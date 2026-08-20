-- Erasure on request: destroy one channel's stored signals for one student,
-- and record that it happened.
--
-- This is not withdrawal -- withdrawing consent stops future recording and
-- keeps what's stored. Erasure is a separate, explicit request: a linked
-- parent asking for the history itself to go. Nothing here runs on a consent
-- change; it runs only when someone asks for it by name.
--
-- Four decisions shape what follows:
--
--   * A linked parent only. A student may withdraw and only a parent
--     re-enable, safe because a parent can undo it -- erasure can't be undone
--     by anyone, so only a parent may order it.
--   * One channel at a time, named as `signal_consent` names them. Erasing
--     `camera` must leave headband-derived heart rows standing, so the heart
--     deletes below are keyed on `source`, not on the table.
--   * Derived data goes too: `signal_daily_rollup` holds averages of the
--     erased data and the archived SVGs are pictures of it.
--   * A tombstone stays -- one row per student per channel, so a reporting
--     surface can say "erased" instead of rendering a blank.

CREATE TABLE IF NOT EXISTS "public"."signal_erasure" (
    "user_id"      "uuid" NOT NULL
                   REFERENCES "public"."profiles"("id") ON DELETE CASCADE,
    -- Named for the sensor, matching signal_consent -- what a parent decided
    -- about, not what the derivation produced.
    "channel"      "text" NOT NULL
                   CHECK ("channel" IN ('eeg', 'headband_optical', 'camera')),
    "erased_at"    timestamptz NOT NULL DEFAULT now(),
    -- The parent who asked, kept as an identity rather than a role (unlike
    -- signal_consent's revoked_by, which is surfaced to teachers). ON DELETE
    -- SET NULL so the record of an erasure outlives the account that ordered
    -- it, rather than making a deleted parent's erasure look like it never
    -- happened.
    "erased_by"    "uuid" REFERENCES "public"."profiles"("id") ON DELETE SET NULL,
    -- What was deleted, per table, for the caller's confirmation and for any
    -- later question about what an erasure actually covered.
    "rows_deleted" "jsonb" NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY ("user_id", "channel")
);

COMMENT ON TABLE "public"."signal_erasure" IS
    'One row per student per channel whose stored signals have been erased on '
    'request. Withdrawal of consent does NOT write here -- that keeps history.';

-- Re-erasing a channel updates `erased_at` in place rather than accumulating
-- rows -- a parent who erases, re-consents, and erases again is describing
-- one ongoing position, not two events to list.

ALTER TABLE "public"."signal_erasure" ENABLE ROW LEVEL SECURITY;

-- Read-your-own, plus a linked parent reading their child's. No insert,
-- update or delete policy for anyone, so PostgREST can't write this table
-- under any JWT -- the only correct writer is `erase_signals` below, and a
-- tombstone a client could delete would let an erasure be hidden.
CREATE POLICY "signal_erasure: own" ON "public"."signal_erasure"
    FOR SELECT TO "authenticated"
    USING ("auth"."uid"() = "user_id");

CREATE POLICY "signal_erasure: linked parent" ON "public"."signal_erasure"
    FOR SELECT TO "authenticated"
    USING (EXISTS (SELECT 1 FROM "public"."parent_child_links" l
                    WHERE l."child_id" = "signal_erasure"."user_id"
                      AND l."parent_id" = "auth"."uid"()));

-- Revoke before granting: Supabase already grants every privilege to these
-- roles by name, and a named grant isn't narrowed by adding a smaller one.
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

    -- Not batched, unlike `expire_signal_rows` -- this touches one child's
    -- rows, not the whole instance. A half-finished erasure that reported
    -- success would be unrecoverable, so it's one transaction or none.

    IF p_channel = 'eeg' THEN
        DELETE FROM cognitive_signals WHERE user_id = p_user_id;
        GET DIAGNOSTICS n_cognitive = ROW_COUNT;
    END IF;

    IF p_channel = 'camera' THEN
        DELETE FROM face_signals WHERE user_id = p_user_id;
        GET DIAGNOSTICS n_face = ROW_COUNT;
    END IF;

    -- Keyed on `source`, not the table: both sensors write heart_signals, and
    -- erasing the camera says nothing about the headband.
    IF p_channel IN ('camera', 'headband_optical') THEN
        DELETE FROM heart_signals
         WHERE user_id = p_user_id
           AND source = CASE p_channel WHEN 'camera' THEN 'rppg'
                                       ELSE 'muse_optics' END;
        GET DIAGNOSTICS n_heart = ROW_COUNT;
    END IF;

    -- Derived rows: deleted and then rebuilt, rather than left for
    -- `rollup_signal_day` to correct. That function's `HAVING count(*) > 0`
    -- means with the raw rows gone it inserts nothing and leaves the
    -- existing rollup standing -- averages of erased data surviving the
    -- erasure. Deleting first makes the rebuild below a real recomputation.
    DELETE FROM signal_daily_rollup
     WHERE user_id = p_user_id
       AND channel IN (SELECT unnest(CASE p_channel
                                     WHEN 'eeg' THEN ARRAY['cognitive']
                                     WHEN 'headband_optical' THEN ARRAY['heart']
                                     ELSE ARRAY['emotion', 'heart'] END));
    GET DIAGNOSTICS n_rollup = ROW_COUNT;

    -- Only the heart channel can have survivors: erasing one heart source
    -- leaves the other's rows, and their rollup has to be rebuilt or that data
    -- becomes unreadable. It also matters because `expire_signal_rows`
    -- refuses to delete a day's raw rows with no rollup row, so a day left
    -- without one would keep its raw rows past `ends_on`.
    IF p_channel IN ('camera', 'headband_optical') THEN
        FOR d IN
            SELECT DISTINCT (ts AT TIME ZONE p_timezone)::date
              FROM heart_signals WHERE user_id = p_user_id
        LOOP
            PERFORM rollup_signal_day(p_user_id, d, p_timezone);
        END LOOP;
    END IF;

    -- Archived charts: the only copy outside the database. A chart is erased
    -- if it draws on the erased channel at all, so `camera` takes the two
    -- heart charts with it too -- they mix headband and camera sources, and
    -- nothing in an SVG says which pixels came from which sensor. That
    -- over-deletes a headband chart on a camera erasure, accepted
    -- deliberately over serving a picture that still contains erased data.
    charts := CASE p_channel
              WHEN 'eeg' THEN ARRAY['cognitive_timeline']
              WHEN 'headband_optical' THEN ARRAY['heart_rate', 'stress_pie']
              ELSE ARRAY['emotion_pie', 'heart_rate', 'stress_pie'] END;

    -- Paths are derived here, never read out of `chart_paths` -- this list is
    -- a delete list, so a value taken from that column would let a client
    -- point it at an object of their choosing.
    SELECT COALESCE(array_agg(p_user_id || '/' || s.id || '/' || c || '.svg'), ARRAY[]::text[])
      INTO objects
      FROM sessions s CROSS JOIN unnest(charts) c
     WHERE s.user_id = p_user_id
       AND s.chart_paths IS NOT NULL
       AND s.chart_paths->>c IS NOT NULL;

    -- Null rather than drop the key: the chart existed and no longer does,
    -- a different fact from one never attempted. The caller removes the
    -- storage objects themselves -- SQL can't reach object storage -- so once
    -- this commits the charts are unreachable through the product regardless.
    UPDATE sessions s
       SET chart_paths = s.chart_paths || (
               SELECT COALESCE(jsonb_object_agg(c, NULL), '{}'::jsonb)
                 FROM unnest(charts) c WHERE s.chart_paths ? c)
     WHERE s.user_id = p_user_id AND s.chart_paths IS NOT NULL;

    -- The tombstone, last: it claims the work above is done, so it can't be
    -- written before the work is. Same transaction, so a failure anywhere
    -- rolls the claim back with the deletes.
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
        -- Summed, not replaced: the row describes everything this channel has
        -- ever had erased, not just the latest pass.
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
        -- The caller's work list, for removing the storage objects. Safe to
        -- return rather than await: `chart_paths` no longer points at these,
        -- so they're already unservable even if the caller never removes them.
        'object_paths', to_jsonb(objects));
END;
$$;

-- This function destroys a child's stored biometrics and takes the subject as
-- a parameter, so it can't stay ambiently callable -- that would hand every
-- logged-in user a delete button for anyone's history.
REVOKE ALL ON FUNCTION "public"."erase_signals"("uuid", "text", "uuid", "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."erase_signals"("uuid", "text", "uuid", "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."erase_signals"("uuid", "text", "uuid", "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."erase_signals"("uuid", "text", "uuid", "text") TO "service_role";

NOTIFY pgrst, 'reload schema';
