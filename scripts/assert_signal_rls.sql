-- RLS, CHECK and column-shape assertions for the signal tables, against a real stack (the
-- backend suite's fake client cannot test RLS). Every assertion raises. Run before merging a change here:
--   docker exec -i supabase_db_AdaptiveLearning psql -U postgres -d postgres \
--     -v ON_ERROR_STOP=1 -f - < scripts/assert_signal_rls.sql
-- BEGIN ... ROLLBACK, so safe against a working database. Never name the dollar-quote marker in a comment.

BEGIN;

-- ── column shape ────────────────────────────────────────────────────────────
-- First, so a broken fixture INSERT under ON_ERROR_STOP cannot skip them.
-- Postgres-side half of `test_the_three_unproduced_face_columns_are_kept_on_purpose`.

DO $$
DECLARE
    missing text;
    present int;
BEGIN
    -- These have no producer yet and must survive: they wait on a scope decision, not dead weight.
    FOR missing IN
        SELECT t.c FROM unnest(ARRAY['attention', 'gaze_x', 'gaze_y',
                                   'head_yaw', 'head_pitch', 'head_roll']) AS t(c)
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'face_signals'
              AND column_name = t.c)
    LOOP
        RAISE EXCEPTION
            'face_signals.% was dropped. It has no producer yet -- that is '
            'Phase 11 of the plan, not dead weight. Retiring it needs the same '
            'scope decision identity_confidence got in #86; if that has '
            'happened, delete this assertion deliberately rather than making '
            'it pass.', missing;
    END LOOP;

    -- The retired column stays retired; a rollback or an old dump would restore it silently.
    SELECT count(*) INTO present
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'face_signals'
      AND column_name = 'identity_confidence';
    IF present > 0 THEN
        RAISE EXCEPTION
            'face_signals.identity_confidence is back. It was retired in #86 '
            'as out of scope -- identifying a child by face is a different '
            'purpose from what the camera consent asks about -- so it needs '
            'its own consent channel before it needs a column. If this is a '
            'rollback, the schema is older than the code.';
    END IF;
END $$;

-- Each signal table's unique key, which makes a replayed batch a no-op; without it
-- every writer's `on_conflict` is silently inert.
DO $$
DECLARE
    spec record;
BEGIN
    FOR spec IN
        SELECT * FROM (VALUES
            ('cognitive_signals', 'cog_session_ts_key'),
            ('face_signals',      'face_session_ts_key'),
            ('heart_signals',     'heart_session_source_ts_key')
        ) AS t(tbl, idx)
        WHERE NOT EXISTS (
            SELECT 1 FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = t.tbl
              AND indexname = t.idx)
    LOOP
        RAISE EXCEPTION
            '%.% is missing. Every writer of that table upserts against it, '
            'so without it a replayed batch -- or a poller running alongside '
            'a pusher -- writes every sample twice, with no error anywhere '
            'and nothing but a wrong average to show for it.',
            spec.tbl, spec.idx;
    END LOOP;
END $$;

-- Every column `main._ACTIVITY_SOURCES` filters on; a missing one silently makes quiet sessions read LIVE.
-- The only check on these, and deliberately an independent list rather than one derived from the backend.
DO $$
DECLARE
    spec record;
BEGIN
    FOR spec IN
        SELECT * FROM (VALUES
            ('session_answers',   'answered_at'),
            ('cognitive_signals', 'ts'),
            ('cognitive_signals', 'focus'),
            ('face_signals',      'ts'),
            ('face_signals',      'emotion'),
            ('face_signals',      'gaze_x'),
            ('face_signals',      'head_yaw'),
            ('heart_signals',     'ts'),
            ('heart_signals',     'heart_rate_bpm')
        ) AS t(tbl, col)
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = t.tbl
              AND column_name = t.col)
    LOOP
        RAISE EXCEPTION
            '%.% is gone, and student_sessions filters an activity read on '
            'it. PostgREST will reject that request, the endpoint swallows '
            'the error, and every session then reports activity unknown -- so '
            'a quiet session reads LIVE again. Update _ACTIVITY_SOURCES in '
            'main.py in the same change that drops the column.',
            spec.tbl, spec.col;
    END LOOP;
END $$;

-- ── fixtures ────────────────────────────────────────────────────────────────
-- A real FK chain (auth.users -> profiles -> sessions), so inserts reach the CHECKs.

CREATE TEMP TABLE _ids AS
SELECT gen_random_uuid() AS owner_id,
       gen_random_uuid() AS other_id,
       gen_random_uuid() AS sess_id;

INSERT INTO auth.users (id, email)
SELECT owner_id, 'owner@test.invalid' FROM _ids
UNION ALL
SELECT other_id, 'other@test.invalid' FROM _ids;

INSERT INTO public.profiles (id, email, role)
SELECT owner_id, 'owner@test.invalid', 'student' FROM _ids
UNION ALL
SELECT other_id, 'other@test.invalid', 'student' FROM _ids
ON CONFLICT (id) DO NOTHING;   -- handle_new_user may have created them already

INSERT INTO public.sessions (id, user_id)
SELECT sess_id, owner_id FROM _ids;

-- ── the CHECK constraints actually reject ───────────────────────────────────
-- A typo'd source must fail, not become a source no consent rule covers.

DO $$
DECLARE
    sess uuid;
    usr  uuid;
BEGIN
    SELECT sess_id, owner_id INTO sess, usr FROM _ids;

    BEGIN
        INSERT INTO public.heart_signals (session_id, user_id, source)
        VALUES (sess, usr, 'wrist_strap');
        RAISE EXCEPTION 'an unknown heart source was accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;

    BEGIN
        INSERT INTO public.heart_signals (session_id, user_id, source, stress_score)
        VALUES (sess, usr, 'muse_optics', 140);
        RAISE EXCEPTION 'a stress_score above 100 was accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;

    BEGIN
        INSERT INTO public.heart_signals (session_id, user_id, source, heart_rate_bpm)
        VALUES (sess, usr, 'muse_optics', 400);
        RAISE EXCEPTION 'an impossible heart rate was accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;

    BEGIN
        INSERT INTO public.heart_signals (session_id, user_id, source, stress_category)
        VALUES (sess, usr, 'muse_optics', 'panicking');
        RAISE EXCEPTION 'an unknown stress_category was accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
END $$;

-- ── the dedupe key added in 20260809120000 ──────────────────────────────────

DO $$
DECLARE
    sess uuid;
    usr  uuid;
    when_ts timestamptz := now();
BEGIN
    SELECT sess_id, owner_id INTO sess, usr FROM _ids;

    INSERT INTO public.heart_signals (session_id, user_id, source, ts)
    VALUES (sess, usr, 'muse_optics', when_ts);

    BEGIN
        INSERT INTO public.heart_signals (session_id, user_id, source, ts)
        VALUES (sess, usr, 'muse_optics', when_ts);
        RAISE EXCEPTION 'a duplicate (session, source, ts) was accepted';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;

    -- Two sources may legitimately report the same instant. A key without
    -- `source` would discard this as a duplicate of the row above.
    INSERT INTO public.heart_signals (session_id, user_id, source, ts)
    VALUES (sess, usr, 'muse_ppg', when_ts);
END $$;

-- ── the consent SELECT grant exists, asserted separately ────────────────────
-- The signal tables have none: see the face_signals/cognitive_signals block below.

DO $$
BEGIN
    IF NOT has_table_privilege('authenticated', 'public.signal_consent', 'SELECT') THEN
        RAISE EXCEPTION 'authenticated lacks SELECT on signal_consent';
    END IF;
END $$;

-- ── RLS: an unrelated authenticated user sees nothing ───────────────────────
-- Protects any ordinary-JWT access (PostgREST); main.py's service-role client bypasses it.

DO $$
DECLARE
    owner_id  uuid;
    other_id  uuid;
    sess      uuid;
    visible   int;
BEGIN
    SELECT i.owner_id, i.other_id, i.sess_id
      INTO owner_id, other_id, sess FROM _ids i;

    -- Explicit ts: now() is the transaction timestamp and would collide with the dedupe row above.
    INSERT INTO public.heart_signals (session_id, user_id, source, ts, heart_rate_bpm)
    VALUES (sess, owner_id, 'muse_optics', now() + interval '1 minute', 72);

    -- No client reads it, owner included: the backend reads it and applies consent per channel.
    SET LOCAL ROLE authenticated;
    PERFORM set_config('request.jwt.claims',
                       json_build_object('sub', owner_id, 'role', 'authenticated')::text,
                       true);
    BEGIN
        SELECT count(*) INTO visible FROM public.heart_signals;
        RAISE EXCEPTION 'an authenticated user read % heart rows', visible;
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;

    -- And cannot write one: no INSERT policy.
    BEGIN
        INSERT INTO public.heart_signals (session_id, user_id, source, ts)
        VALUES (sess, other_id, 'muse_optics', now() + interval '2 minutes');
        RAISE EXCEPTION 'an authenticated user inserted a heart row';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;

    RESET ROLE;
END $$;

-- ── the same, for the consent table that governs all of it ──────────────────
-- No write policy for anyone: the backend enforces consent transitions, so the table stays unwritable.

DO $$
DECLARE
    someone uuid;
BEGIN
    SELECT other_id INTO someone FROM _ids;

    SET LOCAL ROLE authenticated;
    PERFORM set_config('request.jwt.claims',
                       json_build_object('sub', someone, 'role', 'authenticated')::text,
                       true);
    BEGIN
        INSERT INTO public.signal_consent (user_id, camera_enabled)
        VALUES (someone, true);
        RAISE EXCEPTION 'a student granted themselves camera consent';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    RESET ROLE;
END $$;

-- ── the same, for face_signals and cognitive_signals ────────────────────────
-- No client grant on any of the three, and no teacher-read policy to reopen them if one returns:
-- RLS let any class teacher read every row whatever the student had declined.

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['cognitive_signals', 'face_signals', 'heart_signals'] LOOP
        IF has_table_privilege('authenticated', 'public.' || t, 'SELECT')
           OR has_table_privilege('anon', 'public.' || t, 'SELECT') THEN
            RAISE EXCEPTION 'a client role can SELECT % -- only the backend reads it', t;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'public' AND tablename = t AND policyname ILIKE '%teacher%') THEN
            RAISE EXCEPTION 'a teacher policy on % would reopen raw reads if SELECT is granted back', t;
        END IF;
    END LOOP;
    -- No sequence USAGE without the INSERT it existed for.
    IF has_sequence_privilege('authenticated', 'public.face_signals_id_seq', 'USAGE') THEN
        RAISE EXCEPTION
            'authenticated still holds USAGE on face_signals_id_seq with no '
            'INSERT left to justify it';
    END IF;
    IF has_sequence_privilege('authenticated', 'public.cognitive_signals_id_seq', 'USAGE') THEN
        RAISE EXCEPTION
            'authenticated still holds USAGE on cognitive_signals_id_seq with '
            'no INSERT left to justify it';
    END IF;
END $$;

DO $$
DECLARE
    owner_id  uuid;
    other_id  uuid;
    sess      uuid;
    visible   int;
BEGIN
    SELECT i.owner_id, i.other_id, i.sess_id
      INTO owner_id, other_id, sess FROM _ids i;

    INSERT INTO public.face_signals (session_id, user_id, emotion)
    VALUES (sess, owner_id, 'neutral');
    INSERT INTO public.cognitive_signals (session_id, user_id)
    VALUES (sess, owner_id);

    -- No client reads them, owner included, as in the heart_signals block.
    SET LOCAL ROLE authenticated;
    PERFORM set_config('request.jwt.claims',
                       json_build_object('sub', owner_id, 'role', 'authenticated')::text,
                       true);
    BEGIN
        SELECT count(*) INTO visible FROM public.face_signals;
        RAISE EXCEPTION 'an authenticated user read % face rows', visible;
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        SELECT count(*) INTO visible FROM public.cognitive_signals;
        RAISE EXCEPTION 'an authenticated user read % cognitive rows', visible;
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;

    -- And cannot write one, which would bypass consent.
    BEGIN
        INSERT INTO public.face_signals (session_id, user_id, emotion)
        VALUES (sess, owner_id, 'happy');
        RAISE EXCEPTION 'an authenticated user inserted a face row';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        INSERT INTO public.cognitive_signals (session_id, user_id)
        VALUES (sess, owner_id);
        RAISE EXCEPTION 'an authenticated user inserted a cognitive row';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;

    RESET ROLE;
END $$;

-- ── the end-of-year delete ──────────────────────────────────────────────────
-- A day with no rollup row must survive: its raw rows are the only copy.

DO $$
DECLARE
    uid uuid; sess uuid; result jsonb; survivors int;
BEGIN
    SELECT owner_id, sess_id INTO uid, sess FROM _ids;

    DELETE FROM public.retention_window;
    INSERT INTO public.retention_window (starts_on, ends_on, timezone)
    VALUES ('2025-09-01', '2026-06-30', 'America/Los_Angeles');

    -- Two expired days for this student; only the first is summarised.
    INSERT INTO public.cognitive_signals (session_id, user_id, ts, focus)
    VALUES (sess, uid, '2026-03-10T18:00:00Z', 0.5),
           (sess, uid, '2026-03-11T18:00:00Z', 0.5);
    INSERT INTO public.signal_daily_rollup
        (user_id, day, channel, avg_focus, sample_count, trusted_sample_count)
    VALUES (uid, DATE '2026-03-10', 'cognitive', 0.5, 1, 1);

    result := public.expire_signal_rows();

    IF (result->>'cutoff')::date <> DATE '2026-06-30' THEN
        RAISE EXCEPTION 'cutoff was %, expected the finished year''s ends_on',
            result->>'cutoff';
    END IF;

    SELECT count(*) INTO survivors
    FROM public.cognitive_signals
    WHERE user_id = uid AND (ts AT TIME ZONE 'America/Los_Angeles')::date
                            = DATE '2026-03-11';
    IF survivors = 0 THEN
        RAISE EXCEPTION
            'the delete job removed a day with no rollup row. That day had no '
            'summary, so its per-sample rows were the only copy -- this is the '
            'one failure mode in the retention design with no recovery.';
    END IF;

    SELECT count(*) INTO survivors
    FROM public.cognitive_signals
    WHERE user_id = uid AND (ts AT TIME ZONE 'America/Los_Angeles')::date
                            = DATE '2026-03-10';
    IF survivors <> 0 THEN
        RAISE EXCEPTION
            'a summarised expired day was not deleted -- the job is not '
            'expiring anything, so the refusal above passes for the wrong reason';
    END IF;
END $$;

-- An unconfigured window deletes nothing (fail closed, like the recording gate).
DO $$
DECLARE
    uid uuid; sess uuid; result jsonb; remaining int;
BEGIN
    SELECT owner_id, sess_id INTO uid, sess FROM _ids;
    DELETE FROM public.retention_window;
    INSERT INTO public.cognitive_signals (session_id, user_id, ts, focus)
    VALUES (sess, uid, '2020-01-01T12:00:00Z', 0.5);

    result := public.expire_signal_rows();

    IF result->>'cutoff' IS NOT NULL THEN
        RAISE EXCEPTION 'an unconfigured window produced a cutoff of %',
            result->>'cutoff';
    END IF;
    SELECT count(*) INTO remaining FROM public.cognitive_signals
    WHERE user_id = uid AND ts < '2021-01-01';
    IF remaining = 0 THEN
        RAISE EXCEPTION 'rows were deleted with no retention window configured';
    END IF;
END $$;

-- The same refusal on all three tables; `face_signals` -> 'emotion' is the mapping whose names differ.
DO $$
DECLARE
    uid uuid; sess uuid; tbl text; chan text; survivors int;
