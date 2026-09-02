-- RLS, CHECK and column-shape assertions for the signal tables, run against a
-- real stack.
--
-- These cannot live in the backend test suite. Those tests drive `main.py` with
-- a fake Supabase client, which is the right shape for testing the *code* --
-- but RLS is enforced by Postgres, so a fake proves nothing about it. The
-- `Database migrations` CI job already stands up a local stack and applies
-- every migration; this runs there, which is the only place both halves exist.
--
-- Until now this verification was done by hand once and written up in a PR
-- description. That caught the state of the schema on one afternoon. It cannot
-- catch a later migration that re-widens a grant or drops a policy, and those
-- are the changes most likely to be made by someone who does not know the
-- history -- which is the whole reason the rules are written down.
--
-- Every assertion raises rather than returning a row, so a failure fails the
-- job instead of scrolling past in the log.

BEGIN;

-- ── column shape ────────────────────────────────────────────────────────────
--
-- First, and deliberately: these need none of the fixtures below and must not
-- be gated behind them. `ON_ERROR_STOP=1` aborts the whole script on the first
-- failure, so with these at the bottom a broken fixture INSERT -- a new NOT
-- NULL on profiles, say -- would take the cheapest and most robust checks in
-- the file down with it, and the schema would go unverified while the job
-- failed for an unrelated reason.
--
-- Not RLS, but the same argument: a rule that exists only in prose is enforced
-- by nobody. These are about which columns are *supposed* to be there, in both
-- directions, and Postgres is the only place that question can be answered.
--
-- The Python side has a matching guard
-- (`test_the_three_unproduced_face_columns_are_kept_on_purpose`), and it checks
-- the mapper rather than the database -- so a migration dropping these columns
-- would sail past it. This is the half that catches that.

DO $$
DECLARE
    missing text;
    present int;
