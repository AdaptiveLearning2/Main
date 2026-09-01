-- Rationals questions were stored with subject 'algebra', and the attempts
-- they credited went to the algebra topic.
--
-- `LLM_rationals_generation` returned the model's own `question_topic` where
-- the other nine generators hardcode their own name, and its prompt named
-- "algebra" in prose and "rations" in the JSON example. That value becomes
-- `questions.subject`, which `record_topic_attempt` joins against
-- `math_topics.topic_name` -- so a student's fractions work was credited to
-- algebra in `user_math_performance`, the table the adaptive engine reads to
-- choose what to serve next, while rationals accumulated nothing.
--
-- The generator is fixed. This repairs what it already wrote.
--
-- THE RULE IS A PROSE REGULARITY, NOT A STRUCTURAL FACT, and an earlier
-- version of this comment claimed otherwise. The claim was that an algebra
-- question must contain '=' because `_solve_worker` splits on it -- but it
-- splits `variables`, which is *never stored*: `questions` has no such column.
-- What is filtered here is `question_text`, which the model writes freely. So
-- this is a pattern observed on a sample, applied irreversibly, and it is
-- built accordingly:
--
--   * Three signals must AGREE, not one. No '=', no coefficient-variable
--     (`\d+[xyn]`, the pattern `grade_appropriateness` uses), and a fraction
--     present. A row where they disagree is left alone -- the safe direction
--     for an irreversible change resting on a sample.
--   * Every change is recorded in `question_subject_reclassification`, so this
--     can be reviewed after the fact and reversed. That table is the reason
--     a prose heuristic is acceptable here at all.
--
-- Measured over 25 real rows, the three signals partition them completely:
-- 19 with '=' and a coefficient-variable and no fraction, 6 with a fraction
-- and neither of the others. Nothing in between.
--
-- Two signals were considered and rejected. A fractional ANSWER is useless --
-- genuine algebra answers here are frequently fractions (7/2, 17/6). And
-- "mentions x" is worse: one of the six reads "Solve for x: 3/4 + 2/5", a
-- rationals question wearing algebra's phrasing, because the prompt told the
-- model the topic was algebra. Only a coefficient-variable separates them.
--
-- Idempotent: a second run finds no rows, since the subject has moved.

BEGIN;

-- What was changed, so a heuristic applied to a student's record can be
-- audited and undone. Service-role only, like `feature_flag_changes`.
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
-- The other spelling the prompt offered. Unambiguous -- no topic is named
-- that -- so it needs no signal agreement. It also credited nothing: the
-- subject matched no `math_topics` row, so `record_topic_attempt` attributed
-- those attempts to nothing rather than to the wrong topic. They are lost and
-- there is nothing to move, which the recorded `attempts_moved` will show.
SELECT "id", "subject" FROM "public"."questions" WHERE "subject" = 'rations';

-- Attempts to move, per student. Computed before the subject changes.
DROP TABLE IF EXISTS "misattributed_attempts";
CREATE TEMP TABLE "misattributed_attempts" ON COMMIT DROP AS
SELECT "sa"."user_id",
       count(*)                               AS "attempts",
       count(*) FILTER (WHERE "sa"."correct") AS "corrects"
FROM "public"."session_answers" "sa"
JOIN "misfiled_rationals" "m" ON "m"."id" = "sa"."question_id"
JOIN "public"."questions" "q" ON "q"."id" = "m"."id"
WHERE "m"."from_subject" = 'algebra'   -- only these ever credited a topic
GROUP BY "sa"."user_id";

DO $$
BEGIN
  -- All of it or none of it. The subject update and the counter move used to
  -- be guarded separately, so a database whose `math_topics` lacks a rationals
  -- row would have moved the subject and left the attempts on algebra -- and
  -- a re-run would then find nothing to correct, leaving that inconsistency
  -- permanent. There is no partial repair worth having.
  IF NOT EXISTS (SELECT 1 FROM "public"."math_topics"
                 WHERE "topic_name" = 'rationals') THEN
    RAISE NOTICE 'no rationals topic in math_topics; nothing reclassified';
    RETURN;
  END IF;

  -- Take them off algebra. `greatest(0, ...)` because a counter that has
  -- already drifted must not be driven negative by a repair.
  UPDATE "public"."user_math_performance" "p"
  SET "attempted_questions" = greatest(0, "p"."attempted_questions" - "a"."attempts"),
      "correct_questions"   = greatest(0, "p"."correct_questions"   - "a"."corrects"),
      "updated_at"          = now()
  FROM "misattributed_attempts" "a", "public"."math_topics" "t"
  WHERE "p"."user_id" = "a"."user_id"
    AND "p"."topic_id" = "t"."id"
    AND "t"."topic_name" = 'algebra';

  -- And put them on rationals, which most of these students will have no row
  -- for -- the whole point is that the topic never accumulated anything.
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
  SELECT "m"."id", "m"."from_subject", 'rationals',
         (SELECT count(*) FROM "public"."session_answers" "sa"
          WHERE "sa"."question_id" = "m"."id")
  FROM "misfiled_rationals" "m";

  UPDATE "public"."questions"
  SET "subject" = 'rationals'
  WHERE "id" IN (SELECT "id" FROM "misfiled_rationals");
END $$;

COMMIT;