BEGIN
    SELECT owner_id, sess_id INTO uid, sess FROM _ids;
    DELETE FROM public.retention_window;
    INSERT INTO public.retention_window (starts_on, ends_on, timezone)
    VALUES ('2025-09-01', '2026-06-30', 'America/Los_Angeles');

    FOREACH tbl IN ARRAY ARRAY['cognitive_signals', 'face_signals', 'heart_signals']
    LOOP
        chan := CASE tbl WHEN 'cognitive_signals' THEN 'cognitive'
                         WHEN 'face_signals' THEN 'emotion' ELSE 'heart' END;
        EXECUTE format(
            'DELETE FROM public.%I WHERE user_id = $1', tbl) USING uid;
        DELETE FROM public.signal_daily_rollup WHERE user_id = uid;

        -- One summarised expired day, one not.
        IF tbl = 'heart_signals' THEN
            EXECUTE format($q$INSERT INTO public.%I (session_id, user_id, ts, source)
                              VALUES ($1, $2, '2026-03-10T18:00:00Z', 'muse_optics'),
                                     ($1, $2, '2026-03-11T18:00:00Z', 'muse_optics')$q$, tbl)
                USING sess, uid;
        ELSE
            EXECUTE format($q$INSERT INTO public.%I (session_id, user_id, ts)
                              VALUES ($1, $2, '2026-03-10T18:00:00Z'),
                                     ($1, $2, '2026-03-11T18:00:00Z')$q$, tbl)
                USING sess, uid;
        END IF;
        INSERT INTO public.signal_daily_rollup
            (user_id, day, channel, sample_count, trusted_sample_count)
        VALUES (uid, DATE '2026-03-10', chan, 1, 1);

        PERFORM public.expire_signal_rows();

        EXECUTE format($q$SELECT count(*) FROM public.%I
                          WHERE user_id = $1
                            AND (ts AT TIME ZONE 'America/Los_Angeles')::date
                                = DATE '2026-03-11'$q$, tbl)
            INTO survivors USING uid;
        IF survivors = 0 THEN
            RAISE EXCEPTION '% lost a day with no rollup row', tbl;
        END IF;

        EXECUTE format($q$SELECT count(*) FROM public.%I
                          WHERE user_id = $1
                            AND (ts AT TIME ZONE 'America/Los_Angeles')::date
                                = DATE '2026-03-10'$q$, tbl)
            INTO survivors USING uid;
        IF survivors <> 0 THEN
            RAISE EXCEPTION
                '% did not expire a summarised day -- its channel mapping (%) '
                'may not match the rollup rows, which would make the refusal '
                'check above pass for the wrong reason', tbl, chan;
        END IF;
    END LOOP;
END $$;

-- The batching loop: it iterates to completion, and the cap stops it and reports itself.
DO $$
DECLARE
    uid uuid; sess uuid; result jsonb; remaining int;
BEGIN
    SELECT owner_id, sess_id INTO uid, sess FROM _ids;
    DELETE FROM public.cognitive_signals WHERE user_id = uid;
    DELETE FROM public.signal_daily_rollup WHERE user_id = uid;
    DELETE FROM public.retention_window;
    INSERT INTO public.retention_window (starts_on, ends_on, timezone)
    VALUES ('2025-09-01', '2026-06-30', 'America/Los_Angeles');

    -- Distinct stamps, as `cog_session_ts_key` requires; seconds apart so all stay on one school day.
    INSERT INTO public.cognitive_signals (session_id, user_id, ts)
    SELECT sess, uid, '2026-03-10T18:00:00Z'::timestamptz + (g || ' s')::interval
      FROM generate_series(1, 5) g;
    INSERT INTO public.signal_daily_rollup
        (user_id, day, channel, sample_count, trusted_sample_count)
    VALUES (uid, DATE '2026-03-10', 'cognitive', 5, 5);

    -- One row per batch: five rows must take five passes, not one.
    result := public.expire_signal_rows(p_batch_size => 1);
    SELECT count(*) INTO remaining FROM public.cognitive_signals WHERE user_id = uid;
    IF remaining <> 0 THEN
        RAISE EXCEPTION
            'batching stopped early: % rows left with a batch size of 1, so the '
            'loop is running once rather than until the work is done', remaining;
    END IF;

    -- The cap stops it, visibly: without `hit_batch_cap` this reads as "nothing eligible".
    INSERT INTO public.cognitive_signals (session_id, user_id, ts)
    SELECT sess, uid, '2026-03-10T18:00:00Z'::timestamptz + (g || ' s')::interval
      FROM generate_series(1, 5) g;
    result := public.expire_signal_rows(p_batch_size => 1, p_max_batches => 2);
    SELECT count(*) INTO remaining FROM public.cognitive_signals WHERE user_id = uid;
    IF remaining <> 3 THEN
        RAISE EXCEPTION 'expected 3 rows left after 2 batches of 1, found %', remaining;
    END IF;
    IF (result->'hit_batch_cap'->>'cognitive_signals')::boolean IS NOT TRUE THEN
        RAISE EXCEPTION
            'the batch cap was hit and not reported, so "skipped = 0" reads as '
            '"everything eligible was handled" when it was not';
    END IF;
END $$;

-- Seven summarised rows and an unsummarised day, per (batch size, cap, rows left): the cap is
-- reported only while summarised rows remain, so not when the limit lands on the last one.
DO $$
DECLARE
    uid uuid; sess_a uuid := gen_random_uuid(); sess_b uuid := gen_random_uuid();
    result jsonb; n int; c int[];
BEGIN
    SELECT owner_id INTO uid FROM _ids;
    DELETE FROM public.signal_daily_rollup WHERE user_id = uid;
    DELETE FROM public.retention_window;
    INSERT INTO public.retention_window (starts_on, ends_on, timezone)
    VALUES ('2025-09-01', '2026-06-30', 'America/Los_Angeles');
    INSERT INTO public.sessions (id, user_id) VALUES (sess_a, uid), (sess_b, uid);
    -- No rollup row for 2026-03-11.
    INSERT INTO public.signal_daily_rollup
        (user_id, day, channel, sample_count, trusted_sample_count)
    VALUES (uid, DATE '2026-03-10', 'cognitive', 4, 4),
           (uid, DATE '2026-03-12', 'cognitive', 3, 3);

    -- 6 of 7 reached; exactly 7 of 7; a limit of 8 and of 10 over 7.
    FOREACH c SLICE 1 IN ARRAY ARRAY[[2, 3, 1], [1, 7, 0], [2, 4, 0], [2, 5, 0]]
    LOOP
        DELETE FROM public.cognitive_signals WHERE user_id = uid;
        -- The 2026-03-11 rows are inserted among the others, so the scan meets them mid-run.
        INSERT INTO public.cognitive_signals (session_id, user_id, ts) VALUES
            (sess_a, uid, '2026-03-10T18:00:00Z'), (sess_b, uid, '2026-03-10T18:00:00Z'),
            (sess_a, uid, '2026-03-10T18:00:01Z'),
            (sess_a, uid, '2026-03-11T18:00:00Z'), (sess_b, uid, '2026-03-11T18:00:00Z'),
            (sess_b, uid, '2026-03-10T18:00:01Z'),
            (sess_a, uid, '2026-03-12T18:00:00Z'), (sess_b, uid, '2026-03-12T18:00:00Z'),
            (sess_a, uid, '2026-03-12T18:00:01Z');

        result := public.expire_signal_rows(p_batch_size => c[1], p_max_batches => c[2]);

        SELECT count(*) INTO n FROM public.cognitive_signals
         WHERE user_id = uid AND ts <> '2026-03-11T18:00:00Z';
        IF n <> c[3] THEN
            RAISE EXCEPTION
                'batch % cap %: % summarised rows left, expected %: the cap deleted the wrong '
                'number of rows', c[1], c[2], n, c[3];
        END IF;
        SELECT count(*) INTO n FROM public.cognitive_signals
         WHERE user_id = uid AND ts = '2026-03-11T18:00:00Z';
        IF n <> 2 THEN
            RAISE EXCEPTION 'batch % cap %: a day with no rollup row lost rows: % of 2 left',
                c[1], c[2], n;
        END IF;
        IF (result->'hit_batch_cap'->>'cognitive_signals')::boolean IS DISTINCT FROM (c[3] > 0) THEN
            RAISE EXCEPTION 'batch % cap %: hit_batch_cap should be %, as % summarised rows remain: %',
                c[1], c[2], c[3] > 0, c[3], result;
        END IF;
    END LOOP;

    DELETE FROM public.signal_daily_rollup WHERE user_id = uid;
    DELETE FROM public.sessions WHERE id IN (sess_a, sess_b);
END $$;

-- The cutoff day ends at local midnight, on a 25-hour DST day too: the delete bounds on an
-- instant, so a fixed UTC offset would keep its last hour or take the next day's first.
DO $$
DECLARE
    uid uuid; sess uuid; last_second int; next_day int;
    saved public.retention_window; had_row boolean;
BEGIN
    SELECT owner_id, sess_id INTO uid, sess FROM _ids;
    SELECT * INTO saved FROM public.retention_window LIMIT 1;
    had_row := FOUND;
    DELETE FROM public.retention_window;
    -- Los Angeles leaves DST on 2025-11-02, so that local day runs 07:00Z to 08:00Z next day.
    INSERT INTO public.retention_window (starts_on, ends_on, timezone)
    VALUES ('2025-01-01', '2025-11-02', 'America/Los_Angeles');

    INSERT INTO public.cognitive_signals (session_id, user_id, ts, focus) VALUES
        (sess, uid, '2025-11-03T07:59:59Z', 0.5),
        (sess, uid, '2025-11-03T08:00:00Z', 0.5);
    INSERT INTO public.signal_daily_rollup
        (user_id, day, channel, sample_count, trusted_sample_count)
    VALUES (uid, DATE '2025-11-02', 'cognitive', 1, 1),
           (uid, DATE '2025-11-03', 'cognitive', 1, 1);

    PERFORM public.expire_signal_rows();

    SELECT count(*) INTO last_second FROM public.cognitive_signals
     WHERE user_id = uid AND ts = '2025-11-03T07:59:59Z';
    SELECT count(*) INTO next_day FROM public.cognitive_signals
     WHERE user_id = uid AND ts = '2025-11-03T08:00:00Z';
    IF last_second <> 0 THEN
        RAISE EXCEPTION
            'the last second of the cutoff day (23:59:59 local) survived: the delete '
            'bound is short of local midnight on a DST day';
    END IF;
    IF next_day <> 1 THEN
        RAISE EXCEPTION
            'the first second after the cutoff day (00:00 local) was deleted: the '
            'delete bound runs past local midnight';
    END IF;
    -- Leave the shared fixtures as found: later sections read them.
    DELETE FROM public.cognitive_signals
     WHERE user_id = uid AND ts IN ('2025-11-03T07:59:59Z', '2025-11-03T08:00:00Z');
    DELETE FROM public.signal_daily_rollup
     WHERE user_id = uid AND day IN (DATE '2025-11-02', DATE '2025-11-03');
    DELETE FROM public.retention_window;
    IF had_row THEN
        INSERT INTO public.retention_window SELECT saved.*;
    END IF;
END $$;

-- ── latest_signals_for_sessions: the newest row per channel, named fields only ──
DO $$
DECLARE
    uid uuid; sess uuid := gen_random_uuid(); empty_sess uuid := gen_random_uuid();
    newer_sess uuid := gen_random_uuid();
    n int; body jsonb; keys text[];
BEGIN
    -- Sessions of its own, so the shared fixture's rows neither help nor hinder.
    SELECT owner_id INTO uid FROM _ids;
    INSERT INTO public.sessions (id, user_id)
    VALUES (sess, uid), (empty_sess, uid), (newer_sess, uid);

    INSERT INTO public.cognitive_signals (session_id, user_id, ts, focus, stress, engagement, raw)
    VALUES (sess, uid, '2026-09-01T10:00:00Z', 0.1, 0.9, 0.1,
            '{"signal_quality": "good", "quality_basis": "contact", "bands": [1, 2]}'),
           (sess, uid, '2026-09-01T10:00:05Z', 0.2, 0.8, 0.2,
            '{"signal_quality": "poor", "quality_basis": "contact", "bands": [3, 4]}');
    INSERT INTO public.face_signals (session_id, user_id, ts, emotion)
    VALUES (sess, uid, '2026-09-01T10:00:01Z', 'neutral'),
           (sess, uid, '2026-09-01T10:00:06Z', 'happy');
    INSERT INTO public.heart_signals
        (session_id, user_id, source, ts, heart_rate_bpm, rmssd_ms, trusted)
    VALUES (sess, uid, 'muse_optics', '2026-09-01T10:00:02Z', 70, 40, true),
           (sess, uid, 'muse_optics', '2026-09-01T10:00:07Z', 72, 41, false);
    INSERT INTO public.session_answers (session_id, user_id, correct, answered_at)
    VALUES (sess, uid, true, '2026-09-01T10:00:03Z'),
           (sess, uid, false, '2026-09-01T10:00:08Z');

    -- A sibling whose every row is newer: each lookup must stay inside its own session.
    INSERT INTO public.cognitive_signals (session_id, user_id, ts, focus, stress, engagement, raw)
    VALUES (newer_sess, uid, '2026-09-01T11:00:00Z', 0.9, 0.1, 0.9,
            '{"signal_quality": "good", "quality_basis": "contact"}');
    INSERT INTO public.face_signals (session_id, user_id, ts, emotion)
    VALUES (newer_sess, uid, '2026-09-01T11:00:01Z', 'sad');
    -- Two sensors on one stamp, inserted apart so the later one has the higher id and wins.
    INSERT INTO public.heart_signals
        (session_id, user_id, source, ts, heart_rate_bpm, rmssd_ms, trusted)
    VALUES (newer_sess, uid, 'rppg', '2026-09-01T11:00:02Z', 90, 30, true);
    INSERT INTO public.heart_signals
        (session_id, user_id, source, ts, heart_rate_bpm, rmssd_ms, trusted)
    VALUES (newer_sess, uid, 'muse_optics', '2026-09-01T11:00:02Z', 95, 35, true);
    INSERT INTO public.session_answers (session_id, user_id, correct, answered_at)
    VALUES (newer_sess, uid, true, '2026-09-01T11:00:03Z');

    -- The same id twice and a session with no rows: one row per channel, once.
    SELECT count(*) INTO n
      FROM public.latest_signals_for_sessions(ARRAY[sess, sess, empty_sess]);
    IF n <> 4 THEN
        RAISE EXCEPTION 'expected one row per channel for the one session with data, got %', n;
    END IF;

    SELECT payload INTO body FROM public.latest_signals_for_sessions(ARRAY[sess, newer_sess])
     WHERE session_id = sess AND channel = 'cognitive';
    SELECT array_agg(k ORDER BY k) INTO keys FROM jsonb_object_keys(body) k;
    -- IS DISTINCT FROM throughout: a missing row is a NULL body, and `<>` on NULL never raises.
    IF (body->>'focus')::float8 IS DISTINCT FROM 0.2
       OR body->'raw'->>'signal_quality' IS DISTINCT FROM 'poor'
       OR keys IS DISTINCT FROM ARRAY['engagement', 'focus', 'raw', 'stress', 'ts'] THEN
        RAISE EXCEPTION 'cognitive is not the newest row, or not the named fields: %', body;
    END IF;
    SELECT array_agg(k ORDER BY k) INTO keys FROM jsonb_object_keys(body->'raw') k;
    IF keys IS DISTINCT FROM ARRAY['quality_basis', 'signal_quality'] THEN
        RAISE EXCEPTION 'raw carries more than the live view reads: %', body->'raw';
    END IF;

    SELECT payload INTO body FROM public.latest_signals_for_sessions(ARRAY[sess, newer_sess])
     WHERE session_id = sess AND channel = 'face';
    SELECT array_agg(k ORDER BY k) INTO keys FROM jsonb_object_keys(body) k;
    IF body->>'emotion' IS DISTINCT FROM 'happy'
       OR keys IS DISTINCT FROM ARRAY['emotion', 'ts'] THEN
        RAISE EXCEPTION 'face is not the newest row, or not the named fields: %', body;
    END IF;

    -- Newest, trusted or not: the card reads `trusted` itself.
    SELECT payload INTO body FROM public.latest_signals_for_sessions(ARRAY[sess, newer_sess])
     WHERE session_id = sess AND channel = 'heart';
    SELECT array_agg(k ORDER BY k) INTO keys FROM jsonb_object_keys(body) k;
    IF (body->>'heart_rate_bpm')::float8 IS DISTINCT FROM 72
       OR (body->>'trusted')::boolean IS NOT FALSE
       OR keys IS DISTINCT FROM ARRAY['heart_rate_bpm', 'rmssd_ms', 'source', 'trusted', 'ts'] THEN
        RAISE EXCEPTION 'heart is not the newest row, or not the named fields: %', body;
    END IF;

    SELECT payload INTO body FROM public.latest_signals_for_sessions(ARRAY[sess, newer_sess])
     WHERE session_id = sess AND channel = 'answer';
    IF (body->>'answered_at')::timestamptz IS DISTINCT FROM '2026-09-01T10:00:08Z'
       OR (SELECT count(*) FROM jsonb_object_keys(body)) <> 1 THEN
        RAISE EXCEPTION 'answer is not the newest answered_at alone: %', body;
    END IF;

    SELECT jsonb_object_agg(channel, payload) INTO body
      FROM public.latest_signals_for_sessions(ARRAY[sess, newer_sess])
     WHERE session_id = newer_sess;
    IF (body->'cognitive'->>'focus')::float8 IS DISTINCT FROM 0.9
       OR body->'face'->>'emotion' IS DISTINCT FROM 'sad'
       OR (body->'answer'->>'answered_at')::timestamptz IS DISTINCT FROM '2026-09-01T11:00:03Z' THEN
        RAISE EXCEPTION 'the sibling session did not get its own newest rows: %', body;
    END IF;
    IF body->'heart'->>'source' IS DISTINCT FROM 'muse_optics'
       OR (body->'heart'->>'heart_rate_bpm')::float8 IS DISTINCT FROM 95 THEN
        RAISE EXCEPTION 'two heart rows on one ts: expected the higher id (muse_optics), got %',
            body->'heart';
    END IF;

    -- Cascades the four tables' rows, so later sections see the fixtures as before.
    DELETE FROM public.sessions WHERE id IN (sess, empty_sess, newer_sess);
