-- RLS and CHECK assertions for the signal tables, run against a real stack.
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

-- Nothing here should persist; the assertions are the product.
ROLLBACK;
