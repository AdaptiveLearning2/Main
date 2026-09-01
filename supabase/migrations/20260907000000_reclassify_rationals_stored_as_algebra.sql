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
-- THE RULE. An algebra question is an *equation*: `_solve_worker` splits the
-- expression on '=', so one is always present. A rationals question is an
-- expression to evaluate and never has one. That is structural rather than
-- stylistic, which is what makes this a derivation and not a guess. A fraction
-- must also be present, so a row that merely lacks '=' is left alone.
--
-- Checked against 25 real rows: 19 genuine algebra, all carrying '=' and a
-- variable, and 6 fraction-arithmetic rows carrying neither. The tempting
-- second signal -- a fractional answer -- is useless here, because genuine
-- algebra answers are frequently fractions too (7/2, 17/6). It is deliberately
-- not used.
--
-- Note one of the six reads "Solve for x: 3/4 + 2/5": a rationals question
-- wearing algebra's phrasing, because the prompt told the model the topic was
-- algebra. So "mentions x" cannot be a discriminator either.
--
-- Idempotent: a second run finds no rows, since the subject has moved.

BEGIN;

-- Dropped first so re-applying inside one session cannot fail on a name that
-- ON COMMIT DROP has not cleared yet.
DROP TABLE IF EXISTS "misfiled_rationals";
CREATE TEMP TABLE "misfiled_rationals" ON COMMIT DROP AS
SELECT "id"
FROM "public"."questions"
WHERE "subject" = 'algebra'
  AND position('=' in coalesce("question_text", '')) = 0
  AND coalesce("question_text", '') ~ '[0-9]+/[0-9]+';

-- Attempts to move, per student. Computed before the subject changes.
DROP TABLE IF EXISTS "misattributed_attempts";
CREATE TEMP TABLE "misattributed_attempts" ON COMMIT DROP AS
SELECT "sa"."user_id",
       count(*)                                  AS "attempts",
       count(*) FILTER (WHERE "sa"."correct")    AS "corrects"
FROM "public"."session_answers" "sa"
JOIN "misfiled_rationals" "m" ON "m"."id" = "sa"."question_id"
GROUP BY "sa"."user_id";

-- Take them off algebra. `greatest(0, ...)` because a counter that has already
-- drifted must not be driven negative by a repair.
UPDATE "public"."user_math_performance" "p"
SET "attempted_questions" = greatest(0, "p"."attempted_questions" - "a"."attempts"),
    "correct_questions"   = greatest(0, "p"."correct_questions"   - "a"."corrects"),
    "updated_at"          = now()
FROM "misattributed_attempts" "a", "public"."math_topics" "t"
WHERE "p"."user_id" = "a"."user_id"
  AND "p"."topic_id" = "t"."id"
  AND "t"."topic_name" = 'algebra'
  -- Only if there is somewhere for them to go. The decrement and the insert
  -- below are separate statements, so without this a database whose
  -- `math_topics` lacks a rationals row would take the attempts off algebra
  -- and put them nowhere -- destroying the record this migration exists to
  -- repair, and looking like it worked.
  AND EXISTS (SELECT 1 FROM "public"."math_topics"
              WHERE "topic_name" = 'rationals');

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

UPDATE "public"."questions"
SET "subject" = 'rationals'
WHERE "id" IN (SELECT "id" FROM "misfiled_rationals");

-- The other spelling the prompt offered. Unambiguous -- no topic is named
-- that -- and it needs no counter repair: it matched no `math_topics` row, so
-- `record_topic_attempt` credited nothing rather than crediting the wrong
-- thing. Those attempts are simply lost; there is nothing to move.
UPDATE "public"."questions"
SET "subject" = 'rationals'
WHERE "subject" = 'rations';

COMMIT;