END $$;

-- ── the archived charts (Phase 8) ───────────────────────────────────────────
-- They outlive the rows they draw on, and both guards are database state a fake client cannot see.

DO $$
DECLARE
    owner_id       uuid;
    other_id       uuid;
    is_public      boolean;
    visible        int;
    policies       int;
    -- Not `owner_id`: that would be ambiguous with the storage.objects column in plpgsql.
    fixture_owner  text;
BEGIN
    SELECT i.owner_id, i.other_id INTO owner_id, other_id FROM _ids i;
    fixture_owner := owner_id::text;

    -- 1. The bucket is private: a public object URL bypasses RLS and cannot be un-shared.
    SELECT b.public INTO is_public
      FROM storage.buckets b WHERE b.id = 'session-charts';
    IF is_public IS NULL THEN
        RAISE EXCEPTION
            'the session-charts bucket does not exist, so every archive upload '
            'fails -- silently, in the out-of-band path where nothing is waiting';
    END IF;
    IF is_public THEN
        RAISE EXCEPTION 'the session-charts bucket is public';
    END IF;

    -- 2. No policy on storage.objects, so only service_role (the backend) reads or writes.
    SELECT count(*) INTO policies
      FROM pg_policy WHERE polrelid = 'storage.objects'::regclass;
    IF policies <> 0 THEN
        RAISE EXCEPTION
            'storage.objects has % policy/policies -- charts are now reachable '
            'without going through the signed-URL endpoint', policies;
    END IF;

    INSERT INTO storage.objects (bucket_id, name, owner_id)
    VALUES ('session-charts', owner_id || '/sess/heart_rate.svg', fixture_owner);

    -- Negative control: the fixture exists, so "nobody sees it" below means something.
    -- Scoped to this owner_id, since a developer stack already holds real (owner-less) charts.
    SELECT count(*) INTO visible
      FROM storage.objects o
     WHERE o.bucket_id = 'session-charts' AND o.owner_id = fixture_owner;
    IF visible <> 1 THEN
        RAISE EXCEPTION 'the fixture object was not stored; the checks below '
                        'would pass for the wrong reason';
    END IF;

    SET LOCAL ROLE authenticated;

    -- 3. Not even the student it is about: access is decided in the backend, via a signed URL.
    PERFORM set_config('request.jwt.claims',
                       json_build_object('sub', owner_id, 'role', 'authenticated')::text,
                       true);
    SELECT count(*) INTO visible
      FROM storage.objects WHERE bucket_id = 'session-charts';
    IF visible <> 0 THEN
        RAISE EXCEPTION
            'a student read % chart object(s) straight from storage, bypassing '
            'the signed-URL endpoint', visible;
    END IF;

    PERFORM set_config('request.jwt.claims',
                       json_build_object('sub', other_id, 'role', 'authenticated')::text,
                       true);
    SELECT count(*) INTO visible
      FROM storage.objects WHERE bucket_id = 'session-charts';
    IF visible <> 0 THEN
        RAISE EXCEPTION
            'an unrelated authenticated user read % chart object(s)', visible;
    END IF;

    -- And cannot write one, or it would be served as that student's chart.
    BEGIN
        INSERT INTO storage.objects (bucket_id, name)
        VALUES ('session-charts', other_id || '/sess/emotion_pie.svg');
        RAISE EXCEPTION 'an authenticated user wrote a chart object';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;

    RESET ROLE;
END $$;

-- `sessions.chart_paths` has no default: column-NULL means the archive never ran,
-- a null key means it ran and that channel had nothing.
DO $$
DECLARE
    has_default boolean;
BEGIN
    SELECT a.atthasdef INTO has_default
      FROM pg_attribute a
     WHERE a.attrelid = 'public.sessions'::regclass
       AND a.attname = 'chart_paths' AND NOT a.attisdropped;
    IF has_default IS NULL THEN
        RAISE EXCEPTION 'sessions.chart_paths is missing';
    END IF;
    IF has_default THEN
        RAISE EXCEPTION
            'sessions.chart_paths has a default, so a session that was never '
            'archived is indistinguishable from one archived with nothing to draw';
    END IF;
END $$;

-- ── `sessions` is read-only to clients ──────────────────────────────────────
-- Its `FOR ALL` own policy is safe only while the grant is SELECT: RLS narrows rows, not commands.

DO $$
DECLARE
    owner_id uuid;
    sess     uuid;
BEGIN
    SELECT i.owner_id, i.sess_id INTO owner_id, sess FROM _ids i;

    IF has_table_privilege('anon', 'public.sessions', 'SELECT') THEN
        RAISE EXCEPTION 'anon holds SELECT on sessions';
    END IF;

    -- Narrowed, not removed, so "cannot write" is not passing for lack of any access.
    IF NOT has_table_privilege('authenticated', 'public.sessions', 'SELECT') THEN
        RAISE EXCEPTION
            'authenticated lost SELECT on sessions, so the write checks below '
            'prove nothing about the grant being the narrow one intended';
    END IF;

    IF has_table_privilege('authenticated', 'public.sessions', 'UPDATE')
       OR has_table_privilege('authenticated', 'public.sessions', 'INSERT')
       OR has_table_privilege('authenticated', 'public.sessions', 'DELETE') THEN
        RAISE EXCEPTION
            'authenticated can write sessions -- a student can rewrite their '
            'own chart_paths, timestamps, or cascade-delete their signal rows';
    END IF;

    -- In practice too: the FOR ALL policy permits this, so only the privilege can refuse it.
    SET LOCAL ROLE authenticated;
    PERFORM set_config('request.jwt.claims',
                       json_build_object('sub', owner_id, 'role', 'authenticated')::text,
                       true);
    BEGIN
        UPDATE public.sessions
           SET chart_paths = '{"cognitive_timeline": "someone-else/x.svg"}'::jsonb
         WHERE id = sess;
        RAISE EXCEPTION 'a student rewrote chart_paths on their own session';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    RESET ROLE;
END $$;