BEGIN
    -- attention, gaze_x and gaze_y have no producer and must survive anyway.
    --
    -- They are mechanically indistinguishable from identity_confidence, which
    -- was just retired: unwritten since 20260625000000, rendering as "No
    -- sensor" on every surface. What separates them is a decision -- identity
    -- was out of scope (#86), these are waiting on the landmark model that is
    -- Phase 11 -- and a cleanup that could not see the difference would drop
    -- all four and be green everywhere else.
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

    -- And the retired one stays retired. Asserted in the opposite direction
    -- because the failure it catches is not a bad migration but a bad
    -- *environment*: a rollback, or a stack rebuilt from a dump predating
    -- 20260812000000, would restore the column silently. The backend no longer
    -- writes it, so it would sit there NULL and re-open the two-confidence
    -- ambiguity that signal_fusion.face_channel documents having been bitten by.
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

-- The three signal tables each carry a unique key, so a replayed ingest batch
-- is a no-op rather than a second copy of every sample.
--
-- Asserted here because the writers cannot show it: the backend suite drives
-- `main.py` with a fake client, so it proves the endpoints *ask* for
-- `ON CONFLICT` and not that anything enforces it. Postgres is the only place
-- that question can be answered, and the answer is what stands between a
-- flaky connection and a permanently wrong average -- a duplicate row is not
-- an error, appears on no dashboard, and is carried into the rollup that
-- outlives the raw rows.
--
-- Dropping one of these would leave every writer's `on_conflict` silently
-- inert, which is the shape worth failing CI over.
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

-- Every column `student_sessions` filters an activity read on, checked against
-- the live schema and scoped to its own table.
--
-- `main._ACTIVITY_SOURCES` names these, and the filter is applied server-side:
-- a column that is not there makes PostgREST reject the request, the endpoint
-- catches it, and `activity_known` goes False for every session with `idle`
-- following -- which puts the pulsing LIVE badge back on a teacher's screen
-- for a student who has gone home. Silent, and green everywhere.
--
-- This is the only check on these columns, and that is deliberate. A Python
-- guard replaying the migrations used to sit beside it and was retired: it
-- was a regex over SQL text and was wrong twice in consecutive commits --
-- first matching a quoted name *anywhere* in the concatenated files, so a
-- column appearing only in a `DROP COLUMN` counted as present
-- (`identity_confidence`, 20260812000000, would have passed); then reading
-- one clause per `ALTER TABLE`, so two of the three columns added by
-- 20260820000000 went unseen. Both were green, and for the same reason:
-- `_ACTIVITY_SOURCES` happens to name the column that survived each bug.
--
-- Text is the wrong thing to ask. `information_schema` is the schema, and
-- reading it here costs nothing that the job was not already paying.
--
-- Deliberately not derived from a list in the backend: this file is the
-- independent statement of what must be true, and a check that imported its
-- own expectations would agree with itself.
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
--
-- heart_signals.user_id -> profiles.id -> auth.users.id, and session_id ->
-- sessions.id. Random UUIDs therefore fail on the foreign key long before they
-- reach the CHECK constraints being tested, so the chain has to be real. The
-- whole script runs in a transaction that rolls back, so none of this persists.

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
--
-- Constrained rather than free text: a typo in a source name would not fail, it
-- would silently create a fourth source that no consent rule covers and no
-- reader knows about.

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
--
-- Without this, the zero-rows assertion below is ambiguous. It passes when RLS
-- correctly filters every row, and it would also fail -- loudly but with a
-- confusing message about privileges -- if the grant were missing entirely.
-- Those are opposite problems: one is the policy working, the other is the
-- table being unreachable. Checking the grant first means the assertion below
-- can only be about RLS.

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
--
-- The service-role client in main.py bypasses RLS, so this is not what protects
-- the product API -- the relationship checks there are. It is what protects
-- anything reaching the table with an ordinary JWT, including PostgREST with
-- the anon key that ships in the frontend bundle.

DO $$
DECLARE
    owner_id  uuid;
    other_id  uuid;
    sess      uuid;
    visible   int;
BEGIN
    SELECT i.owner_id, i.other_id, i.sess_id
      INTO owner_id, other_id, sess FROM _ids i;

    -- An explicit, distinct ts. `now()` is the *transaction* timestamp and is
    -- therefore identical to the one the dedupe block above used, so relying on
    -- the column default collides with that row on the very key this script
    -- asserts. Found by running it; the constraint was doing its job.
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

    -- And the owner *can* see it. This half is what makes the half above mean
    -- anything: with RLS on and no policy at all, SELECT returns zero rows, so
    -- "the stranger sees nothing" passes just as happily when the policy has
    -- been dropped as when it is working. Verified by dropping both policies
    -- and watching this script still pass -- it did, until this check existed.
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

    -- And cannot write one either: with no INSERT policy, the command is denied
    -- whatever the grant says.
    BEGIN
        INSERT INTO public.heart_signals (session_id, user_id, source, ts)
        VALUES (sess, other_id, 'muse_optics', now() + interval '2 minutes');
        RAISE EXCEPTION 'an authenticated user inserted a heart row';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;

    RESET ROLE;
END $$;

-- ── the same, for the consent table that governs all of it ──────────────────
--
-- signal_consent has no insert/update/delete policy for anyone, deliberately:
-- "off-direction only" is not expressible as a WITH CHECK, because a policy
-- cannot see the previous row. The backend is the enforcement, and this asserts
-- the table stays unwritable underneath it.

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
--
-- These two shipped with a bare FOR ALL "own" policy and a full DML grant to
-- authenticated -- unlike heart_signals, which was built later and knowingly
-- did not repeat it. #47: "is this my row" is not "have I consented", and with
-- no WITH CHECK the FOR ALL policy let an authenticated client insert straight
-- past signal_consent. 20260811000000 narrows the grant to SELECT and the
-- policy to FOR SELECT, matching heart_signals exactly, so these three blocks
-- mirror the heart_signals ones above rather than introducing a new shape.

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
    -- The sequence USAGE grant 20260805110000 gave authenticated to insert
    -- into these two tables has no reason to survive 20260811000000 revoking
    -- the INSERT it existed for. Not RLS -- a leftover grant a later
    -- migration could otherwise silently re-justify by adding INSERT back
    -- without anyone noticing USAGE had quietly been there the whole time.
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

    -- And the owner *can* see their own rows -- the half that makes the half
    -- above mean anything; see the identical note on the heart_signals block.
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

    -- And cannot write one either -- this is the gap #47 closes. Before
    -- 20260811000000 both of these INSERTs succeeded: the bare FOR ALL policy
    -- checked only "is this my own row", never consent.
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
--
-- The one job here that destroys data, so its refusal is asserted rather than
-- trusted: a day with no rollup row must survive. Without that, a bug in the
-- rollup writer turns into silent permanent loss on a fixed date, and the rows
-- it takes are the only copy.

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

-- An unconfigured window deletes nothing. Same fail-closed direction as the
-- recording gate: a school that has not said when its year runs does not have
-- data that has expired.
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

-- The same refusal, on all three tables rather than one. The per-table logic is
-- generated from one loop body, so this is cheap -- but "generated identically"
-- is an argument about the code, and the point of an assertion is not to take
-- that argument on trust. A typo in the channel mapping (`face_signals` ->
-- 'emotion' is the one that does not match its table name) would leave one
-- table deleting against a rollup that never matches, which reads as "nothing
-- expired" rather than as an error.
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

-- The batching loop, which is the one piece of new mechanics with no other
-- assertion. Two things to establish: that it *iterates* rather than deleting
-- one batch and stopping, and that the cap is real and reports itself.
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

    -- Distinct stamps, which `cog_session_ts_key` (20260914000000) now
    -- requires: five rows sharing one `(session_id, ts)` was legal when this
    -- fixture was written and is a `unique_violation` since. Unhandled in an
    -- anonymous code block under `ON_ERROR_STOP=1`, that fails the whole job
    -- and takes every assertion below it with it.
    --
    -- (Written without the dollar-quote marker on purpose: naming it inside
    -- one of these blocks closes the block, which is a syntax error two
    -- hundred lines later. This comment cost exactly that once.)
    --
    -- Seconds apart, not minutes: all five stay on one school day in
    -- America/Los_Angeles, so the day bucketing these assertions rest on is
    -- unchanged and only the batching is being measured. Same shape the
    -- heart_signals fixtures have used since that table got its own key.
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

    -- And the cap stops it, visibly. Without `hit_batch_cap` this state is
    -- indistinguishable from "nothing was eligible".
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
--
-- The objects are pictures of a named child's cognitive and physiological
-- signals, and they outlive the rows they are drawn from -- the end-of-year
-- delete above takes the per-sample detail and leaves these. So this is the
-- copy with the longest exposure, and the two things guarding it are both
-- database state that no test with a fake client can see.

DO $$
DECLARE
    owner_id       uuid;
    other_id       uuid;
    is_public      boolean;
    visible        int;
    policies       int;
    -- A separate name from the `owner_id` column on storage.objects itself:
    -- referencing the bare variable inside a query against that table is
    -- ambiguous to plpgsql, which cannot tell a column from a variable of the
    -- same name once both are in scope.
    fixture_owner  text;
BEGIN
    SELECT i.owner_id, i.other_id INTO owner_id, other_id FROM _ids i;
    fixture_owner := owner_id::text;

    -- 1. The bucket is private. This one is not an RLS property and cannot be
    --    asserted through storage.objects at all: a public bucket serves every
    --    object over HTTP to anyone holding the URL, without a row-level check
    --    ever running. And a URL travels -- once one is pasted into a message,
    --    no policy added later can un-share it.
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

    -- 2. No policy on storage.objects. With RLS on and nothing granting a role
    --    anything, every command is denied for everyone who is not BYPASSRLS --
    --    which is service_role, which is the backend, which is the only reader
    --    and the only writer. A permissive policy for `authenticated` added
    --    later would be a second access path that has to agree with
    --    `_verify_can_view_student` forever, for no caller that exists.
    SELECT count(*) INTO policies
      FROM pg_policy WHERE polrelid = 'storage.objects'::regclass;
    IF policies <> 0 THEN
        RAISE EXCEPTION
            'storage.objects has % policy/policies -- charts are now reachable '
            'without going through the signed-URL endpoint', policies;
    END IF;

    INSERT INTO storage.objects (bucket_id, name, owner_id)
    VALUES ('session-charts', owner_id || '/sess/heart_rate.svg', fixture_owner);

    -- The negative control the check below needs. With no policy, SELECT
    -- returns zero rows whether or not the object is there, so "the owner sees
    -- nothing" would pass just as happily against an empty table -- the same
    -- trap as the heart_signals block above, arriving from the other side.
    --
    -- Scoped to this fixture's own owner_id, not a bare count of the bucket. A
    -- real developer stack has already archived real charts by the time this
    -- runs, so an unscoped count is never 1 there -- it was written against a
    -- bucket assumed empty, which only CI's fresh database actually is. Real
    -- archive rows are written by the backend without an owner_id, so this
    -- fixture's own value cannot collide with one.
    SELECT count(*) INTO visible
      FROM storage.objects o
     WHERE o.bucket_id = 'session-charts' AND o.owner_id = fixture_owner;
    IF visible <> 1 THEN
        RAISE EXCEPTION 'the fixture object was not stored; the checks below '
                        'would pass for the wrong reason';
    END IF;

    SET LOCAL ROLE authenticated;

    -- 3. Not even the student the object is *about*. This is the one place the
    --    archive deliberately differs from the tables it summarises: those
    --    carry a read-your-own policy, and these do not, because an object is
    --    fetched by URL rather than filtered by a query -- so the access
    --    decision has to happen in the backend, where the relationship checks
    --    live, and be handed out as a short-lived signed URL.
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

    -- And cannot write one. An attacker-supplied SVG under a student's prefix
    -- would be served by the signed-URL endpoint as that student's chart.
    BEGIN
        INSERT INTO storage.objects (bucket_id, name)
        VALUES ('session-charts', other_id || '/sess/emotion_pie.svg');
        RAISE EXCEPTION 'an authenticated user wrote a chart object';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;

    RESET ROLE;
END $$;

-- `sessions.chart_paths` has no default, and that is load-bearing rather than
-- an omission. `'{}'::jsonb` would claim every session closed before Phase 8 was
-- archived and found nothing to draw -- an absence reported as data, which is
-- the failure this schema has spent nine phases avoiding. Column-NULL means the
-- archive never ran; `{"heart_rate": null, ...}` means it ran and that channel
-- had nothing.
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
--
-- The table has a `sessions: own` policy with no `FOR` clause, so `FOR ALL`.
-- That is fine as long as the *grant* is SELECT: RLS narrows which rows a
-- command touches, never which commands exist. With `authenticated=arwd` on
-- top of it -- which is what Supabase's default privileges hand out, and what
-- this table carried until `20260817000000` -- a student could rewrite any
-- column of their own sessions through PostgREST.
--
-- `chart_paths` is the sharp end and the reason this was found: a path pointed
-- at another child's chart object, then signed by an endpoint that had just
-- correctly confirmed the caller owns the session. The endpoint no longer
-- trusts it, but `started_at`/`ended_at` drive the rollup's day bucketing and
-- the expiry cutoff, and a DELETE here cascades all three signal tables.

DO $$
DECLARE
    owner_id uuid;
    sess     uuid;
BEGIN
    SELECT i.owner_id, i.sess_id INTO owner_id, sess FROM _ids i;

    IF has_table_privilege('anon', 'public.sessions', 'SELECT') THEN
        RAISE EXCEPTION 'anon holds SELECT on sessions';
    END IF;

    -- The half that keeps the rest honest: the grant is narrowed, not removed,
    -- so a check for "cannot write" must not be passing because the role cannot
    -- reach the table at all.
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

    -- And it holds in practice, not just in the ACL. A student updating their
    -- *own* row is exactly what the FOR ALL policy permits, so this fails on
    -- the privilege or it does not fail at all.
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
--
-- Runs last, because it deletes the fixture rows every block above depends on.
--
-- The property with no recovery is the one in the middle: erasing one heart
-- source must leave the other standing, and must leave that survivor's day
-- average correct. Everything else here is a delete, which is easy to get
-- right; that one is a delete plus a recomputation, and the recomputation is
-- the half a reviewer would not think to check.

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

    -- One day, both heart sources, deliberately far apart in value so an
    -- average computed over the wrong set is unmistakable rather than close.
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

    -- The one that matters. A parent erasing the camera has said nothing about
    -- the headband, and a delete keyed on the table rather than the source
    -- would take both.
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

    -- And the survivor's average is *recomputed*, not merely left alone.
    -- `rollup_signal_day` has `HAVING count(*) > 0` on every channel, so it
    -- cannot correct a stale row by itself -- deleting first is what makes the
    -- rebuild a recomputation. If this reads 95 the erased readings are still
    -- being published, in the copy designed to outlive the rows.
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

    -- The tombstone. Without it an erased term is indistinguishable from one
    -- where the sensor was never worn.
    SELECT count(*) INTO n FROM signal_erasure
     WHERE user_id = owner_id AND channel = 'camera' AND erased_by = other_id;
    IF n <> 1 THEN RAISE EXCEPTION 'no tombstone recorded for the erasure'; END IF;

    IF (result->>'face_signals')::int <> 4 THEN
        RAISE EXCEPTION 'reported % face rows deleted, expected 4',
            result->>'face_signals';
    END IF;

    -- Erasing the other source now empties the channel, and the rollup row for
    -- it has to go rather than linger at the surviving average of nothing.
    PERFORM public.erase_signals(owner_id, 'headband_optical', other_id, 'UTC');
    SELECT count(*) INTO n FROM signal_daily_rollup
     WHERE user_id = owner_id AND channel = 'heart';
    IF n <> 0 THEN
        RAISE EXCEPTION 'the heart rollup outlived every row it summarised';
    END IF;
END $$;

-- The function destroys a child's stored biometrics and takes the subject as a
-- parameter, so an ambient grant is a delete button for anyone's history.
-- `check_function_grants.py` matches by name and would catch a missing revoke
-- block; it cannot see that the grant is right on a real instance.
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

    -- The tombstone is readable and not writable. A client that could delete it
    -- could hide the erasure, which is worse than not recording one.
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
--
-- `face_signals` gained a second producer in Phase 11 step 2: the landmarker
-- writes `gaze_x`/`gaze_y`, and a row is written when *either* measurement
-- succeeds. So `count(*)` over the table stopped meaning "emotion samples".
--
-- Asserted here rather than in the backend suite because that suite drives
-- `main.py` with a fake client and this arithmetic is in Postgres. The
-- migration passing CI only proves the SQL *applies*; this is the only place it
-- runs.
DO $$
DECLARE
    owner_id uuid;
    sess     uuid;
    n        int;
BEGIN
    SELECT i.owner_id, i.sess_id INTO owner_id, sess FROM _ids i;

    DELETE FROM face_signals WHERE user_id = owner_id;
    DELETE FROM signal_daily_rollup WHERE user_id = owner_id;

    -- Three rows carrying an emotion, two carrying only a gaze. The second pair
    -- is what a window looks like when the landmarker read a face and FER+
    -- refused it -- an ordinary outcome, not an error.
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

    -- The day is still summarised. `expire_signal_rows` refuses to delete a day
    -- with no rollup row, so gating the row's existence on emotion would leave
    -- a gaze-only day's raw rows undeletable for ever.
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
--
-- The per-topic attribution moved out of `main.py` and into the database
-- (20260825000000), because in Python it was four sequential round trips on the
-- hottest path in the product and the last two of them were a read-modify-write
-- with no lock: two answers landing together both read the same counts and the
-- second write overwrote the first, losing attempts from the table the adaptive
-- engine reads to choose what to serve next.
--
-- The backend suite can no longer check the arithmetic -- it drives `main.py`
-- with a fake client, so what it can assert is that one call is made with the
-- right three arguments. This is the half that checks the counting, and it is
-- the only place the increment is exercised at all.

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

    -- First attempt creates the row, and answers with the topic *name* -- what
    -- the answer endpoint hands back to the page so it can move one figure
    -- rather than re-reading the whole table.
    got := public.record_topic_attempt(usr, q1, true);
    IF got IS DISTINCT FROM 'assert-rls-topic' THEN
        RAISE EXCEPTION 'the topic was resolved as % rather than assert-rls-topic', got;
    END IF;

    -- Three more, so the increment is exercised against an existing row rather
    -- than only against the INSERT branch.
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

    -- One row, not one per attempt: the ON CONFLICT target has to match the
    -- (user_id, topic_id) unique constraint or every attempt inserts.
    SELECT count(*) INTO n FROM public.user_math_performance
     WHERE user_id = usr AND topic_id = topic;
    IF n <> 1 THEN
        RAISE EXCEPTION '% rows for one student and one topic', n;
    END IF;

    -- A subject with no `math_topics` row records nothing and invents nothing.
    -- Inventing a topic here would put a subject in the table that the question
    -- generator cannot pick from.
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
--
-- `student_signal_summary_many` is a LATERAL fan-out over
-- `student_signal_summary` (20260824040000). Before that they were two
-- independent copies of the same six averages and four counts, which is how
-- 20260823000000 came to fix `count(f.attention)` -> `count(f.emotion)` twice by
-- hand. Delegation only helps while it *is* delegation, and the cheapest way for
-- it to stop being so is a future edit that "optimises" the batch back into its
-- own query.
--
-- So this asserts the property the migration claims: same student, same
-- arguments, same answer. It also checks the channel gates still reach the
-- inner body -- `p_include_heart`/`p_include_emotion` gate the *read*, and a
-- fan-out that nulled excluded columns on the way out instead would satisfy
-- every value assertion while quietly reading rows a parent opted out of.
--
-- Its own student, deliberately. Sharing `owner_id` would make these counts
-- depend on which of the blocks above happened to leave rows inside the
-- seven-day window, so the assertion would pass or fail on the calendar.

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
    -- And the cognitive channel is *not* gated by those flags -- it has no
    -- opt-out on the aggregate, which is why the payload calls its consent
    -- state `eeg_enabled` rather than `eeg_included`.
    IF many.cognitive_samples <> 4 THEN
        RAISE EXCEPTION 'the cognitive channel was gated by a flag that does '
                        'not apply to it: %', many;
    END IF;
END $$;

-- Nothing here should persist; the assertions are the product.
ROLLBACK;
