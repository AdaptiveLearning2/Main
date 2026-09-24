-- Repair rationals questions stored as subject 'algebra', and move the attempts
-- they credited. A prose heuristic, so three signals must agree (no '=', no
-- coefficient-variable, a fraction) and every change is recorded for reversal.
-- See docs/question-generation.md. Idempotent.

BEGIN;

-- Audit trail for reversal. Service-role only.
CREATE TABLE IF NOT EXISTS "public"."question_subject_reclassification" (
    "id" bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    "question_id" "uuid" NOT NULL,
    "from_subject" "text" NOT NULL,
    "to_subject" "text" NOT NULL,
    "attempts_moved" integer NOT NULL DEFAULT 0,
    "reclassified_at" timestamp with time zone NOT NULL DEFAULT now()
);

ALTER TABLE "public"."question_subject_reclassification" ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE "public"."question_subject_reclassification" FROM "anon";
REVOKE ALL ON TABLE "public"."question_subject_reclassification" FROM "authenticated";
GRANT ALL ON TABLE "public"."question_subject_reclassification" TO "service_role";

DROP TABLE IF EXISTS "misfiled_rationals";
CREATE TEMP TABLE "misfiled_rationals" ON COMMIT DROP AS
SELECT "id", "subject" AS "from_subject"
FROM "public"."questions"
WHERE "subject" = 'algebra'
  AND position('=' in coalesce("question_text", '')) = 0
  AND coalesce("question_text", '') !~ '[0-9]+\s*[xyn]\y'
  AND coalesce("question_text", '') ~ '[0-9]+/[0-9]+'
UNION ALL
-- 'rations' matched no topic, so it credited nothing and there is nothing to move.
SELECT "id", "subject" FROM "public"."questions" WHERE "subject" = 'rations';

-- Computed before the subject changes.
DROP TABLE IF EXISTS "misattributed_attempts";
CREATE TEMP TABLE "misattributed_attempts" ON COMMIT DROP AS
SELECT "sa"."user_id",
       count(*)                               AS "attempts",
       count(*) FILTER (WHERE "sa"."correct") AS "corrects"
FROM "public"."session_answers" "sa"
JOIN "misfiled_rationals" "m" ON "m"."id" = "sa"."question_id"
WHERE "m"."from_subject" = 'algebra'   -- only these ever credited a topic
GROUP BY "sa"."user_id";

DO $$
BEGIN
  -- All or nothing: a partial repair could never be re-run.
  IF NOT EXISTS (SELECT 1 FROM "public"."math_topics"
                 WHERE "topic_name" = 'rationals') THEN
    RAISE NOTICE 'no rationals topic in math_topics; nothing reclassified';
    RETURN;
  END IF;

  -- greatest(0, ...): a drifted counter must not go negative.
  UPDATE "public"."user_math_performance" "p"
  SET "attempted_questions" = greatest(0, "p"."attempted_questions" - "a"."attempts"),
      "correct_questions"   = greatest(0, "p"."correct_questions"   - "a"."corrects"),
      "updated_at"          = now()
  FROM "misattributed_attempts" "a", "public"."math_topics" "t"
  WHERE "p"."user_id" = "a"."user_id"
    AND "p"."topic_id" = "t"."id"
    AND "t"."topic_name" = 'algebra';

  INSERT INTO "public"."user_math_performance"
         ("user_id", "topic_id", "attempted_questions", "correct_questions")
  SELECT "a"."user_id", "t"."id", "a"."attempts", "a"."corrects"
  FROM "misattributed_attempts" "a", "public"."math_topics" "t"
  WHERE "t"."topic_name" = 'rationals'
  ON CONFLICT ("user_id", "topic_id") DO UPDATE
  SET "attempted_questions" = "user_math_performance"."attempted_questions"
                              + excluded."attempted_questions",
      "correct_questions"   = "user_math_performance"."correct_questions"
                              + excluded."correct_questions",
      "updated_at"          = now();

  INSERT INTO "public"."question_subject_reclassification"
         ("question_id", "from_subject", "to_subject", "attempts_moved")
  -- Only 'algebra' rows moved anything; a 'rations' count would mislead a reversal.
  SELECT "m"."id", "m"."from_subject", 'rationals',
         CASE WHEN "m"."from_subject" = 'algebra'
              THEN (SELECT count(*) FROM "public"."session_answers" "sa"
                    WHERE "sa"."question_id" = "m"."id")
              ELSE 0 END
  FROM "misfiled_rationals" "m";

  UPDATE "public"."questions"
  SET "subject" = 'rationals'
  WHERE "id" IN (SELECT "id" FROM "misfiled_rationals");
END $$;

COMMIT;
