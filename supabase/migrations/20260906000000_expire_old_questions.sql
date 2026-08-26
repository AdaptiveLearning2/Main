-- `questions` is the one table in this schema that grows with total product
-- usage and has nothing cleaning it up.
--
-- Every generated question is stored permanently by add_question_to_supabase,
-- deduped only by an exact question_text match -- and every generation prompt
-- explicitly instructs the model to vary its wording ("DO NOT generate a
-- question matching any of the above. Use different wording and numerical
-- values"), so exact duplicates are the rare case by design. Unlike the three
-- signal tables, whose growth is bounded by one school year before
-- expire_signal_rows batch-deletes them, this one is bounded by nothing.
--
-- Two indexes first, because without them this job is worse than the problem:
--
--   session_answers.question_id is an UNINDEXED foreign key. Postgres does not
--   index the referencing side automatically, so both this function's
--   NOT EXISTS check and the ON DELETE SET NULL cascade it triggers would scan
--   session_answers once per deleted question.
--
--   questions.created_at is unindexed too, which this function needs -- and so
--   does GET /api/questions, which orders by it on every cache miss and today
--   sorts the whole table to return 100 rows.

CREATE INDEX IF NOT EXISTS "answers_question_idx"
    ON "public"."session_answers" USING "btree" ("question_id");

CREATE INDEX IF NOT EXISTS "questions_created_idx"
    ON "public"."questions" USING "btree" ("created_at" DESC);


-- Deletes questions older than p_retention_days, except any still referenced
-- by an answer inside that same window.
--
-- The exception is the safety property, and it is scoped to the window rather
-- than to "any reference at all" deliberately. Protecting every answered
-- question would leave growth unbounded again, since most questions are
-- answered; protecting none would break SessionReview for a question answered
-- the day before the cutoff. session_answers.question_id is ON DELETE SET
-- NULL, so a dropped question degrades an old review to "The question is no
-- longer in the bank" -- which that page already renders -- rather than
-- removing the answer. Academic history is never what expires here: the
-- answer row, its correctness, and the topic attributed to it all stay.
--
-- 365 days by default: a question generated a year ago is outside any current
-- student's practice history, and the teacher's browser shows newest-first
-- with a limit of 100 regardless. Tune by changing the cron body below rather
-- than editing this default, so the scheduled value is visible in one place.
--
-- Batched on ctid like expire_signal_rows, and for the same reason: an
-- unbounded DELETE over a year of rows holds a long lock. Idempotent and
-- self-healing the same way -- the cutoff is derived from the clock, so a
-- missed run completes on the next one and a repeat deletes nothing new.
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

    -- Counted before the delete so the return value can say how many rows the
    -- reference check saved, not just how many it removed. A number that only
    -- reports deletions cannot distinguish "nothing was old enough" from
    -- "everything old was still in use".
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
        -- Eligible by age but kept because an answer inside the window still
        -- points at them.
        'kept_still_referenced', protected,
        -- Rows that were eligible but not reached before the batch cap appear
        -- in neither count, so `deleted` alone does not mean the work is done.
        -- Same reporting rule as expire_signal_rows' hit_batch_cap.
        'hit_batch_cap', batches >= p_max_batches AND n > 0,
        'cutoff', cutoff
    );
END;
$$;

REVOKE ALL ON FUNCTION "public"."expire_old_questions"(integer, integer, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."expire_old_questions"(integer, integer, integer) FROM "anon";
REVOKE ALL ON FUNCTION "public"."expire_old_questions"(integer, integer, integer) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."expire_old_questions"(integer, integer, integer) TO "service_role";


-- 04:30 UTC: an hour after expire-signal-rows, so the two bulk-delete jobs
-- do not contend. `cron.schedule` upserts on the job name, so re-running this
-- migration re-points the job rather than creating a second one.
CREATE EXTENSION IF NOT EXISTS "pg_cron";

SELECT cron.schedule('expire-old-questions', '30 4 * * *',
                     $job$SELECT public.expire_old_questions();$job$);

NOTIFY pgrst, 'reload schema';
