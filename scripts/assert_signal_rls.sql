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

-- ── the SELECT grant exists, asserted separately ────────────────────────────
-- So the zero-rows assertion below can only be about RLS, not a missing grant.

DO $$
BEGIN
    IF NOT has_table_privilege('authenticated', 'public.heart_signals', 'SELECT') THEN
        RAISE EXCEPTION
            'authenticated lacks SELECT on heart_signals -- the RLS assertion '
            'below would pass for the wrong reason';
    END IF;
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

    -- Impersonate an unrelated logged-in user.
    SET LOCAL ROLE authenticated;
    PERFORM set_config('request.jwt.claims',
                       json_build_object('sub', other_id, 'role', 'authenticated')::text,
                       true);

    SELECT count(*) INTO visible FROM public.heart_signals;
    IF visible <> 0 THEN
        RAISE EXCEPTION 'an unrelated authenticated user saw % heart rows', visible;
    END IF;

    -- The owner *can* see it; with no policy at all the stranger check would pass too.
    PERFORM set_config('request.jwt.claims',
                       json_build_object('sub', owner_id, 'role', 'authenticated')::text,
                       true);
    SELECT count(*) INTO visible FROM public.heart_signals;
    IF visible = 0 THEN
        RAISE EXCEPTION
            'the owner cannot see their own heart rows -- the RLS policy is '
            'missing or too strict, and the stranger check above is therefore '
            'passing for the wrong reason';
    END IF;

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
-- SELECT-only grant and policy, as heart_signals: an own-row policy is not consent.

DO $$
BEGIN
    IF NOT has_table_privilege('authenticated', 'public.face_signals', 'SELECT') THEN
        RAISE EXCEPTION
            'authenticated lacks SELECT on face_signals -- the RLS assertion '
            'below would pass for the wrong reason';
    END IF;
    IF NOT has_table_privilege('authenticated', 'public.cognitive_signals', 'SELECT') THEN
        RAISE EXCEPTION
            'authenticated lacks SELECT on cognitive_signals -- the RLS '
            'assertion below would pass for the wrong reason';
    END IF;
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

    -- Impersonate an unrelated logged-in user.
    SET LOCAL ROLE authenticated;
    PERFORM set_config('request.jwt.claims',
                       json_build_object('sub', other_id, 'role', 'authenticated')::text,
                       true);

    SELECT count(*) INTO visible FROM public.face_signals;
    IF visible <> 0 THEN
        RAISE EXCEPTION 'an unrelated authenticated user saw % face rows', visible;
    END IF;
    SELECT count(*) INTO visible FROM public.cognitive_signals;
    IF visible <> 0 THEN
        RAISE EXCEPTION 'an unrelated authenticated user saw % cognitive rows', visible;
    END IF;

    -- The owner *can* see their own rows, as in the heart_signals block.
    PERFORM set_config('request.jwt.claims',
                       json_build_object('sub', owner_id, 'role', 'authenticated')::text,
                       true);
    SELECT count(*) INTO visible FROM public.face_signals;
    IF visible = 0 THEN
        RAISE EXCEPTION
            'the owner cannot see their own face rows -- the RLS policy is '
            'missing or too strict, and the stranger check above is therefore '
            'passing for the wrong reason';
    END IF;
    SELECT count(*) INTO visible FROM public.cognitive_signals;
    IF visible = 0 THEN
        RAISE EXCEPTION
            'the owner cannot see their own cognitive rows -- the RLS policy '
            'is missing or too strict, and the stranger check above is '
            'therefore passing for the wrong reason';
    END IF;

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

-- ── the rollup records the score scale, and a posted value cannot abort it ──
-- `raw` is client-supplied, so a bad score_scale must be skipped, not abort the day's rollup
-- (which would exempt its rows from expiry).
DO $$
DECLARE
    owner_id uuid;
    sess     uuid;
    lo       smallint;
    hi       smallint;
    n        int;
