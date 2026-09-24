-- Expire old generated questions; nothing else bounds the table's growth.
-- The FK and created_at indexes come first, or each delete scans session_answers.

CREATE INDEX IF NOT EXISTS "answers_question_idx"
    ON "public"."session_answers" USING "btree" ("question_id");

CREATE INDEX IF NOT EXISTS "questions_created_idx"
    ON "public"."questions" USING "btree" ("created_at" DESC);


-- Deletes questions older than p_retention_days unless answered in that window; answers
-- survive via ON DELETE SET NULL. Gap: practice_session_answers.question_id has no FK and
-- is not protected; add both before building any practice-review reader.
CREATE OR REPLACE FUNCTION "public"."expire_old_questions"(
    p_retention_days integer DEFAULT 365,
    p_batch_size integer DEFAULT 5000,
    p_max_batches integer DEFAULT 200
)
RETURNS jsonb
LANGUAGE "plpgsql"
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
DECLARE
    cutoff timestamptz;
    n integer;
    total integer := 0;
    batches integer := 0;
    protected integer;
BEGIN
    IF p_retention_days < 1 THEN
        RAISE EXCEPTION 'p_retention_days must be at least 1, got %', p_retention_days;
    END IF;

    cutoff := now() - make_interval(days => p_retention_days);

    -- Counted first, so "nothing old" differs from "everything old in use".
    SELECT count(*) INTO protected
    FROM questions q
    WHERE q.created_at < cutoff
      AND EXISTS (
          SELECT 1 FROM session_answers a
          WHERE a.question_id = q.id
            AND a.answered_at >= cutoff
      );

    LOOP
        WITH doomed AS (
            SELECT q.ctid
            FROM questions q
            WHERE q.created_at < cutoff
              AND NOT EXISTS (
                  SELECT 1 FROM session_answers a
                  WHERE a.question_id = q.id
                    AND a.answered_at >= cutoff
              )
            LIMIT p_batch_size
        )
        DELETE FROM questions WHERE ctid IN (SELECT ctid FROM doomed);

        GET DIAGNOSTICS n = ROW_COUNT;
        total := total + n;
        batches := batches + 1;
        EXIT WHEN n = 0 OR batches >= p_max_batches;
    END LOOP;

    RETURN jsonb_build_object(
        'deleted', total,
        'kept_still_referenced', protected,
        -- Rows unreached before the cap appear in neither count.
        'hit_batch_cap', batches >= p_max_batches AND n > 0,
        'cutoff', cutoff
    );
END;
$$;

REVOKE ALL ON FUNCTION "public"."expire_old_questions"(integer, integer, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."expire_old_questions"(integer, integer, integer) FROM "anon";
REVOKE ALL ON FUNCTION "public"."expire_old_questions"(integer, integer, integer) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."expire_old_questions"(integer, integer, integer) TO "service_role";


-- An hour after expire-signal-rows, so the bulk deletes do not contend.
CREATE EXTENSION IF NOT EXISTS "pg_cron";

SELECT cron.schedule('expire-old-questions', '30 4 * * *',
                     $job$SELECT public.expire_old_questions();$job$);

NOTIFY pgrst, 'reload schema';
