-- Erasure on request: destroy one channel's stored signals for one student,
-- and record that it happened (#75).
--
-- **This is not withdrawal.** Withdrawing consent stops future recording and
-- keeps what is stored -- decided 2026-08-10 and unchanged. This is the
-- separate, explicit request that issue left open: a linked parent asking for
-- the history itself to go. Nothing here runs on a consent change; it runs only
-- when someone asks for it by name.
--
-- Four decisions shape what follows, all taken deliberately:
--
--   * **A linked parent only.** The consent model already lets a student
--     withdraw and only a parent re-enable, and that asymmetry is safe because
--     a parent can undo it. Erasure cannot be undone by anyone, so the party
--     who can order it is the one who can weigh that.
--   * **One channel at a time**, named as `signal_consent` names them. Erasing
--     `camera` must leave headband-derived heart rows standing, which is why
--     the heart deletes below are keyed on `source` and not on the table.
--   * **Derived data goes too.** `signal_daily_rollup` holds averages of the
--     child's biometrics and the archived SVGs are pictures of them; leaving
--     either would mean the raw rows are gone and the data is not.
--   * **A tombstone stays.** One row per student per channel, so a reporting
--     surface can say "erased" rather than rendering an absence -- the same
--     rule that governs every other blank tile in this project.

-- ── the tombstone ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "public"."signal_erasure" (
    "user_id"      "uuid" NOT NULL
                   REFERENCES "public"."profiles"("id") ON DELETE CASCADE,
    -- Named for the sensor, matching signal_consent. A channel here is what a
    -- parent decided about, not what the derivation produced.
    "channel"      "text" NOT NULL
                   CHECK ("channel" IN ('eeg', 'headband_optical', 'camera')),
    "erased_at"    timestamptz NOT NULL DEFAULT now(),
    -- The parent who asked. An identity, not a role, unlike `revoked_by` on
    -- signal_consent -- that one is surfaced to teachers and this one is not.
    -- ON DELETE SET NULL: the record that an erasure happened must outlive the
    -- account of whoever ordered it, or deleting a parent quietly converts an
    -- erased term into one that reads as never recorded.
    "erased_by"    "uuid" REFERENCES "public"."profiles"("id") ON DELETE SET NULL,
    -- What went, per table, for the confirmation the caller is shown and for
    -- any later question about what an erasure actually covered.
    "rows_deleted" "jsonb" NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY ("user_id", "channel")
);

COMMENT ON TABLE "public"."signal_erasure" IS
    'One row per student per channel whose stored signals have been erased on '
    'request. Withdrawal of consent does NOT write here -- that keeps history.';

-- Re-erasing a channel updates `erased_at` in place rather than accumulating
-- rows. The useful fact is "this channel has been erased, most recently on
-- <date>"; a parent who erases, re-consents, and erases again is describing one
-- ongoing position, not two events to be listed.

ALTER TABLE "public"."signal_erasure" ENABLE ROW LEVEL SECURITY;

-- Read-your-own, and a linked parent reads their child's -- matching who is
-- allowed to see that an erasure happened. There is deliberately no insert,
-- update or delete policy for anyone: with RLS on, that denies every write
-- through PostgREST whatever JWT it carries. The only correct writer is
-- `erase_signals` below, and a tombstone a client could delete would let the
-- erasure be hidden rather than reported.
CREATE POLICY "signal_erasure: own" ON "public"."signal_erasure"
    FOR SELECT TO "authenticated"
    USING ("auth"."uid"() = "user_id");

CREATE POLICY "signal_erasure: linked parent" ON "public"."signal_erasure"
    FOR SELECT TO "authenticated"
    USING (EXISTS (SELECT 1 FROM "public"."parent_child_links" l
                    WHERE l."child_id" = "signal_erasure"."user_id"
                      AND l."parent_id" = "auth"."uid"()));

-- Revoke before granting: Supabase's ALTER DEFAULT PRIVILEGES hands every
-- privilege to these roles by name, and an explicit named grant is not
-- narrowed by adding another. Only a REVOKE removes it.
REVOKE ALL ON TABLE "public"."signal_erasure" FROM "anon";
REVOKE ALL ON TABLE "public"."signal_erasure" FROM "authenticated";
GRANT SELECT ON TABLE "public"."signal_erasure" TO "authenticated";
GRANT ALL ON TABLE "public"."signal_erasure" TO "service_role";

-- ── the delete ──────────────────────────────────────────────────────────────

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

    -- Not batched, unlike `expire_signal_rows`. That one sweeps every student
    -- in the instance and would hold locks across the table; this is one
    -- child's rows, bounded by their own sessions. Correctness is worth more
    -- here than throughput: a half-finished erasure that reported success is
    -- the failure with no recovery, so it is one transaction or none.

    IF p_channel = 'eeg' THEN
        DELETE FROM cognitive_signals WHERE user_id = p_user_id;
        GET DIAGNOSTICS n_cognitive = ROW_COUNT;
    END IF;

    IF p_channel = 'camera' THEN
        DELETE FROM face_signals WHERE user_id = p_user_id;
        GET DIAGNOSTICS n_face = ROW_COUNT;
    END IF;

    -- Keyed on `source`, never on the table. Both sensors write heart_signals,
    -- and a parent erasing the camera has said nothing about the headband.
    IF p_channel IN ('camera', 'headband_optical') THEN
        DELETE FROM heart_signals
         WHERE user_id = p_user_id
           AND source = CASE p_channel WHEN 'camera' THEN 'rppg'
                                       ELSE 'muse_optics' END;
        GET DIAGNOSTICS n_heart = ROW_COUNT;
    END IF;

    -- ── the derived rows ──
    --
    -- Deleted and then rebuilt, rather than left to `rollup_signal_day` to
    -- correct. That function has `HAVING count(*) > 0` on every channel, so
    -- with the raw rows gone it inserts nothing and **leaves the existing row
    -- standing** -- averages of erased data, surviving the erasure. Deleting
    -- first is what makes the rebuild below a recomputation rather than a
    -- no-op.
    DELETE FROM signal_daily_rollup
     WHERE user_id = p_user_id
       AND channel IN (SELECT unnest(CASE p_channel
                                     WHEN 'eeg' THEN ARRAY['cognitive']
                                     WHEN 'headband_optical' THEN ARRAY['heart']
                                     ELSE ARRAY['emotion', 'heart'] END));
    GET DIAGNOSTICS n_rollup = ROW_COUNT;

    -- Only the heart channel can have survivors: erasing one heart source
    -- leaves the other's rows, and their day averages have to come back or the
    -- surviving data is unreadable. It also has to come back for a second
    -- reason -- `expire_signal_rows` refuses to delete a day's raw rows when no
    -- rollup row exists for it, so a day left without one would keep its raw
    -- rows past `ends_on`, for a reason no one would think to look for.
    IF p_channel IN ('camera', 'headband_optical') THEN
        FOR d IN
            SELECT DISTINCT (ts AT TIME ZONE p_timezone)::date
              FROM heart_signals WHERE user_id = p_user_id
        LOOP
            PERFORM rollup_signal_day(p_user_id, d, p_timezone);
        END LOOP;
    END IF;

    -- ── the archived charts ──
    --
    -- The third copy, and the only one outside the database. A chart is erased
    -- if it draws on the erased channel at all, which is why `camera` takes the
    -- two heart charts with it: those mix headband and camera sources into one
    -- picture, and nothing in an SVG says which pixels came from which sensor.
    -- That over-deletes a headband chart on a camera erasure, accepted
    -- deliberately -- the alternative is serving a picture that still contains
    -- what was erased.
    charts := CASE p_channel
              WHEN 'eeg' THEN ARRAY['cognitive_timeline']
              WHEN 'headband_optical' THEN ARRAY['heart_rate', 'stress_pie']
              ELSE ARRAY['emotion_pie', 'heart_rate', 'stress_pie'] END;

    -- Paths are **derived**, never read out of `chart_paths`. Same rule as the
    -- signing endpoint, and it matters more here: this list is a delete list,
    -- so a value taken from that column would be an instruction to destroy an
    -- object of the writer's choosing. `20260817000000` revoked the write that
    -- made that reachable; deriving is what makes it not matter.
    SELECT COALESCE(array_agg(p_user_id || '/' || s.id || '/' || c || '.svg'), ARRAY[]::text[])
      INTO objects
      FROM sessions s CROSS JOIN unnest(charts) c
     WHERE s.user_id = p_user_id
       AND s.chart_paths IS NOT NULL
       AND s.chart_paths->>c IS NOT NULL;

    -- Null rather than drop the key: the chart existed and no longer does,
    -- which is not the same fact as one that was never attempted. The caller
    -- removes the objects themselves -- SQL cannot reach object storage, and
    -- `storage.objects` refuses a direct DELETE -- so once this commits the
    -- charts are unreachable through the product whether or not that succeeds.
    UPDATE sessions s
       SET chart_paths = s.chart_paths || (
               SELECT COALESCE(jsonb_object_agg(c, NULL), '{}'::jsonb)
                 FROM unnest(charts) c WHERE s.chart_paths ? c)
     WHERE s.user_id = p_user_id AND s.chart_paths IS NOT NULL;

    -- The tombstone, last: it claims the work above is done, so it must not be
    -- written before the work is. Same transaction, so a failure anywhere rolls
    -- the claim back with the deletes.
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
        -- Summed, not replaced. The row describes everything this channel has
        -- ever had erased; a second erasure reporting only its own tail would
        -- read as the first one having taken almost nothing.
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
        -- The caller's work list. Everything above is already committed by the
        -- time it is read, so a caller that fails to remove these leaves
        -- objects that are orphaned but unservable -- `chart_paths` no longer
        -- points at them, so the signing endpoint will not issue a URL. That is
        -- the reason this is safe to return rather than await.
        'object_paths', to_jsonb(objects));
END;
$$;

-- Postgres grants EXECUTE on new functions to PUBLIC automatically, and
-- Supabase grants it to anon and authenticated by name on top of that. This one
-- destroys a child's stored biometrics and takes the subject as a parameter, so
-- leaving it ambiently callable would be handing every logged-in user a delete
-- button for anyone's history.
REVOKE ALL ON FUNCTION "public"."erase_signals"("uuid", "text", "uuid", "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."erase_signals"("uuid", "text", "uuid", "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."erase_signals"("uuid", "text", "uuid", "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."erase_signals"("uuid", "text", "uuid", "text") TO "service_role";

NOTIFY pgrst, 'reload schema';