BEGIN
    SELECT i.owner_id, i.sess_id INTO owner_id, sess FROM _ids i;
    DELETE FROM cognitive_signals WHERE user_id = owner_id;
    DELETE FROM signal_daily_rollup WHERE user_id = owner_id;

    -- Four rows on one day: no key (predates the label: scale 1), scale 2, a
    -- string, and a null measurement carrying scale 3 that must not count.
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

    -- A scale-3 row with no stress contributed only a scale-2 focus; with a stress it is scale 3.
    INSERT INTO cognitive_signals (session_id, user_id, ts, focus, raw) VALUES
        (sess, owner_id, '2026-03-12T18:00:05Z', 0.5, '{"score_scale": 3}'::jsonb);
    PERFORM public.rollup_signal_day(owner_id, DATE '2026-03-12', 'UTC');
    SELECT score_scale_max INTO hi FROM signal_daily_rollup
     WHERE user_id = owner_id AND channel = 'cognitive';
    IF hi IS DISTINCT FROM 2 THEN
        RAISE EXCEPTION 'a scale-3 row with no stress reported scale %', hi;
    END IF;
    UPDATE cognitive_signals SET stress = 0.4
     WHERE user_id = owner_id AND ts = '2026-03-12T18:00:05Z';
    PERFORM public.rollup_signal_day(owner_id, DATE '2026-03-12', 'UTC');
    SELECT score_scale_max INTO hi FROM signal_daily_rollup
     WHERE user_id = owner_id AND channel = 'cognitive';
    IF hi IS DISTINCT FROM 3 THEN
        RAISE EXCEPTION 'a scale-3 row with a stress reported scale %', hi;
    END IF;

    -- An ordinary local session (held then scored calm) is one source: 3..3.
    -- A row with a NULL raw predates the label and anchors at 1.
    DELETE FROM cognitive_signals WHERE user_id = owner_id;
    INSERT INTO cognitive_signals (session_id, user_id, ts, focus, stress, raw) VALUES
        (sess, owner_id, '2026-03-12T18:00:01Z', 0.5, NULL, '{"score_scale": 3}'::jsonb),
        (sess, owner_id, '2026-03-12T18:00:02Z', 0.5, 0.4,  '{"score_scale": 3}'::jsonb);
    PERFORM public.rollup_signal_day(owner_id, DATE '2026-03-12', 'UTC');
    SELECT score_scale_min, score_scale_max INTO lo, hi FROM signal_daily_rollup
     WHERE user_id = owner_id AND channel = 'cognitive';
    IF lo IS DISTINCT FROM 3 OR hi IS DISTINCT FROM 3 THEN
        RAISE EXCEPTION 'a local session with a held then a scored calm reads %..%, '
                        'expected 3..3', lo, hi;
    END IF;
    UPDATE cognitive_signals SET raw = NULL
     WHERE user_id = owner_id AND ts = '2026-03-12T18:00:01Z';
    PERFORM public.rollup_signal_day(owner_id, DATE '2026-03-12', 'UTC');
    SELECT score_scale_min INTO lo FROM signal_daily_rollup
     WHERE user_id = owner_id AND channel = 'cognitive';
    IF lo IS DISTINCT FROM 1 THEN
        RAISE EXCEPTION 'a row with a NULL raw reads scale % (expected 1: it predates the label)', lo;
    END IF;

    -- Pre-label rows beside held local rows mix scales 1 and 2: range 1..2.
    DELETE FROM cognitive_signals WHERE user_id = owner_id;
    INSERT INTO cognitive_signals (session_id, user_id, ts, focus, stress, raw) VALUES
        (sess, owner_id, '2026-03-12T18:00:01Z', 0.5, 0.4,  NULL),
        (sess, owner_id, '2026-03-12T18:00:02Z', 0.5, NULL, '{"score_scale": 3}'::jsonb);
    PERFORM public.rollup_signal_day(owner_id, DATE '2026-03-12', 'UTC');
    SELECT score_scale_min, score_scale_max INTO lo, hi FROM signal_daily_rollup
     WHERE user_id = owner_id AND channel = 'cognitive';
    IF lo IS DISTINCT FROM 1 OR hi IS DISTINCT FROM 2 THEN
        RAISE EXCEPTION 'pre-label rows beside held local rows read %..%, expected 1..2', lo, hi;
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

-- Nothing here should persist; the assertions are the product.
ROLLBACK;
