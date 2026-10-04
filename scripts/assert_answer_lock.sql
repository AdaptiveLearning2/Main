-- record_answer's row lock, across real connections: a close cannot land between its check and its
-- write, and two answers to one session queue rather than deadlock. Fixtures commit, then are deleted.
-- dblink connects without a password only for a superuser, hence supabase_admin:
--   docker exec -i supabase_db_AdaptiveLearning psql -U supabase_admin -d postgres \
--     -v ON_ERROR_STOP=1 -f - < scripts/assert_answer_lock.sql

BEGIN;
SET LOCAL lock_timeout = '10s';
-- Rolled back with this transaction; only this session calls dblink.
CREATE EXTENSION IF NOT EXISTS dblink;

-- True once p_pid waits on a lock; false after 10 s.
CREATE FUNCTION pg_temp.answer_lock_waits(p_pid int) RETURNS boolean LANGUAGE plpgsql AS $w$
DECLARE
    t0 timestamptz := clock_timestamp();
BEGIN
    WHILE clock_timestamp() - t0 < interval '10 seconds' LOOP
        IF cardinality(pg_blocking_pids(p_pid)) > 0 THEN
            RETURN true;
        END IF;
        PERFORM pg_sleep(0.01);
    END LOOP;
    RETURN false;
END $w$;

DO $$
DECLARE
    conn text := format('dbname=%s user=postgres', current_database());
    marker text := 'answer-lock-%@assert.invalid';
    qtext text := 'assert answer lock';
    u uuid := gen_random_uuid(); s1 uuid := gen_random_uuid(); s2 uuid := gen_random_uuid();
    q uuid := gen_random_uuid();
    def text; c text; pid_a int; pid_b int; n bigint; t0 timestamptz;
    res_a jsonb; res_b jsonb; done_a boolean := false; done_b boolean := false;
    err text; err_state text;