-- ── erasure on request (#75) ────────────────────────────────────────────────
-- After the blocks above, since it deletes their fixture rows. The key property: erasing
-- one heart source leaves the other standing and its day average recomputed.

DO $$
DECLARE
    owner_id uuid;
    other_id uuid;
    sess     uuid;
    result   jsonb;
    n        int;
    avg_bpm  numeric;
BEGIN
    SELECT i.owner_id, i.other_id, i.sess_id INTO owner_id, other_id, sess FROM _ids i;

    DELETE FROM heart_signals WHERE user_id = owner_id;
    DELETE FROM face_signals WHERE user_id = owner_id;
    DELETE FROM cognitive_signals WHERE user_id = owner_id;
    DELETE FROM signal_daily_rollup WHERE user_id = owner_id;

    -- Both heart sources, far apart in value so a wrong-set average is unmistakable.
    INSERT INTO heart_signals (session_id, user_id, source, ts, heart_rate_bpm, trusted)
    SELECT sess, owner_id, 'muse_optics',
           '2026-03-10T18:00:00Z'::timestamptz + (g || ' s')::interval, 70, true
      FROM generate_series(1, 5) g;
    INSERT INTO heart_signals (session_id, user_id, source, ts, heart_rate_bpm, trusted)
    SELECT sess, owner_id, 'rppg',
           '2026-03-10T18:10:00Z'::timestamptz + (g || ' s')::interval, 120, true
      FROM generate_series(1, 5) g;
    INSERT INTO face_signals (session_id, user_id, ts, emotion, emotion_trusted)
    SELECT sess, owner_id,
           '2026-03-10T18:00:00Z'::timestamptz + (g || ' s')::interval, 'happy', true
      FROM generate_series(1, 4) g;
    INSERT INTO cognitive_signals (session_id, user_id, ts, focus)
    SELECT sess, owner_id,
           '2026-03-10T18:00:00Z'::timestamptz + (g || ' s')::interval, 0.5
      FROM generate_series(1, 3) g;

    PERFORM public.rollup_signal_day(owner_id, DATE '2026-03-10', 'UTC');
    SELECT avg_heart_rate_bpm INTO avg_bpm
      FROM signal_daily_rollup WHERE user_id = owner_id AND channel = 'heart';
    IF avg_bpm IS DISTINCT FROM 95 THEN
        RAISE EXCEPTION
            'fixture is wrong: heart average should be 95 over both sources, '
            'got % -- the survivor check below would prove nothing', avg_bpm;
    END IF;

    result := public.erase_signals(owner_id, 'camera', other_id, 'UTC');

    SELECT count(*) INTO n FROM face_signals WHERE user_id = owner_id;
    IF n <> 0 THEN RAISE EXCEPTION '% face rows survived a camera erasure', n; END IF;

    SELECT count(*) INTO n
      FROM heart_signals WHERE user_id = owner_id AND source = 'rppg';
    IF n <> 0 THEN RAISE EXCEPTION '% rppg heart rows survived', n; END IF;

    -- Erasing the camera must not take headband rows (delete keyed on source, not table).
    SELECT count(*) INTO n
      FROM heart_signals WHERE user_id = owner_id AND source = 'muse_optics';
    IF n <> 5 THEN
        RAISE EXCEPTION
            'erasing the camera took % of 5 headband heart rows with it', 5 - n;
    END IF;

    SELECT count(*) INTO n FROM cognitive_signals WHERE user_id = owner_id;
    IF n <> 3 THEN RAISE EXCEPTION 'erasing the camera touched EEG rows'; END IF;

    -- Derived data goes too, or the rows are gone and the data is not.
    SELECT count(*) INTO n FROM signal_daily_rollup
     WHERE user_id = owner_id AND channel = 'emotion';
    IF n <> 0 THEN RAISE EXCEPTION 'the emotion rollup survived the erasure'; END IF;

    -- The survivor's average is recomputed; 95 would mean erased readings are still averaged in.
    SELECT avg_heart_rate_bpm INTO avg_bpm
      FROM signal_daily_rollup WHERE user_id = owner_id AND channel = 'heart';
    IF avg_bpm IS DISTINCT FROM 70 THEN
        RAISE EXCEPTION
            'the heart rollup reads % after erasing the camera, expected 70 '
            '(the headband rows alone) -- erased readings are still averaged in',
            avg_bpm;
    END IF;

    SELECT count(*) INTO n FROM signal_daily_rollup
     WHERE user_id = owner_id AND channel = 'cognitive';
    IF n <> 1 THEN RAISE EXCEPTION 'the cognitive rollup was collateral'; END IF;

    -- The tombstone, so an erased term differs from a sensor never worn.
    SELECT count(*) INTO n FROM signal_erasure
     WHERE user_id = owner_id AND channel = 'camera' AND erased_by = other_id;
    IF n <> 1 THEN RAISE EXCEPTION 'no tombstone recorded for the erasure'; END IF;

    IF (result->>'face_signals')::int <> 4 THEN
        RAISE EXCEPTION 'reported % face rows deleted, expected 4',
            result->>'face_signals';
    END IF;

    -- Erasing the other source empties the channel, so its rollup row must go.
    PERFORM public.erase_signals(owner_id, 'headband_optical', other_id, 'UTC');
    SELECT count(*) INTO n FROM signal_daily_rollup
     WHERE user_id = owner_id AND channel = 'heart';
    IF n <> 0 THEN
        RAISE EXCEPTION 'the heart rollup outlived every row it summarised';
    END IF;
END $$;

-- erase_signals takes the subject as a parameter, so an application-role grant deletes anyone's history.
-- check_function_grants.py reads text; this checks the real instance.
DO $$
BEGIN
    IF has_function_privilege('authenticated',
            'public.erase_signals(uuid, text, uuid, text)', 'EXECUTE')
       OR has_function_privilege('anon',
            'public.erase_signals(uuid, text, uuid, text)', 'EXECUTE') THEN
        RAISE EXCEPTION
            'erase_signals is executable by an application role -- any logged-in '
            'user can delete any student''s stored signals';
    END IF;

    -- The tombstone is readable and not writable, so an erasure cannot be hidden.
    IF NOT has_table_privilege('authenticated', 'public.signal_erasure', 'SELECT') THEN
        RAISE EXCEPTION 'authenticated cannot read signal_erasure';
    END IF;
    IF has_table_privilege('authenticated', 'public.signal_erasure', 'DELETE')
       OR has_table_privilege('authenticated', 'public.signal_erasure', 'UPDATE')
       OR has_table_privilege('authenticated', 'public.signal_erasure', 'INSERT') THEN
        RAISE EXCEPTION 'authenticated can write signal_erasure';
    END IF;
    IF has_table_privilege('anon', 'public.signal_erasure', 'SELECT') THEN
        RAISE EXCEPTION 'anon can read signal_erasure';
    END IF;
END $$;

-- ── the emotion rollup counts emotion samples, not face rows ────────────────
-- A face row is written when either emotion or gaze succeeds, so `count(*)` is not emotion samples.
DO $$
DECLARE
    owner_id uuid;
    sess     uuid;
    n        int;
BEGIN
    SELECT i.owner_id, i.sess_id INTO owner_id, sess FROM _ids i;

    DELETE FROM face_signals WHERE user_id = owner_id;
    DELETE FROM signal_daily_rollup WHERE user_id = owner_id;

    -- Three emotion rows, two gaze-only (FER+ refused the face: ordinary, not an error).
    INSERT INTO face_signals (session_id, user_id, ts, emotion, emotion_trusted)
    SELECT sess, owner_id,
           '2026-03-11T18:00:00Z'::timestamptz + (g || ' s')::interval,
           'happy', true
      FROM generate_series(1, 3) g;
    INSERT INTO face_signals (session_id, user_id, ts, emotion, emotion_trusted,
                              gaze_x, gaze_y)
    SELECT sess, owner_id,
           '2026-03-11T18:01:00Z'::timestamptz + (g || ' s')::interval,
           NULL, false, 0.42, -0.03
      FROM generate_series(1, 2) g;

    PERFORM public.rollup_signal_day(owner_id, DATE '2026-03-11', 'UTC');

    SELECT sample_count INTO n FROM signal_daily_rollup
     WHERE user_id = owner_id AND channel = 'emotion';
    IF n <> 3 THEN
        RAISE EXCEPTION
            'emotion sample_count is % over 3 emotion rows and 2 gaze-only '
            'rows, expected 3 -- gaze inflates the figure the weekly report '
            'presents as how much is behind the emotion numbers, in the copy '
            'that outlives expire_signal_rows', n;
    END IF;

    -- A gaze-only day still gets a rollup row, or its raw rows could never expire.
    DELETE FROM face_signals WHERE user_id = owner_id AND emotion IS NOT NULL;
    DELETE FROM signal_daily_rollup WHERE user_id = owner_id;
    PERFORM public.rollup_signal_day(owner_id, DATE '2026-03-11', 'UTC');

    SELECT count(*) INTO n FROM signal_daily_rollup
     WHERE user_id = owner_id AND channel = 'emotion';
    IF n <> 1 THEN
        RAISE EXCEPTION
            'a gaze-only day produced no emotion rollup row, so its raw rows '
            'can never expire';
    END IF;

    SELECT sample_count INTO n FROM signal_daily_rollup
     WHERE user_id = owner_id AND channel = 'emotion';
    IF n <> 0 THEN
        RAISE EXCEPTION 'a gaze-only day reports % emotion samples', n;
    END IF;
END $$;

-- ── record_topic_attempt counts, and counts atomically ──────────────────────
-- The only place the increment runs; the backend suite can check only the call's arguments.

DO $$
DECLARE
    usr   uuid;
    topic integer;
    q1    uuid := gen_random_uuid();
    q2    uuid := gen_random_uuid();
    got   text;
    n     integer;
BEGIN
    SELECT owner_id INTO usr FROM _ids;

    INSERT INTO public.math_topics (topic_name)
    VALUES ('assert-rls-topic')
    RETURNING id INTO topic;

    INSERT INTO public.questions (id, subject, question_text)
    VALUES (q1, 'assert-rls-topic', 'two plus two'),
           (q2, 'assert-rls-no-such-subject', 'unattributable');

    -- First attempt creates the row and returns the topic *name* for the page.
    got := public.record_topic_attempt(usr, q1, true);
    IF got IS DISTINCT FROM 'assert-rls-topic' THEN
        RAISE EXCEPTION 'the topic was resolved as % rather than assert-rls-topic', got;
    END IF;

    -- Three more, exercising the update branch.
    PERFORM public.record_topic_attempt(usr, q1, false);
    PERFORM public.record_topic_attempt(usr, q1, true);
    PERFORM public.record_topic_attempt(usr, q1, false);

    SELECT attempted_questions INTO n FROM public.user_math_performance
     WHERE user_id = usr AND topic_id = topic;
    IF n <> 4 THEN
        RAISE EXCEPTION 'four attempts recorded % -- the increment reads a '
                        'value the caller supplied rather than the stored one, '
                        'which is the lost update this replaced', n;
    END IF;

    SELECT correct_questions INTO n FROM public.user_math_performance
     WHERE user_id = usr AND topic_id = topic;
    IF n <> 2 THEN
        RAISE EXCEPTION 'two correct answers of four recorded %', n;
    END IF;

    -- One row: the ON CONFLICT target must match the (user_id, topic_id) constraint.
    SELECT count(*) INTO n FROM public.user_math_performance
     WHERE user_id = usr AND topic_id = topic;
    IF n <> 1 THEN
        RAISE EXCEPTION '% rows for one student and one topic', n;
    END IF;

    -- A subject with no `math_topics` row records nothing and invents no topic.
    got := public.record_topic_attempt(usr, q2, true);
    IF got IS NOT NULL THEN
        RAISE EXCEPTION 'an unattributable question resolved to topic %', got;
    END IF;

    -- Same for a question that does not exist at all.
    got := public.record_topic_attempt(usr, gen_random_uuid(), true);
    IF got IS NOT NULL THEN
        RAISE EXCEPTION 'an unknown question resolved to topic %', got;
    END IF;

    PERFORM 1 FROM public.math_topics WHERE id = topic;   -- keep `topic` used

    SELECT count(*) INTO n FROM public.user_math_performance WHERE user_id = usr;
    IF n <> 1 THEN
        RAISE EXCEPTION 'an unattributable answer created % performance rows', n - 1;
    END IF;
END $$;

-- ── a stack built from migrations alone can attribute every original topic ──
-- Seeded by 20261008000000, not by seed.sql, which CI never runs.

DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(name, ', ') INTO missing
      FROM unnest(ARRAY['geometry', 'algebra', 'expressions', 'ordering', 'rationals',
                        'mean', 'median', 'mode', 'probability', 'angle_relationships']) AS name
     WHERE NOT EXISTS (SELECT 1 FROM public.math_topics t WHERE t.topic_name = name);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'math_topics has no row for %, so their answers attribute to nothing', missing;
    END IF;
END $$;

-- ── the batch summary agrees with the body it delegates to ──────────────────
-- `student_signal_summary_many` must stay a fan-out over `student_signal_summary`, and the
-- channel flags must gate the read. Its own student, so counts do not depend on blocks above.

DO $$
DECLARE
    usr  uuid := gen_random_uuid();
    sess uuid := gen_random_uuid();
    one  record;
    many record;
BEGIN
    INSERT INTO auth.users (id, email) VALUES (usr, 'summary@test.invalid');
    INSERT INTO public.profiles (id, email, role)
    VALUES (usr, 'summary@test.invalid', 'student') ON CONFLICT (id) DO NOTHING;
    INSERT INTO public.sessions (id, user_id) VALUES (sess, usr);

    INSERT INTO public.cognitive_signals (session_id, user_id, ts, focus, stress, engagement)
    SELECT sess, usr, now() - (g || ' min')::interval, 0.6 + g * 0.01, 0.3, 0.5
      FROM generate_series(1, 4) g;
    INSERT INTO public.face_signals (session_id, user_id, ts, emotion, emotion_trusted)
    SELECT sess, usr, now() - (g || ' min')::interval, 'happy', true
      FROM generate_series(1, 3) g;
    INSERT INTO public.heart_signals (session_id, user_id, ts, source, heart_rate_bpm,
                                      rmssd_ms, trusted)
    SELECT sess, usr, now() - (g || ' min')::interval, 'muse_optics', 70 + g, 40, true
      FROM generate_series(1, 2) g;

    SELECT * INTO one  FROM public.student_signal_summary(usr, 7, true, true, 'UTC');
    SELECT * INTO many FROM public.student_signal_summary_many(ARRAY[usr], 7, true, true, 'UTC');

    IF many.student_id IS DISTINCT FROM usr THEN
        RAISE EXCEPTION 'the fan-out lost the student id: %', many.student_id;
    END IF;

    IF (one.focus, one.stress, one.engagement, one.face_attention,
        one.heart_rate_bpm, one.rmssd_ms, one.sessions,
        one.cognitive_samples, one.face_samples, one.heart_samples)
       IS DISTINCT FROM
       (many.focus, many.stress, many.engagement, many.face_attention,
        many.heart_rate_bpm, many.rmssd_ms, many.sessions,
        many.cognitive_samples, many.face_samples, many.heart_samples) THEN
        RAISE EXCEPTION 'the batch summary disagrees with the single-student '
                        'body it delegates to: one=% many=%', one, many;
    END IF;

    -- Non-vacuous: if both returned all-nulls the comparison above would pass.
    IF one.cognitive_samples <> 4 OR one.face_samples <> 3 OR one.heart_samples <> 2 THEN
        RAISE EXCEPTION 'the fixture rows did not reach the summary: %', one;
    END IF;

    SELECT * INTO many
      FROM public.student_signal_summary_many(ARRAY[usr], 7, false, false, 'UTC');
    IF many.face_samples <> 0 OR many.heart_samples <> 0
       OR many.heart_rate_bpm IS NOT NULL OR many.face_attention IS NOT NULL THEN
        RAISE EXCEPTION 'an excluded channel came back through the fan-out: %', many;
    END IF;
    -- The cognitive channel has no opt-out on the aggregate (hence `eeg_enabled`, not `eeg_included`).
    IF many.cognitive_samples <> 4 THEN
        RAISE EXCEPTION 'the cognitive channel was gated by a flag that does '
                        'not apply to it: %', many;
    END IF;
END $$;

-- ── the summary reads settled days from the rollup, and trusted emotion only ────────────
-- A past day comes from its rollup row even once its raw rows are gone; a day a still-open
-- session reaches comes from raw rows, since its rollup can predate some of them.

DO $$
DECLARE
    usr   uuid := gen_random_uuid();
    done_ uuid := gen_random_uuid();  -- closed, three days ago
    open_ uuid := gen_random_uuid();  -- started two days ago, never closed
    t0    timestamptz := date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC';
    s     record;
BEGIN
    INSERT INTO auth.users (id, email) VALUES (usr, 'summary-rollup@test.invalid');
    INSERT INTO public.profiles (id, email, role)
    VALUES (usr, 'summary-rollup@test.invalid', 'student') ON CONFLICT (id) DO NOTHING;
    INSERT INTO public.sessions (id, user_id, started_at, ended_at) VALUES
        (done_, usr, t0 - interval '3 days' + interval '9 hours', t0 - interval '3 days' + interval '10 hours'),
        (open_, usr, t0 - interval '2 days' + interval '9 hours', NULL);

    -- Three days ago, rolled up and then expired: focus 0.4/0.6/0.4/0.6 with stress 0.2 on two
    -- rows; one trusted 'happy' beside two untrusted 'sad'; trusted 60 and 80 bpm beside an untrusted 150.
    INSERT INTO public.cognitive_signals (session_id, user_id, ts, focus, stress, engagement)
    SELECT done_, usr, t0 - interval '3 days' + interval '9 hours' + g * interval '1 min',
           CASE WHEN g % 2 = 1 THEN 0.4 ELSE 0.6 END, CASE WHEN g <= 2 THEN 0.2 END,
           CASE WHEN g % 2 = 1 THEN 0.4 ELSE 0.6 END
      FROM generate_series(1, 4) g;
    INSERT INTO public.face_signals (session_id, user_id, ts, emotion, emotion_trusted)
    SELECT done_, usr, t0 - interval '3 days' + interval '9 hours' + g * interval '1 min',
           CASE WHEN g = 1 THEN 'happy' ELSE 'sad' END, g = 1
      FROM generate_series(1, 3) g;
    INSERT INTO public.heart_signals (session_id, user_id, ts, source, heart_rate_bpm, rmssd_ms, trusted)
    VALUES (done_, usr, t0 - interval '3 days' + interval '9 hours 1 min', 'muse_optics', 60, 40, true),
           (done_, usr, t0 - interval '3 days' + interval '9 hours 2 min', 'muse_optics', 80, 40, true),
           (done_, usr, t0 - interval '3 days' + interval '9 hours 3 min', 'muse_optics', 150, 40, false);
    PERFORM public.rollup_signal_day(usr, (now() AT TIME ZONE 'UTC')::date - 3, 'UTC');
    DELETE FROM public.cognitive_signals WHERE session_id = done_;
    DELETE FROM public.face_signals WHERE session_id = done_;
    DELETE FROM public.heart_signals WHERE session_id = done_;

    -- Two days ago, inside the open session: rolled with one row, then a second lands, so the
    -- rollup row is stale and only the raw rows hold the day.
    INSERT INTO public.cognitive_signals (session_id, user_id, ts, focus, stress, engagement)
    VALUES (open_, usr, t0 - interval '2 days' + interval '9 hours 1 min', 0.2, 0.6, 0.2);
    PERFORM public.rollup_signal_day(usr, (now() AT TIME ZONE 'UTC')::date - 2, 'UTC');
    INSERT INTO public.cognitive_signals (session_id, user_id, ts, focus, stress, engagement)
    VALUES (open_, usr, t0 - interval '2 days' + interval '9 hours 2 min', 0.8, 0.6, 0.8);
    INSERT INTO public.face_signals (session_id, user_id, ts, emotion, emotion_trusted)
    VALUES (open_, usr, t0 - interval '2 days' + interval '9 hours 1 min', 'angry', true);

    -- Today, raw: focus 0.9 without stress; a trusted 'happy' beside three untrusted 'sad';
    -- a trusted 90 bpm beside an untrusted 200.
    INSERT INTO public.cognitive_signals (session_id, user_id, ts, focus, stress, engagement)
    VALUES (open_, usr, t0 + interval '1 min', 0.9, NULL, 0.9);
    INSERT INTO public.face_signals (session_id, user_id, ts, emotion, emotion_trusted)
    SELECT open_, usr, t0 + g * interval '1 min', CASE WHEN g = 1 THEN 'happy' ELSE 'sad' END, g = 1
      FROM generate_series(1, 4) g;
    INSERT INTO public.heart_signals (session_id, user_id, ts, source, heart_rate_bpm, rmssd_ms, trusted)
    VALUES (open_, usr, t0 + interval '1 min', 'muse_optics', 90, 50, true),
           (open_, usr, t0 + interval '2 min', 'muse_optics', 200, 50, false);

    SELECT * INTO s FROM public.student_signal_summary(usr, 7, true, true, 'UTC');

    -- Every focus reading once: 2.0 rolled, 1.0 raw two days ago, 0.9 today, over 7.
    IF s.cognitive_samples <> 7 OR abs(s.focus - 3.9 / 7) > 1e-9 THEN
        RAISE EXCEPTION 'the summary did not combine the rolled day, the open session''s day '
                        'and today: %', s;
    END IF;
    -- Stress on its own count (two at 0.2 rolled, two at 0.6 raw), not on focus's four.
    IF abs(s.stress - 0.4) > 1e-9 THEN
        RAISE EXCEPTION 'stress was not weighted on its own sample count: %', s.stress;
    END IF;
    -- The label from trusted readings only ('happy' rolled and today over 'angry'; every 'sad' is
    -- untrusted); the count is every labelled reading: 3 rolled, 1 two days ago, 4 today.
    IF s.face_samples <> 8 OR s.dominant_emotion IS DISTINCT FROM 'happy' THEN
        RAISE EXCEPTION 'emotion counted the wrong readings: % %', s.face_samples, s.dominant_emotion;
    END IF;
    -- Trusted heart only: 60 and 80 rolled, 90 today.
    IF s.heart_samples <> 3 OR abs(s.heart_rate_bpm - 230.0 / 3) > 1e-9 THEN
        RAISE EXCEPTION 'heart did not combine the rolled day with today''s trusted reading: %', s;
    END IF;

    -- A declined channel's rollup rows are not read either.
    SELECT * INTO s FROM public.student_signal_summary(usr, 7, false, false, 'UTC');
    IF s.face_samples <> 0 OR s.heart_samples <> 0 OR s.dominant_emotion IS NOT NULL
       OR s.heart_rate_bpm IS NOT NULL THEN
        RAISE EXCEPTION 'a declined channel came back from the rollup: %', s;
    END IF;
END $$;

-- A row written before a session reaching its day closed is stale (that close's rollup failed), so
-- the day comes from raw rows; a row whose raw rows have expired is read even while a session is open.

DO $$
DECLARE
    usr    uuid := gen_random_uuid();
    stale_ uuid := gen_random_uuid();  -- five days ago, closed after its day's row was written
    open_  uuid := gen_random_uuid();  -- started four days ago, never closed
    t0     timestamptz := date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC';
    s      record;
BEGIN
    INSERT INTO auth.users (id, email) VALUES (usr, 'summary-stale@test.invalid');
    INSERT INTO public.profiles (id, email, role)
    VALUES (usr, 'summary-stale@test.invalid', 'student') ON CONFLICT (id) DO NOTHING;
    INSERT INTO public.sessions (id, user_id, started_at, ended_at) VALUES
        (stale_, usr, t0 - interval '5 days' + interval '9 hours', t0 - interval '5 days' + interval '11 hours'),
        (open_, usr, t0 - interval '4 days' + interval '9 hours', NULL);

    -- Five days ago: rolled with two readings at 0.3, then a third (0.9) lands, and the row is dated
    -- before the session's close, as a close whose rollup failed leaves it.
    INSERT INTO public.cognitive_signals (session_id, user_id, ts, focus, stress, engagement)
    VALUES (stale_, usr, t0 - interval '5 days' + interval '9 hours 1 min', 0.3, NULL, 0.3),
           (stale_, usr, t0 - interval '5 days' + interval '9 hours 2 min', 0.3, NULL, 0.3);
    PERFORM public.rollup_signal_day(usr, (now() AT TIME ZONE 'UTC')::date - 5, 'UTC');
    INSERT INTO public.cognitive_signals (session_id, user_id, ts, focus, stress, engagement)
    VALUES (stale_, usr, t0 - interval '5 days' + interval '10 hours 30 min', 0.9, NULL, 0.9);
    UPDATE public.signal_daily_rollup SET updated_at = t0 - interval '5 days' + interval '10 hours'
     WHERE user_id = usr AND day = (now() AT TIME ZONE 'UTC')::date - 5;

    -- Four days ago, inside the open session: readings 0.5 and 0.7, rolled up, then expired.
    INSERT INTO public.cognitive_signals (session_id, user_id, ts, focus, stress, engagement)
    VALUES (open_, usr, t0 - interval '4 days' + interval '9 hours 1 min', 0.5, NULL, 0.5),
           (open_, usr, t0 - interval '4 days' + interval '9 hours 2 min', 0.7, NULL, 0.7);
    PERFORM public.rollup_signal_day(usr, (now() AT TIME ZONE 'UTC')::date - 4, 'UTC');
    DELETE FROM public.cognitive_signals WHERE session_id = open_;

    -- Today: two face readings, neither trusted.
    INSERT INTO public.face_signals (session_id, user_id, ts, emotion, emotion_trusted)
    SELECT open_, usr, t0 + g * interval '1 min', 'sad', false FROM generate_series(1, 2) g;

    SELECT * INTO s FROM public.student_signal_summary(usr, 7, true, true, 'UTC');

    -- Five days ago from its three raw readings (1.5), four days ago from its row (1.2), over 5.
    IF s.cognitive_samples <> 5 OR abs(s.focus - 2.7 / 5) > 1e-9 THEN
        RAISE EXCEPTION 'a stale row was read over raw rows, or an expired day was dropped: %', s;
    END IF;
    -- Readings arrived and none was trusted: a count with no label, not a camera that saw nothing.
    IF s.face_samples <> 2 OR s.dominant_emotion IS NOT NULL THEN
        RAISE EXCEPTION 'untrusted readings left the count, or one became the label: % %',
                        s.face_samples, s.dominant_emotion;
    END IF;
END $$;

-- ── the rollup records the score scale and calm source; a posted value cannot abort it ──
-- `raw` is client-supplied, so a bad score_scale must be skipped, not abort the day's rollup
-- (which would exempt its rows from expiry).
DO $$
DECLARE
    owner_id uuid;
    sess     uuid;
    lo       smallint;
    hi       smallint;
    n        int;
    srcs     text[];
    r        record;
BEGIN
    SELECT i.owner_id, i.sess_id INTO owner_id, sess FROM _ids i;
    DELETE FROM cognitive_signals WHERE user_id = owner_id;
    DELETE FROM signal_daily_rollup WHERE user_id = owner_id;

    -- Four rows on one day: no key (predates the label: scale 1), scale 2, a
    -- string, and a null measurement carrying a higher scale that must not count.
    INSERT INTO cognitive_signals (session_id, user_id, ts, focus, raw) VALUES
        (sess, owner_id, '2026-03-12T18:00:01Z', 0.5, '{}'::jsonb),
        (sess, owner_id, '2026-03-12T18:00:02Z', 0.5, '{"score_scale": 2}'::jsonb),
        (sess, owner_id, '2026-03-12T18:00:03Z', 0.5, '{"score_scale": "oops"}'::jsonb),
        (sess, owner_id, '2026-03-12T18:00:04Z', NULL, '{"score_scale": 3}'::jsonb);

    PERFORM public.rollup_signal_day(owner_id, DATE '2026-03-12', 'UTC');

    SELECT count(*) INTO n FROM signal_daily_rollup
     WHERE user_id = owner_id AND channel = 'cognitive';
    IF n <> 1 THEN
        RAISE EXCEPTION 'a posted score_scale aborted the cognitive rollup (% rows)', n;
    END IF;
    SELECT score_scale_min, score_scale_max INTO lo, hi FROM signal_daily_rollup
     WHERE user_id = owner_id AND channel = 'cognitive';
    IF lo IS DISTINCT FROM 1 OR hi IS DISTINCT FROM 2 THEN
        RAISE EXCEPTION 'score scale range is %..%, expected 1..2: absent is 1, '
                        'garbage is skipped, a nulled measurement does not count', lo, hi;
    END IF;

    -- Stress has its own count (a held calm nulls stress, keeps focus). Two of the three focus rows get one.
    UPDATE cognitive_signals SET stress = 0.4
     WHERE user_id = owner_id AND ts IN ('2026-03-12T18:00:01Z', '2026-03-12T18:00:02Z');
    PERFORM public.rollup_signal_day(owner_id, DATE '2026-03-12', 'UTC');
    SELECT stress_sample_count INTO n FROM signal_daily_rollup
     WHERE user_id = owner_id AND channel = 'cognitive';
    IF n IS DISTINCT FROM 2 THEN
        RAISE EXCEPTION 'stress_sample_count is %, expected 2 of the 3 rows with a focus', n;
    END IF;

    -- The calm source is its own column, not a scale: only a row with a stress names one,
    -- no key is sdk, and an unknown value names none.
    DELETE FROM cognitive_signals WHERE user_id = owner_id;
    INSERT INTO cognitive_signals (session_id, user_id, ts, focus, stress, raw) VALUES
        (sess, owner_id, '2026-03-12T18:00:01Z', 0.5, 0.4,  '{"score_scale": 2}'::jsonb),
        (sess, owner_id, '2026-03-12T18:00:02Z', 0.5, NULL,
         '{"score_scale": 2, "calm_source": "local"}'::jsonb),
        (sess, owner_id, '2026-03-12T18:00:03Z', 0.5, 0.4,
         '{"score_scale": 2, "calm_source": "martian"}'::jsonb);
    PERFORM public.rollup_signal_day(owner_id, DATE '2026-03-12', 'UTC');
    SELECT calm_sources, score_scale_min, score_scale_max INTO srcs, lo, hi
      FROM signal_daily_rollup WHERE user_id = owner_id AND channel = 'cognitive';
    IF srcs IS DISTINCT FROM ARRAY['sdk']::text[] OR lo IS DISTINCT FROM 2 OR hi IS DISTINCT FROM 2 THEN
        RAISE EXCEPTION 'calm sources % on scale %..%, expected {sdk} on 2..2: a held local '
                        'calm and an unknown source name no source', srcs, lo, hi;
    END IF;
    UPDATE cognitive_signals SET stress = 0.4
     WHERE user_id = owner_id AND ts = '2026-03-12T18:00:02Z';
    PERFORM public.rollup_signal_day(owner_id, DATE '2026-03-12', 'UTC');
    SELECT calm_sources, score_scale_max INTO srcs, hi
      FROM signal_daily_rollup WHERE user_id = owner_id AND channel = 'cognitive';
    IF srcs IS DISTINCT FROM ARRAY['local', 'sdk']::text[] OR hi IS DISTINCT FROM 2 THEN
        RAISE EXCEPTION 'a scored local calm beside sdk reads % on max scale %, '
                        'expected {local,sdk} on 2', srcs, hi;
    END IF;

    -- No stress at all names no source; a NULL raw is sdk and predates the label (scale 1).
    UPDATE cognitive_signals SET stress = NULL WHERE user_id = owner_id;
    PERFORM public.rollup_signal_day(owner_id, DATE '2026-03-12', 'UTC');
    SELECT calm_sources INTO srcs
      FROM signal_daily_rollup WHERE user_id = owner_id AND channel = 'cognitive';
    IF srcs IS NOT NULL THEN
        RAISE EXCEPTION 'a day with no stress named calm sources %', srcs;
    END IF;
    UPDATE cognitive_signals SET stress = 0.4, raw = NULL
     WHERE user_id = owner_id AND ts = '2026-03-12T18:00:01Z';
    PERFORM public.rollup_signal_day(owner_id, DATE '2026-03-12', 'UTC');
    SELECT calm_sources, score_scale_min INTO srcs, lo
      FROM signal_daily_rollup WHERE user_id = owner_id AND channel = 'cognitive';
    IF srcs IS DISTINCT FROM ARRAY['sdk']::text[] OR lo IS DISTINCT FROM 1 THEN
        RAISE EXCEPTION 'a row with a NULL raw reads % on scale %, expected {sdk} on 1', srcs, lo;
    END IF;

    IF public.calm_source_of(NULL) <> 'sdk' OR public.calm_source_of('{}'::jsonb) <> 'sdk'
       OR public.calm_source_of('"local"'::jsonb) <> 'sdk'
       OR public.calm_source_of('{"calm_source": "local"}'::jsonb) <> 'local'
       OR public.calm_source_of('{"calm_source": ["local"]}'::jsonb) IS NOT NULL
       OR public.calm_source_of('{"calm_source": "martian"}'::jsonb) IS NOT NULL THEN
        RAISE EXCEPTION 'calm_source_of does not read a posted source safely';
    END IF;

    -- 3 is retired: a row an older backend wrote for local calm reads as scale 2, source local.
    DELETE FROM cognitive_signals WHERE user_id = owner_id;
    INSERT INTO cognitive_signals (session_id, user_id, ts, focus, stress, raw) VALUES
        (sess, owner_id, '2026-03-12T18:00:01Z', 0.5, 0.4,
         '{"score_scale": 3, "calm_source": "local"}'::jsonb);
    PERFORM public.rollup_signal_day(owner_id, DATE '2026-03-12', 'UTC');
    SELECT calm_sources, score_scale_min, score_scale_max INTO srcs, lo, hi
      FROM signal_daily_rollup WHERE user_id = owner_id AND channel = 'cognitive';
    IF srcs IS DISTINCT FROM ARRAY['local']::text[] OR lo IS DISTINCT FROM 2 OR hi IS DISTINCT FROM 2 THEN
        RAISE EXCEPTION 'a retired scale-3 row reads % on %..%, expected {local} on 2..2', srcs, lo, hi;
    END IF;

    -- The one-time backfill, on rolled days as the old rollup wrote them.
    DELETE FROM signal_daily_rollup WHERE user_id = owner_id;
    INSERT INTO signal_daily_rollup (user_id, day, channel, avg_stress, sample_count,
                                     trusted_sample_count, score_scale_min, score_scale_max) VALUES
        (owner_id, DATE '2026-02-01', 'cognitive', 0.4,  5, 5, 3, 3),
        (owner_id, DATE '2026-02-02', 'cognitive', 0.4,  5, 5, 2, 3),
        (owner_id, DATE '2026-02-03', 'cognitive', 0.4,  5, 5, 2, 2),
        (owner_id, DATE '2026-02-04', 'cognitive', NULL, 5, 5, NULL, NULL),
        (owner_id, DATE '2026-02-05', 'cognitive', NULL, 5, 5, 1, 2);
    PERFORM public.backfill_rollup_calm_sources();
    FOR r IN SELECT day, calm_sources AS s, score_scale_min AS a, score_scale_max AS b
               FROM signal_daily_rollup WHERE user_id = owner_id ORDER BY day LOOP
        IF (r.day = '2026-02-01' AND (r.s IS DISTINCT FROM ARRAY['local']::text[] OR r.a IS DISTINCT FROM 2 OR r.b IS DISTINCT FROM 2))
        OR (r.day = '2026-02-02' AND (r.s IS DISTINCT FROM ARRAY['local', 'sdk']::text[] OR r.a IS DISTINCT FROM 2 OR r.b IS DISTINCT FROM 2))
        OR (r.day = '2026-02-03' AND (r.s IS DISTINCT FROM ARRAY['sdk']::text[] OR r.a IS DISTINCT FROM 2 OR r.b IS DISTINCT FROM 2))
        OR (r.day = '2026-02-04' AND (r.s IS NOT NULL OR r.a IS NOT NULL OR r.b IS NOT NULL))
        OR (r.day = '2026-02-05' AND (r.s IS NOT NULL OR r.a IS DISTINCT FROM 1 OR r.b IS DISTINCT FROM 2)) THEN
            RAISE EXCEPTION 'the calm-source backfill read % as % on %..%', r.day, r.s, r.a, r.b;
        END IF;
    END LOOP;
    IF public.backfill_rollup_calm_sources() <> 0 THEN
        RAISE EXCEPTION 'the calm-source backfill is not idempotent';
    END IF;

    IF public.score_scale_of('{"score_scale": "oops"}'::jsonb) IS NOT NULL
       OR public.score_scale_of('{"score_scale": 99999}'::jsonb) IS NOT NULL
       OR public.score_scale_of('{"score_scale": 2}'::jsonb) <> 2 THEN
        RAISE EXCEPTION 'score_scale_of does not skip what it cannot store';
    END IF;
END $$;

-- ── the cohort RPCs weight stress on its own count, with the fallback ──────
-- One row with a stress count (200 of 4000), one NULL (falls back to 200 focus rows):
-- weighted on stress counts 0.7 and 0.3 give 0.5; on focus counts, 0.68.
DO $$
DECLARE
    owner_id uuid;
    other_id uuid;
    v        double precision;
    n        bigint;
BEGIN
    SELECT i.owner_id, i.other_id INTO owner_id, other_id FROM _ids i;
    DELETE FROM signal_daily_rollup WHERE user_id IN (owner_id, other_id);
    INSERT INTO signal_daily_rollup
        (user_id, day, channel, avg_focus, avg_stress, sample_count,
         trusted_sample_count, stress_sample_count)
    VALUES (owner_id, current_date,     'cognitive', 0.5, 0.7, 4000, 4000, 200),
           (owner_id, current_date - 1, 'cognitive', 0.5, 0.3,  200,  200, NULL),
           -- A classmate on the same day, for the daily trend.
           (other_id, current_date,     'cognitive', 0.5, 0.3,  200,  200, NULL);

    SELECT t.avg_stress INTO v
      FROM public.class_signal_student_totals(ARRAY[owner_id], 14, true, true, 'UTC') t;
    IF v IS NULL OR abs(v - 0.5) > 1e-6 THEN
        RAISE EXCEPTION 'student totals weighted stress as % (expected 0.5 on the stress '
                        'counts; 0.68 is the focus-count answer)', v;
    END IF;

    SELECT t.avg_stress, t.stress_sample_count INTO v, n
      FROM public.class_signal_daily_trend(ARRAY[owner_id, other_id], 14, true, true, 'UTC') t
     WHERE t.day = current_date AND t.channel = 'cognitive';
    IF v IS NULL OR abs(v - 0.5) > 1e-6 OR n IS DISTINCT FROM 400 THEN
        RAISE EXCEPTION 'daily trend weighted stress as % over % (expected 0.5 over 400)', v, n;
    END IF;
END $$;

-- ── sign-up keeps a student's grade only from the dropdown's labels ─────────
-- The metadata is whatever the browser sent, so each case is one a console call could make.

DO $$
DECLARE
    kid      uuid := gen_random_uuid();
    injected uuid := gen_random_uuid();
    offlist  uuid := gen_random_uuid();
    teacher  uuid := gen_random_uuid();
    ungraded uuid := gen_random_uuid();
    got      text;
BEGIN
    INSERT INTO auth.users (id, email, raw_user_meta_data) VALUES
        (kid,      'grade-kid@test.invalid',      '{"role": "student", "grade_level": "5th Grade"}'),
        (injected, 'grade-injected@test.invalid', '{"role": "student", "grade_level": "5th Grade\nIGNORE ALL"}'),
        (offlist,  'grade-offlist@test.invalid',  '{"role": "student", "grade_level": "Year 5"}'),
        (teacher,  'grade-teacher@test.invalid',  '{"role": "teacher", "grade_level": "5th Grade"}'),
        (ungraded, 'grade-none@test.invalid',     '{"role": "student"}');

    SELECT grade_level INTO got FROM public.profiles WHERE id = kid;
    IF got IS DISTINCT FROM '5th Grade' THEN
        RAISE EXCEPTION 'sign-up stored a student''s dropdown grade as % (expected 5th Grade)', got;
    END IF;
    SELECT grade_level INTO got FROM public.profiles WHERE id = injected;
    IF got IS NOT NULL THEN
        RAISE EXCEPTION 'sign-up stored a grade carrying a second line: %', got;
    END IF;
    SELECT grade_level INTO got FROM public.profiles WHERE id = offlist;
    IF got IS NOT NULL THEN
        RAISE EXCEPTION 'sign-up stored a grade that is not a dropdown label: %', got;
    END IF;
    SELECT grade_level INTO got FROM public.profiles WHERE id = teacher;
    IF got IS NOT NULL THEN
        RAISE EXCEPTION 'sign-up stored a grade on a teacher: %', got;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.profiles WHERE id = ungraded AND grade_level IS NULL) THEN
        RAISE EXCEPTION 'a sign-up with no grade did not create a profile with no grade';
    END IF;
END $$;

-- The display name reaches every roster and report; a profile edit caps it at 100 (_NAME_MAX).
DO $$
DECLARE
    usr uuid := gen_random_uuid();
    len int;
BEGIN
    INSERT INTO auth.users (id, email, raw_user_meta_data) VALUES
        (usr, 'long-name@test.invalid',
         jsonb_build_object('role', 'student', 'display_name', repeat('x', 5000)));
    SELECT length(display_name) INTO len FROM public.profiles WHERE id = usr;
    IF len IS DISTINCT FROM 100 THEN
        RAISE EXCEPTION 'sign-up stored a display name of % characters (expected 100)', len;
    END IF;
END $$;

-- erase_signals and record_chart_paths must take one per-student advisory lock, exclusive and
-- shared, or an archive can miss an erasure. Read from the locks this transaction holds.
DO $$
DECLARE
    sess     uuid;
    owner_id uuid;
    lock_key bigint;
    n        int;
    returned jsonb;
BEGIN
    SELECT id, user_id INTO sess, owner_id FROM public.sessions ORDER BY id LIMIT 1;
    IF sess IS NULL THEN
        RAISE EXCEPTION 'fixture is wrong: no session to record chart paths on';
    END IF;
    lock_key := hashtextextended('chart_paths:' || owner_id::text, 0);

    PERFORM public.record_chart_paths(sess, '{"cognitive_timeline": null}'::jsonb);
    IF (SELECT chart_paths FROM public.sessions WHERE id = sess)
           IS DISTINCT FROM '{"cognitive_timeline": null}'::jsonb THEN
        RAISE EXCEPTION 'record_chart_paths did not write the paths it was given';
    END IF;

    -- A bigint advisory key is split across classid (high 32 bits) and objid, objsubid 1.
    SELECT count(*) INTO n FROM pg_locks
     WHERE locktype = 'advisory' AND pid = pg_backend_pid() AND objsubid = 1
       AND mode = 'ShareLock' AND ((classid::bigint << 32) | objid::bigint) = lock_key;
    IF n <> 1 THEN
        RAISE EXCEPTION 'record_chart_paths took no shared lock on its student''s key';
    END IF;

    PERFORM public.erase_signals(owner_id, 'eeg', NULL, 'UTC');
    SELECT count(*) INTO n FROM pg_locks
     WHERE locktype = 'advisory' AND pid = pg_backend_pid() AND objsubid = 1
       AND mode = 'ExclusiveLock' AND ((classid::bigint << 32) | objid::bigint) = lock_key;
    IF n <> 1 THEN
        RAISE EXCEPTION 'erase_signals took no exclusive lock on the key record_chart_paths shares';
    END IF;

    IF has_function_privilege('authenticated', 'public.record_chart_paths(uuid, jsonb)', 'EXECUTE')
       OR has_function_privilege('anon', 'public.record_chart_paths(uuid, jsonb)', 'EXECUTE') THEN
        RAISE EXCEPTION 'record_chart_paths is executable by an application role -- any user '
                        'could point any session''s charts anywhere';
    END IF;

    -- drop_chart_paths nulls the named keys in the row and nothing else: a whole-map write from
    -- a stale copy is what it replaces. A key the row never had is not added.
    PERFORM public.record_chart_paths(sess,
        '{"cognitive_timeline": "a.svg", "heart_rate": "b.svg"}'::jsonb);
    -- Two statements: in one, the row read shares a snapshot from before the call's UPDATE.
    returned := public.drop_chart_paths(sess, ARRAY['cognitive_timeline', 'emotion_pie']);
    IF returned IS DISTINCT FROM '{"cognitive_timeline": null, "heart_rate": "b.svg"}'::jsonb
       OR (SELECT chart_paths FROM public.sessions WHERE id = sess)
           IS DISTINCT FROM '{"cognitive_timeline": null, "heart_rate": "b.svg"}'::jsonb THEN
        RAISE EXCEPTION 'drop_chart_paths changed more than the charts it was named: returned %',
            returned;
    END IF;
    IF has_function_privilege('authenticated', 'public.drop_chart_paths(uuid, text[])', 'EXECUTE')
       OR has_function_privilege('anon', 'public.drop_chart_paths(uuid, text[])', 'EXECUTE') THEN
        RAISE EXCEPTION 'drop_chart_paths is executable by an application role';
    END IF;
END $$;

-- expired_signal_cutoff: ends_on is still a recorded day, so it expires only once passed; an
-- unenforced year has no cutoff whatever dates linger in the row.
DO $$
DECLARE
    today date := (now() AT TIME ZONE 'UTC')::date;
BEGIN
    DELETE FROM public.retention_window;
    INSERT INTO public.retention_window (starts_on, ends_on, timezone)
    VALUES (today - 200, today, 'UTC');
    IF public.expired_signal_cutoff() IS DISTINCT FROM today - 201 THEN
        RAISE EXCEPTION 'on its last day the year already expired: cutoff %',
            public.expired_signal_cutoff();
    END IF;

    DELETE FROM public.retention_window;
    INSERT INTO public.retention_window (starts_on, ends_on, timezone)
    VALUES (today - 200, today - 1, 'UTC');
    IF public.expired_signal_cutoff() IS DISTINCT FROM today - 1 THEN
        RAISE EXCEPTION 'the day after ends_on did not expire the year: cutoff %',
            public.expired_signal_cutoff();
    END IF;

    DELETE FROM public.retention_window;
    INSERT INTO public.retention_window (enforced, starts_on, ends_on, timezone)
    VALUES (false, today - 400, today - 100, 'UTC');
    IF public.expired_signal_cutoff() IS NOT NULL THEN
        RAISE EXCEPTION 'an unenforced year still has a cutoff of %, so it goes on deleting',
            public.expired_signal_cutoff();
    END IF;
END $$;

-- Erasing one heart source keeps the other's expired rollup days: with no raw rows left the
-- rollup is the last copy, and one that never drew on the erased source holds none of it.
DO $$
DECLARE
    uid uuid;
    n   int;
BEGIN
    SELECT owner_id INTO uid FROM _ids;
    DELETE FROM public.heart_signals WHERE user_id = uid;
    DELETE FROM public.signal_daily_rollup WHERE user_id = uid;
    INSERT INTO public.signal_daily_rollup
        (user_id, day, channel, avg_heart_rate_bpm, sample_count, trusted_sample_count,
         heart_sources)
    VALUES (uid, DATE '2025-03-10', 'heart', 70, 5, 5, ARRAY['muse_optics']),
           (uid, DATE '2025-03-11', 'heart', 95, 10, 10, ARRAY['muse_optics', 'rppg']),
           (uid, DATE '2025-03-12', 'heart', 80, 5, 5, NULL),
           (uid, DATE '2025-03-13', 'heart', 120, 5, 5, ARRAY['rppg']);

    PERFORM public.erase_signals(uid, 'camera', NULL, 'UTC');

    IF NOT EXISTS (SELECT 1 FROM public.signal_daily_rollup
                    WHERE user_id = uid AND channel = 'heart' AND day = DATE '2025-03-10') THEN
        RAISE EXCEPTION 'erasing the camera took an expired headband-only heart day with it';
    END IF;
    SELECT count(*) INTO n FROM public.signal_daily_rollup
     WHERE user_id = uid AND channel = 'heart'
       AND day IN (DATE '2025-03-11', DATE '2025-03-12', DATE '2025-03-13');
    IF n <> 0 THEN
        RAISE EXCEPTION '% heart day(s) that drew on the camera, or might have, outlived its erasure', n;
    END IF;

    -- The headband's second source name: a raw row and an expired day under muse_ppg.
    INSERT INTO public.heart_signals (session_id, user_id, source, ts, heart_rate_bpm, trusted)
    SELECT sess_id, uid, 'muse_ppg', '2025-03-20T10:00:00Z', 72, true FROM _ids;
    INSERT INTO public.signal_daily_rollup
        (user_id, day, channel, avg_heart_rate_bpm, sample_count, trusted_sample_count,
         heart_sources)
    VALUES (uid, DATE '2025-03-14', 'heart', 72, 5, 5, ARRAY['muse_ppg']);

    PERFORM public.erase_signals(uid, 'headband_optical', NULL, 'UTC');
    SELECT count(*) INTO n FROM public.signal_daily_rollup
     WHERE user_id = uid AND channel = 'heart';
    IF n <> 0 THEN
        RAISE EXCEPTION '% headband heart day(s) outlived erasing the headband', n;
    END IF;
    SELECT count(*) INTO n FROM public.heart_signals WHERE user_id = uid AND source = 'muse_ppg';
    IF n <> 0 THEN
        RAISE EXCEPTION 'erasing the headband left % muse_ppg heart row(s)', n;
    END IF;
END $$;

-- last_active_for_users: a sweep's ended_at is when the sweep ran, not when the student was there.
DO $$
DECLARE
    usr  uuid := gen_random_uuid();
    s1   uuid := gen_random_uuid();
    s2   uuid := gen_random_uuid();
    seen timestamptz;
BEGIN
    INSERT INTO auth.users (id, email) VALUES (usr, 'last-active@test.invalid');
    INSERT INTO public.sessions (id, user_id, started_at, ended_at) VALUES
        (s1, usr, '2026-06-01T09:00:00Z', '2026-07-15T03:00:00Z'),    -- closed weeks later by the sweep
        (s2, usr, '2026-05-20T09:00:00Z', '2026-05-20T09:20:00Z');
    INSERT INTO public.session_answers (session_id, user_id, correct, answered_at)
    VALUES (s1, usr, true, '2026-06-01T09:10:00Z');

    SELECT last_active INTO seen FROM public.last_active_for_users(ARRAY[usr]);
    IF seen IS DISTINCT FROM '2026-06-01T09:10:00Z'::timestamptz THEN
        RAISE EXCEPTION 'last active reads %, expected the last answer (09:10 on 1 June)', seen;
    END IF;

    -- Work with no answer still counts: a sample in the newest session is activity.
    INSERT INTO public.cognitive_signals (session_id, user_id, ts, focus)
    VALUES (s1, usr, '2026-06-01T09:25:00Z', 0.5);
    SELECT last_active INTO seen FROM public.last_active_for_users(ARRAY[usr]);
    IF seen IS DISTINCT FROM '2026-06-01T09:25:00Z'::timestamptz THEN
        RAISE EXCEPTION 'last active reads %, expected the newest signal (09:25 on 1 June)', seen;
    END IF;

    -- A headband left on the desk writes rows that measured nothing; they are not activity.
    INSERT INTO public.cognitive_signals (session_id, user_id, ts, focus)
    VALUES (s1, usr, '2026-06-01T13:00:00Z', NULL);
    INSERT INTO public.heart_signals (session_id, user_id, source, ts, heart_rate_bpm, trusted)
    VALUES (s1, usr, 'muse_optics', '2026-06-01T13:05:00Z', NULL, false);
    INSERT INTO public.face_signals (session_id, user_id, ts, emotion)
    VALUES (s1, usr, '2026-06-01T13:10:00Z', NULL);
    SELECT last_active INTO seen FROM public.last_active_for_users(ARRAY[usr]);
    IF seen IS DISTINCT FROM '2026-06-01T09:25:00Z'::timestamptz THEN
        RAISE EXCEPTION 'last active reads %: a sample that measured nothing counted as activity',
            seen;
    END IF;
END $$;

-- ── columns a client must never write, checked as Postgres answers it ───────
-- A column REVOKE leaves a table-level grant standing, so has_column_privilege, not the migration
-- text, is the check: a later GRANT UPDATE ON profiles would otherwise make any caller an admin.

DO $$
DECLARE
    c record;
    who text;
    cmd text;
BEGIN
    FOR c IN SELECT * FROM (VALUES ('profiles', 'role'),
                                   ('parent_child_links', 'student_ack_at'),
                                   ('parent_child_links', 'parent_ack_at'),
                                   ('classes', 'teacher_id'),
                                   ('classes', 'join_code')) AS t(tbl, col) LOOP
        FOREACH who IN ARRAY ARRAY['anon', 'authenticated'] LOOP
            FOREACH cmd IN ARRAY ARRAY['UPDATE', 'INSERT'] LOOP
                IF has_column_privilege(who, 'public.' || c.tbl, c.col, cmd) THEN
                    RAISE EXCEPTION '% can % %.% -- grant a column list without it', who, cmd, c.tbl, c.col;
                END IF;
            END LOOP;
        END LOOP;
    END LOOP;
END $$;

-- ── claim_daily_question admits up to the limit, per student per day ────────
-- The only place the claim runs; the backend suite can check only the call's arguments.

DO $$
DECLARE
    usr  uuid;
    got  boolean[] := '{}';
    d    date := DATE '2026-09-28';
BEGIN
    SELECT owner_id INTO usr FROM _ids;
    FOR i IN 1..3 LOOP
        got := got || public.claim_daily_question(usr, d, 2);
    END LOOP;
    IF got IS DISTINCT FROM ARRAY[true, true, false] THEN
        RAISE EXCEPTION 'a limit of 2 answered % over three claims', got;
    END IF;
    IF NOT public.claim_daily_question(usr, d + 1, 2) THEN
        RAISE EXCEPTION 'the next day started already spent';
    END IF;
    IF (SELECT served FROM public.daily_question_usage WHERE user_id = usr AND day = d) <> 2 THEN
        RAISE EXCEPTION 'a refused claim was counted';
    END IF;
    IF public.claim_daily_question(usr, d + 2, 0) THEN
        RAISE EXCEPTION 'a limit of 0 admitted a question';
    END IF;
    -- A refund gives one back, on its own day only, and never below zero.
    PERFORM public.release_daily_question(usr, d);
    IF NOT public.claim_daily_question(usr, d, 2) THEN
        RAISE EXCEPTION 'a refunded question could not be claimed again';
    END IF;
    PERFORM public.release_daily_question(usr, d + 1);
    PERFORM public.release_daily_question(usr, d + 1);
    IF (SELECT served FROM public.daily_question_usage WHERE user_id = usr AND day = d + 1) <> 0
       OR (SELECT served FROM public.daily_question_usage WHERE user_id = usr AND day = d) <> 2 THEN
        RAISE EXCEPTION 'a refund went below zero or reached another day';
    END IF;
    IF has_function_privilege('authenticated', 'public.claim_daily_question(uuid, date, integer)', 'EXECUTE')
       OR has_table_privilege('authenticated', 'public.daily_question_usage', 'SELECT') THEN
        RAISE EXCEPTION 'a client role can reach the daily question budget';
    END IF;
END $$;

-- ── a teacher reads no consent row: it names the parent who changed it ─────
-- The student's own read stays, so this cannot pass by revoking the table from everyone.

DO $$
DECLARE
    usr     uuid;
    teacher uuid := gen_random_uuid();
    cls     uuid := gen_random_uuid();
    n       int;
BEGIN
    SELECT owner_id INTO usr FROM _ids;
    INSERT INTO auth.users (id, email) VALUES (teacher, 'teacher@test.invalid');
    INSERT INTO public.profiles (id, email, role) VALUES (teacher, 'teacher@test.invalid', 'teacher')
        ON CONFLICT (id) DO UPDATE SET role = 'teacher';
    INSERT INTO public.classes (id, teacher_id, name, join_code) VALUES (cls, teacher, 'c', 'ZZZZ9999');
    INSERT INTO public.class_memberships (class_id, student_id) VALUES (cls, usr);
    INSERT INTO public.signal_consent (user_id, camera_enabled) VALUES (usr, true)
        ON CONFLICT (user_id) DO NOTHING;

    SET LOCAL ROLE authenticated;
    PERFORM set_config('request.jwt.claims',
                       json_build_object('sub', teacher, 'role', 'authenticated')::text, true);
    SELECT count(*) INTO n FROM public.signal_consent WHERE user_id = usr;
    IF n <> 0 THEN
        RAISE EXCEPTION 'a teacher read % consent rows naming who changed them', n;
    END IF;
    PERFORM set_config('request.jwt.claims',
                       json_build_object('sub', usr, 'role', 'authenticated')::text, true);
    SELECT count(*) INTO n FROM public.signal_consent WHERE user_id = usr;
    IF n <> 1 THEN
        RAISE EXCEPTION 'the student could not read their own consent row';
    END IF;
    RESET ROLE;
END $$;

-- ── no client role holds a sequence, now or for one created later ───────────

DO $$
DECLARE
    s record;
    who text;
BEGIN
    CREATE SEQUENCE public.assert_rls_new_seq;
    FOR s IN SELECT c.oid::regclass AS seq FROM pg_class c
             JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relkind = 'S' AND n.nspname = 'public' LOOP
        FOREACH who IN ARRAY ARRAY['anon', 'authenticated'] LOOP
            IF has_sequence_privilege(who, s.seq, 'USAGE')
               OR has_sequence_privilege(who, s.seq, 'SELECT')
               OR has_sequence_privilege(who, s.seq, 'UPDATE') THEN
                RAISE EXCEPTION '% holds a privilege on sequence %', who, s.seq;
            END IF;
        END LOOP;
    END LOOP;
END $$;

-- ── the definer helpers resolve only qualified tables ────────────────────────
-- A temp table shadowing class_memberships must not make a student a member of a class.

DO $$
DECLARE
    usr    uuid;
    ghost  uuid := gen_random_uuid();
    cls    uuid;
BEGIN
    SELECT owner_id INTO usr FROM _ids;
    SELECT class_id INTO cls FROM public.class_memberships WHERE student_id = usr LIMIT 1;
    PERFORM set_config('request.jwt.claims',
                       json_build_object('sub', usr, 'role', 'authenticated')::text, true);
    CREATE TEMP TABLE class_memberships (class_id uuid, student_id uuid);
    INSERT INTO pg_temp.class_memberships VALUES (ghost, usr);
    IF public.is_member_of_class(ghost) THEN
        RAISE EXCEPTION 'is_member_of_class read a temp table shadowing class_memberships';
    END IF;
    IF NOT public.is_member_of_class(cls) THEN
        RAISE EXCEPTION 'is_member_of_class no longer finds a real membership';
    END IF;
    DROP TABLE pg_temp.class_memberships;
    IF EXISTS (SELECT 1 FROM pg_proc WHERE proname IN ('is_member_of_class', 'is_teacher_of_class')
               AND NOT ('search_path=""' = ANY (coalesce(proconfig, '{}')))) THEN
        RAISE EXCEPTION 'a definer helper does not pin an empty search_path';
    END IF;
END $$;

-- ── weekly_signal_days: per school day, uncapped, trusted where the rollup is ──
DO $$
DECLARE
    uid uuid := gen_random_uuid(); sess uuid := gen_random_uuid(); res jsonb; d jsonb;
    raised boolean := false; rolled int;
BEGIN
    -- Its own student: the function aggregates every row the student has since p_since.
    INSERT INTO auth.users (id, email) VALUES (uid, 'weekly-days@test.invalid');
    INSERT INTO public.profiles (id, email, role)
    VALUES (uid, 'weekly-days@test.invalid', 'student') ON CONFLICT (id) DO NOTHING;
    INSERT INTO public.sessions (id, user_id) VALUES (sess, uid);
    -- 1500 rows on one Los Angeles day: past the 1000-row PostgREST ceiling the raw read hit.
    INSERT INTO public.cognitive_signals (session_id, user_id, ts, focus, stress)
    SELECT sess, uid, '2026-09-01T16:00:00Z'::timestamptz + (g || ' s')::interval, 0.5, 0.2
      FROM generate_series(1, 1500) g;
    -- 05:00Z on 2 Sept is still 1 Sept in Los Angeles; its focus is null (poor contact).
    INSERT INTO public.cognitive_signals (session_id, user_id, ts, focus, stress)
    VALUES (sess, uid, '2026-09-02T05:00:00Z', NULL, 0.9);

    res := public.weekly_signal_days(uid, 'cognitive', '2026-08-30T00:00:00Z', 'America/Los_Angeles');
    IF jsonb_array_length(res->'days') IS DISTINCT FROM 1 THEN
        RAISE EXCEPTION 'expected one Los Angeles day, got %', res->'days';
    END IF;
    d := res->'days'->0;
    IF d->>'day' IS DISTINCT FROM '2026-09-01' OR (d->>'rows')::int IS DISTINCT FROM 1501 OR (d->>'focus_n')::int IS DISTINCT FROM 1500
       OR (d->>'stress_n')::int IS DISTINCT FROM 1501 OR (d->>'stress_max')::float8 IS DISTINCT FROM 0.9 THEN
        RAISE EXCEPTION 'cognitive day aggregate is wrong or capped: %', d;
    END IF;
    -- Newest row with a measurement, not the newer null-focus row.
    IF (res->'latest'->>'focus')::float8 IS DISTINCT FROM 0.5 THEN
        RAISE EXCEPTION 'latest cognitive is not the newest measured row: %', res->'latest';
    END IF;

    INSERT INTO public.face_signals (session_id, user_id, ts, emotion, emotion_trusted, attention)
    VALUES (sess, uid, '2026-09-01T17:00:00Z', 'happy', true, 0.8),
           (sess, uid, '2026-09-01T17:00:01Z', 'happy', true, NULL),
           (sess, uid, '2026-09-01T17:00:02Z', 'sad', false, NULL),
           (sess, uid, '2026-09-01T17:00:03Z', NULL, NULL, NULL);
    d := public.weekly_signal_days(uid, 'emotion', '2026-08-30T00:00:00Z', 'America/Los_Angeles')->'days'->0;
    -- The shares' denominator: an untrusted label is in neither side.
    IF (SELECT sum(v::int) FROM jsonb_each_text(d->'emotion_counts') e(k, v))
       IS DISTINCT FROM (d->>'trusted_emotion_rows')::int THEN
        RAISE EXCEPTION 'emotion_counts do not sum to trusted_emotion_rows: %', d;
    END IF;
    IF (d->>'rows')::int IS DISTINCT FROM 4 OR (d->>'trusted_emotion_rows')::int IS DISTINCT FROM 2
       OR d->'emotion_counts' IS DISTINCT FROM '{"happy": 2}'::jsonb OR (d->>'attention_n')::int IS DISTINCT FROM 1 THEN
        RAISE EXCEPTION 'emotion day aggregate is wrong (untrusted must not count): %', d;
    END IF;
    -- Raw and rolled-up days must count face samples alike, so ask the rollup itself.
    PERFORM public.rollup_signal_day(uid, '2026-09-01', 'America/Los_Angeles');
    SELECT sample_count INTO rolled FROM public.signal_daily_rollup
     WHERE user_id = uid AND day = '2026-09-01' AND channel = 'emotion';
    IF (d->>'emotion_rows')::int IS DISTINCT FROM rolled THEN
        RAISE EXCEPTION 'emotion_rows is %, the rollup''s emotion sample_count %', d->>'emotion_rows', rolled;
    END IF;

    INSERT INTO public.heart_signals (session_id, user_id, source, ts, heart_rate_bpm, rmssd_ms, trusted)
    VALUES (sess, uid, 'muse_optics', '2026-09-01T17:00:00Z', 70, 40, true),
           (sess, uid, 'rppg',        '2026-09-01T17:00:05Z', 150, 10, false);
    res := public.weekly_signal_days(uid, 'heart', '2026-08-30T00:00:00Z', 'America/Los_Angeles');
    d := res->'days'->0;
    IF (d->>'rows')::int IS DISTINCT FROM 2 OR (d->>'trusted_rows')::int IS DISTINCT FROM 1 OR (d->>'bpm_sum')::float8 IS DISTINCT FROM 70
       OR d->'sources' IS DISTINCT FROM '["muse_optics"]'::jsonb OR res->'latest'->>'source' IS DISTINCT FROM 'muse_optics' THEN
        RAISE EXCEPTION 'heart day aggregate is wrong (trusted only): %', res;
    END IF;

    BEGIN
        PERFORM public.weekly_signal_days(uid, 'eeg', now(), 'UTC');
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM NOT LIKE 'weekly_signal_days: unknown channel%' THEN RAISE; END IF;
        raised := true;
    END;
    -- Asserted outside the block, whose handler would otherwise catch the failure itself.
    IF NOT raised THEN
        RAISE EXCEPTION 'weekly_signal_days accepted channel eeg';
    END IF;
    DELETE FROM public.sessions WHERE id = sess;
END $$;

-- ── recent_sessions_for_users, last_activity_for_sessions, latest_signal_ts_for_sessions ──
DO $$
DECLARE
    uid uuid := gen_random_uuid(); s1 uuid := gen_random_uuid(); s2 uuid := gen_random_uuid();
    s3 uuid := gen_random_uuid(); empty uuid := gen_random_uuid();
    f_emotion uuid := gen_random_uuid(); f_gaze uuid := gen_random_uuid();
    f_yaw uuid := gen_random_uuid(); f_none uuid := gen_random_uuid();
    hr uuid := gen_random_uuid(); hr_none uuid := gen_random_uuid();
    n int; seen timestamptz; keys text[]; act record; raised boolean := false;
BEGIN
    -- Its own student, so the per-student limit counts only this block's sessions.
    INSERT INTO auth.users (id, email) VALUES (uid, 'batch-reads@test.invalid');
    INSERT INTO public.profiles (id, email, role)
    VALUES (uid, 'batch-reads@test.invalid', 'student') ON CONFLICT (id) DO NOTHING;
    INSERT INTO public.sessions (id, user_id, started_at) VALUES
        (s1, uid, '2030-01-01T09:00:00Z'), (s2, uid, '2030-01-02T09:00:00Z'),
        (s3, uid, '2030-01-03T09:00:00Z'), (empty, uid, '2029-12-31T09:00:00Z');

    -- The two newest, once each though the id is passed twice.
    SELECT count(*) INTO n FROM public.recent_sessions_for_users(ARRAY[uid, uid], 2) r
     WHERE r.id IN (s2, s3);
    IF n IS DISTINCT FROM 2 OR (SELECT count(*) FROM public.recent_sessions_for_users(ARRAY[uid], 2)) IS DISTINCT FROM 2 THEN
        RAISE EXCEPTION 'recent_sessions_for_users is not the newest two once each';
    END IF;
    IF (SELECT count(*) FROM public.recent_sessions_for_users(ARRAY[uid], 0)) IS DISTINCT FROM 1 THEN
        RAISE EXCEPTION 'a limit below 1 is not clamped to 1';
    END IF;

    -- Rows reach a browser, so exactly main.py's _SESSION_CLIENT_COLUMNS.
    SELECT array_agg(k ORDER BY k) INTO keys
      FROM (SELECT DISTINCT jsonb_object_keys(to_jsonb(x)) AS k
              FROM public.recent_sessions_for_users(ARRAY[uid], 50) x) z;
    IF 'chart_paths' = ANY (keys) THEN
        RAISE EXCEPTION 'recent_sessions_for_users returns chart_paths: %', keys;
    END IF;
    IF keys IS DISTINCT FROM ARRAY['class_id', 'correct_answers', 'ended_at', 'id',
                                   'questions_answered', 'started_at', 'title', 'user_id'] THEN
        RAISE EXCEPTION 'recent_sessions_for_users returns %, not the client columns', keys;
    END IF;

    INSERT INTO public.session_answers (session_id, user_id, correct, answered_at)
    VALUES (s1, uid, true, '2030-01-01T09:01:00Z');
    INSERT INTO public.cognitive_signals (session_id, user_id, ts, focus) VALUES
        (s1, uid, '2030-01-01T09:02:00Z', 0.5),
        (s1, uid, '2030-01-01T09:05:00Z', NULL);   -- poor contact: not activity
    SELECT last_activity_at INTO seen FROM public.last_activity_for_sessions(ARRAY[s1, empty])
     WHERE session_id = s1;
    IF seen IS DISTINCT FROM '2030-01-01T09:02:00Z'::timestamptz THEN
        RAISE EXCEPTION 'last activity is %, expected the newest measured row at 09:02', seen;
    END IF;
    SELECT last_activity_at INTO seen FROM public.last_activity_for_sessions(ARRAY[s1, empty])
     WHERE session_id = empty;
    IF seen IS NOT NULL THEN
        RAISE EXCEPTION 'a session with no activity reported %', seen;
    END IF;

    -- Face: any one of emotion, gaze_x, head_yaw is activity. Heart: heart_rate_bpm. A newer
    -- unmeasured row on each measured session must not move the answer.
    INSERT INTO public.sessions (id, user_id) VALUES
        (f_emotion, uid), (f_gaze, uid), (f_yaw, uid), (f_none, uid), (hr, uid), (hr_none, uid);
    INSERT INTO public.face_signals (session_id, user_id, ts, emotion, gaze_x, head_yaw) VALUES
        (f_emotion, uid, '2030-01-01T09:01:00Z', 'happy', NULL, NULL),
        (f_gaze,    uid, '2030-01-01T09:01:00Z', NULL, 0.1, NULL),
        (f_yaw,     uid, '2030-01-01T09:01:00Z', NULL, NULL, 5),
        (f_none,    uid, '2030-01-01T09:01:00Z', NULL, NULL, NULL);
    INSERT INTO public.face_signals (session_id, user_id, ts)
    SELECT s, uid, '2030-01-01T09:09:00Z' FROM unnest(ARRAY[f_emotion, f_gaze, f_yaw]) s;
    INSERT INTO public.heart_signals (session_id, user_id, source, ts, heart_rate_bpm) VALUES
        (hr,      uid, 'muse_optics', '2030-01-01T09:01:00Z', 70),
        (hr,      uid, 'muse_optics', '2030-01-01T09:09:00Z', NULL),
        (hr_none, uid, 'muse_optics', '2030-01-01T09:01:00Z', NULL);
    FOR act IN
        SELECT e.label, e.expected, a.last_activity_at
          FROM (VALUES (f_emotion, 'face emotion', '2030-01-01T09:01:00Z'::timestamptz),
                       (f_gaze, 'face gaze_x', '2030-01-01T09:01:00Z'),
                       (f_yaw, 'face head_yaw', '2030-01-01T09:01:00Z'),
                       (f_none, 'face unmeasured', NULL),
                       (hr, 'heart_rate_bpm', '2030-01-01T09:01:00Z'),
                       (hr_none, 'heart unmeasured', NULL)) e(sid, label, expected)
          LEFT JOIN public.last_activity_for_sessions(
                        ARRAY[f_emotion, f_gaze, f_yaw, f_none, hr, hr_none]) a
            ON a.session_id = e.sid
    LOOP
        IF act.last_activity_at IS DISTINCT FROM act.expected THEN
            RAISE EXCEPTION 'last activity (%) is %, expected %', act.label, act.last_activity_at, act.expected;
        END IF;
    END LOOP;

    -- Timestamps only, never readings, and per channel.
    SELECT ts INTO seen FROM public.latest_signal_ts_for_sessions(ARRAY[s1], 'cognitive');
    IF seen IS DISTINCT FROM '2030-01-01T09:05:00Z'::timestamptz THEN
        RAISE EXCEPTION 'latest cognitive ts is %, expected 09:05', seen;
    END IF;
    IF EXISTS (SELECT 1 FROM public.latest_signal_ts_for_sessions(ARRAY[s1], 'face')) THEN
        RAISE EXCEPTION 'a session with no face rows reported one';
    END IF;
    -- 'eeg' is the admin payload's name, not a channel here: empty would read "never reported".
    BEGIN
        PERFORM public.latest_signal_ts_for_sessions(ARRAY[s1], 'eeg');
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM NOT LIKE 'latest_signal_ts_for_sessions: unknown channel%' THEN RAISE; END IF;
        raised := true;
    END;
    IF NOT raised THEN
        RAISE EXCEPTION 'latest_signal_ts_for_sessions accepted channel eeg';
    END IF;

    -- The per-student limit is capped at 50 whatever the caller asks.
    INSERT INTO public.sessions (id, user_id, started_at)
    SELECT gen_random_uuid(), uid, '2020-01-01T00:00:00Z'::timestamptz + (g || ' h')::interval
      FROM generate_series(1, 55) g;
    IF (SELECT count(*) FROM public.sessions WHERE user_id = uid) <= 50 THEN
        RAISE EXCEPTION 'the limit fixture needs more than 50 sessions';
    END IF;
    SELECT count(*), min(x.started_at) INTO n, seen
      FROM public.recent_sessions_for_users(ARRAY[uid], 60) x;
    IF n IS DISTINCT FROM 50 OR seen IS DISTINCT FROM (
           SELECT started_at FROM public.sessions WHERE user_id = uid
            ORDER BY started_at DESC OFFSET 49 LIMIT 1) THEN
        RAISE EXCEPTION 'a limit of 60 returned % sessions back to %, expected the newest 50', n, seen;
    END IF;
    DELETE FROM public.sessions WHERE user_id = uid;
END $$;

-- ── ingest_gate: the session row and the caller's consent row, fetched, never decided ──
DO $$
DECLARE
    uid uuid := gen_random_uuid(); owner uuid := gen_random_uuid();
    sess uuid := gen_random_uuid(); g jsonb; keys text[];
BEGIN
    INSERT INTO auth.users (id, email) VALUES
        (uid, 'ingest-gate@test.invalid'), (owner, 'ingest-gate-owner@test.invalid');
    INSERT INTO public.sessions (id, user_id, started_at) VALUES (sess, owner, '2030-01-01T09:00:00Z');
    INSERT INTO public.signal_consent (user_id, eeg_enabled) VALUES (uid, true);

    -- Someone else's session comes back with its owner: main.py makes the 403 and its event.
    g := public.ingest_gate(sess, uid);
    IF g->'session'->>'user_id' IS DISTINCT FROM owner::text THEN
        RAISE EXCEPTION 'ingest_gate session is %, expected the row and its owner', g->'session';
    END IF;
    IF g->'consent'->>'user_id' IS DISTINCT FROM uid::text
       OR (g->'consent'->>'eeg_enabled')::boolean IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'ingest_gate consent is %, expected the caller''s row', g->'consent';
    END IF;
    -- The consent is the caller's, never the session owner's, and an absent row is null.
    g := public.ingest_gate(sess, owner);
    IF jsonb_typeof(g->'consent') IS DISTINCT FROM 'null' THEN
        RAISE EXCEPTION 'a caller with no consent row got %', g->'consent';
    END IF;
    IF jsonb_typeof(public.ingest_gate(gen_random_uuid(), uid)->'session') IS DISTINCT FROM 'null' THEN
        RAISE EXCEPTION 'an unknown session came back as a row';
    END IF;
    SELECT array_agg(k ORDER BY k) INTO keys
      FROM jsonb_object_keys(public.ingest_gate(sess, uid)->'session') k;
    IF keys IS DISTINCT FROM ARRAY['ended_at', 'started_at', 'user_id'] THEN
        RAISE EXCEPTION 'ingest_gate session keys are %', keys;
    END IF;
    DELETE FROM public.signal_consent WHERE user_id = uid;
    DELETE FROM public.sessions WHERE id = sess;
END $$;

-- ── viewer_relationship: teacher of the student's class, linked parent, or admin ──
DO $$
DECLARE
    kid uuid := gen_random_uuid(); classmate uuid := gen_random_uuid();
    teacher uuid := gen_random_uuid(); other_teacher uuid := gen_random_uuid();
    parent uuid := gen_random_uuid(); other_parent uuid := gen_random_uuid();
    admin uuid := gen_random_uuid();
    cls uuid := gen_random_uuid(); other_cls uuid := gen_random_uuid();
    r record; bad text; got text; raised text;
BEGIN
    INSERT INTO auth.users (id, email)
    SELECT u, u::text || '@relationship.test.invalid'
      FROM unnest(ARRAY[kid, classmate, teacher, other_teacher, parent, other_parent, admin]) u;
    INSERT INTO public.profiles (id, email, role)
    SELECT u, u::text || '@relationship.test.invalid', 'student'
      FROM unnest(ARRAY[kid, classmate, teacher, other_teacher, parent, other_parent, admin]) u
    ON CONFLICT (id) DO NOTHING;
    -- Roles written directly: sign-up grants only the self-service ones.
    UPDATE public.profiles SET role = 'teacher' WHERE id IN (teacher, other_teacher);
    UPDATE public.profiles SET role = 'parent' WHERE id IN (parent, other_parent);
    UPDATE public.profiles SET role = 'admin' WHERE id = admin;
    INSERT INTO public.classes (id, teacher_id, name, join_code) VALUES
        (cls, teacher, 'rel-a', 'RELA0001'), (other_cls, other_teacher, 'rel-b', 'RELB0001');
    INSERT INTO public.class_memberships (class_id, student_id) VALUES
        (cls, kid), (cls, classmate), (other_cls, classmate);
    INSERT INTO public.parent_child_links (parent_id, child_id) VALUES
        (parent, kid), (other_parent, classmate);

    FOR r IN
        SELECT e.label, e.expected, public.viewer_relationship(e.viewer::text, kid::text) AS got
          FROM (VALUES (teacher, 'the teacher of their class', 'teacher'),
                       (other_teacher, 'a teacher of another class', NULL),
                       (parent, 'their linked parent', 'parent'),
                       (other_parent, 'a classmate''s parent', NULL),
                       (admin, 'an admin', 'admin'),
                       (classmate, 'a classmate', NULL),
                       (kid, 'themself (main.py decides self)', NULL)) e(viewer, label, expected)
    LOOP
        IF r.got IS DISTINCT FROM r.expected THEN
            RAISE EXCEPTION 'viewer_relationship for % is %, expected %', r.label, r.got, r.expected;
        END IF;
    END LOOP;
    -- Any spelling the uuid cast accepts still resolves, as it did when the parameters were uuid.
    IF public.viewer_relationship(upper(teacher::text), upper(kid::text)) IS DISTINCT FROM 'teacher' THEN
        RAISE EXCEPTION 'an upper-case uuid lost the teacher relationship';
    END IF;
    -- A path id that is no uuid is no relationship, never an error; the admin row would match anyone.
    FOR bad IN SELECT unnest(ARRAY['not-a-uuid', '', kid::text || '0', '1 OR 1=1']) LOOP
        raised := NULL;
        BEGIN
            got := public.viewer_relationship(admin::text, bad);
        EXCEPTION WHEN OTHERS THEN
            raised := SQLSTATE || ': ' || SQLERRM;
        END;
        IF raised IS NOT NULL THEN
            RAISE EXCEPTION 'viewer_relationship raised % for student %', raised, quote_literal(bad);
        END IF;
        IF got IS NOT NULL THEN
            RAISE EXCEPTION 'viewer_relationship answered % for student %', got, quote_literal(bad);
        END IF;
    END LOOP;
    raised := NULL;
    BEGIN
        got := public.viewer_relationship('not-a-uuid', kid::text);
    EXCEPTION WHEN OTHERS THEN
        raised := SQLSTATE || ': ' || SQLERRM;
    END;
    IF raised IS NOT NULL OR got IS NOT NULL THEN
        RAISE EXCEPTION 'a viewer that is no uuid got % (raised %)', got, raised;
    END IF;
    DELETE FROM public.parent_child_links WHERE child_id IN (kid, classmate);
    DELETE FROM public.class_memberships WHERE class_id IN (cls, other_cls);
    DELETE FROM public.classes WHERE id IN (cls, other_cls);
END $$;

-- ── record_answer: refused before any write; a failed step is returned, never undoes the answer ──
DO $$
DECLARE
    uid uuid := gen_random_uuid(); other uuid := gen_random_uuid();
    sess uuid := gen_random_uuid(); ended_sess uuid := gen_random_uuid();
    q uuid := gen_random_uuid(); nq uuid := gen_random_uuid();
    res jsonb; n int; srow record; original text; raised text;
BEGIN
    INSERT INTO auth.users (id, email) VALUES
        (uid, 'record-answer@test.invalid'), (other, 'record-answer-other@test.invalid');
    INSERT INTO public.math_topics (topic_name) VALUES ('assert-record-answer-topic');
    INSERT INTO public.questions (id, subject, question_text)
    VALUES (q, 'assert-record-answer-topic', 'three plus four'),
           (nq, 'assert-record-answer-no-topic', 'two plus two');
    INSERT INTO public.sessions (id, user_id, started_at) VALUES (sess, uid, '2030-01-01T09:00:00Z');
    INSERT INTO public.sessions (id, user_id, started_at, ended_at)
    VALUES (ended_sess, uid, '2030-01-01T08:00:00Z', '2030-01-01T08:30:00Z');

    res := public.record_answer(gen_random_uuid(), uid, q, 0, true, '2030-01-01T09:01:00Z');
    IF res->>'status' IS DISTINCT FROM 'not_found' THEN
        RAISE EXCEPTION 'an unknown session answered %', res;
    END IF;
    res := public.record_answer(sess, other, q, 0, true, '2030-01-01T09:01:00Z');
    IF res->>'status' IS DISTINCT FROM 'forbidden' OR res->>'owner' IS DISTINCT FROM uid::text THEN
        RAISE EXCEPTION 'another student''s session answered %', res;
    END IF;
    res := public.record_answer(ended_sess, uid, q, 0, true, '2030-01-01T09:01:00Z');
    IF res->>'status' IS DISTINCT FROM 'ended' THEN
        RAISE EXCEPTION 'an ended session answered %', res;
    END IF;
    SELECT count(*) INTO n FROM public.session_answers WHERE session_id IN (sess, ended_sess);
    IF n <> 0 OR EXISTS (SELECT 1 FROM public.sessions WHERE id IN (sess, ended_sess)
                          AND questions_answered <> 0) THEN
        RAISE EXCEPTION 'a refused answer wrote something: % answers', n;
    END IF;

    res := public.record_answer(sess, uid, q, 2, true, '2030-01-01T09:01:00Z');
    IF res IS DISTINCT FROM '{"status": "ok", "topic": "assert-record-answer-topic",
                              "topic_error": null, "counters_error": null}'::jsonb THEN
        RAISE EXCEPTION 'a first answer returned %', res;
    END IF;
    PERFORM public.record_answer(sess, uid, q, 1, false, '2030-01-01T09:02:00Z');
    SELECT questions_answered, correct_answers INTO srow FROM public.sessions WHERE id = sess;
    IF srow.questions_answered <> 2 OR srow.correct_answers <> 1 THEN
        RAISE EXCEPTION 'counters are %/%, expected 2/1', srow.questions_answered, srow.correct_answers;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.session_answers WHERE session_id = sess AND user_id = uid
                     AND question_id = q AND selected_index = 2 AND correct
                     AND answered_at = '2030-01-01T09:01:00Z') THEN
        RAISE EXCEPTION 'the answer row was not written as given';
    END IF;
    IF (SELECT attempted_questions || '/' || correct_questions FROM public.user_math_performance p
          JOIN public.math_topics t ON t.id = p.topic_id
         WHERE p.user_id = uid AND t.topic_name = 'assert-record-answer-topic') IS DISTINCT FROM '2/1' THEN
        RAISE EXCEPTION 'the topic record does not hold the two attempts';
    END IF;

    -- A failing attribution: the answer and its counters stand; the topic is null, the error named.
    original := pg_get_functiondef('public.record_topic_attempt(uuid, uuid, boolean)'::regprocedure);
    EXECUTE $f$CREATE OR REPLACE FUNCTION public.record_topic_attempt(
                   p_user_id uuid, p_question_id uuid, p_correct boolean)
               RETURNS text LANGUAGE plpgsql AS $b$ BEGIN RAISE EXCEPTION 'topic down'; END $b$ $f$;
    res := public.record_answer(sess, uid, q, 0, true, '2030-01-01T09:03:00Z');
    EXECUTE original;
    IF res IS DISTINCT FROM '{"status": "ok", "topic": null, "topic_error": "P0001: topic down",
                              "counters_error": null}'::jsonb THEN
        RAISE EXCEPTION 'an answer with a failed attribution returned %', res;
    END IF;
    SELECT questions_answered, correct_answers INTO srow FROM public.sessions WHERE id = sess;
    IF (SELECT count(*) FROM public.session_answers WHERE session_id = sess) <> 3
       OR srow.questions_answered <> 3 OR srow.correct_answers <> 2 THEN
        RAISE EXCEPTION 'a failed attribution undid the answer: counters %/%',
            srow.questions_answered, srow.correct_answers;
    END IF;

    -- A question with no topic is not a failure: both null.
    res := public.record_answer(sess, uid, nq, 1, false, '2030-01-01T09:04:00Z');
    IF res IS DISTINCT FROM '{"status": "ok", "topic": null, "topic_error": null,
                              "counters_error": null}'::jsonb THEN
        RAISE EXCEPTION 'an answer to a question with no topic returned %', res;
    END IF;

    -- A failing counter bump goes through bump_session_counters and is returned, not raised.
    original := pg_get_functiondef('public.bump_session_counters(uuid, boolean)'::regprocedure);
    EXECUTE $f$CREATE OR REPLACE FUNCTION public.bump_session_counters(p_session_id uuid, p_correct boolean)
               RETURNS TABLE (questions_answered integer, correct_answers integer)
               LANGUAGE plpgsql AS $b$ BEGIN RAISE EXCEPTION 'counters down'; END $b$ $f$;
    raised := NULL;
    BEGIN
        res := public.record_answer(sess, uid, q, 0, true, '2030-01-01T09:05:00Z');
    EXCEPTION WHEN OTHERS THEN
        raised := SQLSTATE || ': ' || SQLERRM;
    END;
    EXECUTE original;
    IF raised IS NOT NULL THEN
        RAISE EXCEPTION 'a failed counter bump undid the answer: %', raised;
    END IF;
    IF res IS DISTINCT FROM '{"status": "ok", "topic": "assert-record-answer-topic", "topic_error": null,
                              "counters_error": "P0001: counters down"}'::jsonb THEN
        RAISE EXCEPTION 'an answer with a failed counter bump returned %', res;
    END IF;
    SELECT questions_answered, correct_answers INTO srow FROM public.sessions WHERE id = sess;
    IF (SELECT count(*) FROM public.session_answers WHERE session_id = sess) <> 5
       OR srow.questions_answered <> 4 OR srow.correct_answers <> 2 THEN
        RAISE EXCEPTION 'after a failed bump: % answers, counters %/%, expected 5 and 4/2',
            (SELECT count(*) FROM public.session_answers WHERE session_id = sess),
            srow.questions_answered, srow.correct_answers;
    END IF;
    IF (SELECT attempted_questions || '/' || correct_questions FROM public.user_math_performance p
          JOIN public.math_topics t ON t.id = p.topic_id
         WHERE p.user_id = uid AND t.topic_name = 'assert-record-answer-topic') IS DISTINCT FROM '3/2' THEN
        RAISE EXCEPTION 'a failed counter bump skipped the topic attempt';
    END IF;

    -- A missing helper is 42883, the code main.py names; the subtransaction undoes the drops.
    raised := NULL;
    res := NULL;
    BEGIN
        DROP FUNCTION public.bump_session_counters(uuid, boolean);
        DROP FUNCTION public.record_topic_attempt(uuid, uuid, boolean);
        res := public.record_answer(sess, uid, q, 0, true, '2030-01-01T09:06:00Z');
        RAISE EXCEPTION 'undo the drops';
    EXCEPTION WHEN OTHERS THEN
        raised := SQLERRM;
    END;
    IF raised IS DISTINCT FROM 'undo the drops' THEN
        RAISE EXCEPTION 'record_answer without its helpers raised %', raised;
    END IF;
    IF res->>'status' IS DISTINCT FROM 'ok'
       OR (res->>'counters_error' LIKE '42883: %') IS NOT TRUE
       OR (res->>'topic_error' LIKE '42883: %') IS NOT TRUE THEN
        RAISE EXCEPTION 'record_answer without its helpers returned %', res;
    END IF;
    DELETE FROM public.sessions WHERE id IN (sess, ended_sess);
    DELETE FROM public.user_math_performance WHERE user_id = uid;