BEGIN
    BEGIN
        -- The function as this transaction sees it, as a session-local copy in each answering connection.
        def := pg_get_functiondef(
            'public.record_answer(uuid, uuid, uuid, integer, boolean, timestamptz)'::regprocedure);
        IF position('FUNCTION public.record_answer(' IN def) = 0 THEN
            RAISE EXCEPTION 'record_answer''s definition does not name public.record_answer';
        END IF;
        def := replace(def, 'FUNCTION public.record_answer(', 'FUNCTION pg_temp.record_answer(');

        PERFORM dblink_connect('lock_fix', conn);
        PERFORM dblink_exec('lock_fix', 'SET lock_timeout = ''10s''');
        -- A run that died before its cleanup left these; nothing else uses the marker.
        PERFORM dblink_exec('lock_fix', format('DELETE FROM auth.users WHERE email LIKE %L', marker));
        PERFORM dblink_exec('lock_fix', format('DELETE FROM public.questions WHERE question_text = %L', qtext));
        PERFORM dblink_exec('lock_fix', format('INSERT INTO auth.users (id, email) VALUES (%L, %L)',
                                               u, replace(marker, '%', u::text)));
        PERFORM dblink_exec('lock_fix', format(
            'INSERT INTO public.sessions (id, user_id, started_at) VALUES (%L, %L, now()), (%L, %L, now())',
            s1, u, s2, u));
        PERFORM dblink_exec('lock_fix', format(
            'INSERT INTO public.questions (id, subject, question_text) VALUES (%L, %L, %L)',
            q, 'assert-answer-lock-no-topic', qtext));

        FOREACH c IN ARRAY ARRAY['lock_a', 'lock_b', 'lock_hold'] LOOP
            PERFORM dblink_connect(c, conn);
            PERFORM dblink_exec(c, 'SET lock_timeout = ''20s''');
        END LOOP;
        PERFORM dblink_exec('lock_a', def);
        PERFORM dblink_exec('lock_b', def);
        SELECT pid INTO pid_a FROM dblink('lock_a', 'SELECT pg_backend_pid()') AS t(pid int);
        SELECT pid INTO pid_b FROM dblink('lock_b', 'SELECT pg_backend_pid()') AS t(pid int);

        -- A close holding the row: the answer waits for it, then sees the session ended.
        PERFORM dblink_exec('lock_hold', 'BEGIN');
        PERFORM dblink_exec('lock_hold', format(
            'UPDATE public.sessions SET ended_at = now() WHERE id = %L AND ended_at IS NULL', s1));
        PERFORM dblink_exec('lock_a', 'BEGIN');
        PERFORM dblink_send_query('lock_a', format(
            'SELECT pg_temp.record_answer(%L, %L, %L, 0, true, now())', s1, u, q));
        IF NOT pg_temp.answer_lock_waits(pid_a) THEN
            RAISE EXCEPTION 'the answer never waited on the open close, so the race was not staged';
        END IF;
        PERFORM dblink_exec('lock_hold', 'COMMIT');
        SELECT r INTO res_a FROM dblink_get_result('lock_a') AS t(r jsonb);
        PERFORM * FROM dblink_get_result('lock_a') AS t(r jsonb);
        SELECT k INTO n FROM dblink('lock_a', format(
            'SELECT count(*) FROM public.session_answers WHERE session_id = %L', s1)) AS t(k bigint);
        PERFORM dblink_exec('lock_a', 'ROLLBACK');
        IF res_a->>'status' IS DISTINCT FROM 'ended' OR n IS DISTINCT FROM 0::bigint THEN
            RAISE EXCEPTION 'an answer landed in a session closed while it was recorded: % (% rows)',
                res_a, n;
        END IF;

        -- Two answers held between their check and their write: they queue, neither deadlocks.
        PERFORM dblink_exec('lock_hold', 'BEGIN');
        PERFORM * FROM dblink('lock_hold', format(
            'SELECT 1 FROM public.questions WHERE id = %L FOR UPDATE', q)) AS t(x int);
        PERFORM dblink_exec('lock_a', 'BEGIN');
        PERFORM dblink_send_query('lock_a', format(
            'SELECT pg_temp.record_answer(%L, %L, %L, 0, true, now())', s2, u, q));
        IF NOT pg_temp.answer_lock_waits(pid_a) THEN
            RAISE EXCEPTION 'the first answer never waited on the held question';
        END IF;
        PERFORM dblink_exec('lock_b', 'BEGIN');
        PERFORM dblink_send_query('lock_b', format(
            'SELECT pg_temp.record_answer(%L, %L, %L, 1, false, now())', s2, u, q));
        IF NOT pg_temp.answer_lock_waits(pid_b) THEN
            RAISE EXCEPTION 'the second answer never waited';
        END IF;
        PERFORM dblink_exec('lock_hold', 'ROLLBACK');
        t0 := clock_timestamp();
        WHILE NOT (done_a AND done_b) LOOP
            -- Each is rolled back as it returns, which is what lets the other one go on.
            IF NOT done_a AND dblink_is_busy('lock_a') = 0 THEN
                SELECT r INTO res_a FROM dblink_get_result('lock_a') AS t(r jsonb);
                PERFORM * FROM dblink_get_result('lock_a') AS t(r jsonb);
                PERFORM dblink_exec('lock_a', 'ROLLBACK');
                done_a := true;
            END IF;
            IF NOT done_b AND dblink_is_busy('lock_b') = 0 THEN
                SELECT r INTO res_b FROM dblink_get_result('lock_b') AS t(r jsonb);
                PERFORM * FROM dblink_get_result('lock_b') AS t(r jsonb);
                PERFORM dblink_exec('lock_b', 'ROLLBACK');
                done_b := true;
            END IF;
            IF clock_timestamp() - t0 > interval '15 seconds' THEN
                RAISE EXCEPTION 'two answers to one session still running after 15 s: % and %', res_a, res_b;
            END IF;
            PERFORM pg_sleep(0.01);
        END LOOP;
        IF res_a->>'status' IS DISTINCT FROM 'ok' OR res_b->>'status' IS DISTINCT FROM 'ok'
           OR res_a->'counters_error' IS DISTINCT FROM 'null'::jsonb
           OR res_b->'counters_error' IS DISTINCT FROM 'null'::jsonb THEN
            RAISE EXCEPTION 'two answers to one session at once returned % and %', res_a, res_b;
        END IF;
    EXCEPTION WHEN OTHERS THEN
        err := SQLERRM;
        err_state := SQLSTATE;
    END;

    -- Always runs: close the answering connections (their transactions hold row locks), then delete.
    FOREACH c IN ARRAY ARRAY['lock_a', 'lock_b', 'lock_hold'] LOOP
        IF c = ANY (dblink_get_connections()) THEN
            IF dblink_is_busy(c) = 1 THEN
                PERFORM dblink_cancel_query(c);
            END IF;
            PERFORM dblink_disconnect(c);
        END IF;
    END LOOP;
    IF 'lock_fix' = ANY (dblink_get_connections()) THEN
        PERFORM dblink_exec('lock_fix', format('DELETE FROM auth.users WHERE email LIKE %L', marker));
        PERFORM dblink_exec('lock_fix', format('DELETE FROM public.questions WHERE question_text = %L', qtext));
        PERFORM dblink_disconnect('lock_fix');
    END IF;
    IF err IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = err_state, MESSAGE = err;
    END IF;
END $$;

-- The fixtures are gone and dblink goes with the transaction.
ROLLBACK;