END $$;


-- ─── ops_counters_add: two flushes into one cell add n and sum, keep the larger max ──
DO $$
DECLARE
    c record;
BEGIN
    PERFORM public.ops_counters_add(
        '[{"hour":"2000-01-01T10:00:00+00:00","kind":"t","key":"k","n":2,"sum":30,"max":20},
          {"hour":"2000-01-01T10:00:00+00:00","kind":"t","key":"plain","n":1,"sum":null,"max":null}]');
    PERFORM public.ops_counters_add(
        '[{"hour":"2000-01-01T10:00:00+00:00","kind":"t","key":"k","n":3,"sum":5,"max":4},
          {"hour":"2000-01-01T10:00:00+00:00","kind":"t","key":"plain","n":4,"sum":null,"max":null}]');

    SELECT "n", "sum", "max" INTO c FROM public.ops_counters WHERE "kind" = 't' AND "key" = 'k';
    IF c.n <> 5 OR c.sum <> 35 OR c.max <> 20 THEN
        RAISE EXCEPTION 'ops_counters_add merged a measured cell into %', c;
    END IF;
    SELECT "n", "sum", "max" INTO c FROM public.ops_counters WHERE "kind" = 't' AND "key" = 'plain';
    IF c.n <> 5 OR c.sum IS NOT NULL OR c.max IS NOT NULL THEN
        RAISE EXCEPTION 'ops_counters_add merged a plain count into %', c;
    END IF;

    IF (public.expire_ops_counters()->>'deleted')::int < 2
       OR EXISTS (SELECT 1 FROM public.ops_counters WHERE "kind" = 't') THEN
        RAISE EXCEPTION 'expire_ops_counters left a cell older than 90 days';
    END IF;
END $$;

-- Nothing here should persist; the assertions are the product.
ROLLBACK;
