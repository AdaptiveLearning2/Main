-- Two topics for the youngest students.
--
-- Grade 1 could be served exactly two topics -- `ordering` and `expressions`
-- -- so a 6-year-old saw the same two on rotation. `TOPIC_MIN_GRADE`'s own
-- comment recorded that as "the honest size of what this system can ask a
-- 6-year-old", which was true of the topics that existed, not of the grade.
--
--   missing_number  1.OA.8, the unknown in an equation ("8 + ? = 11"),
--                   through 3.OA.4's unknown factor. Capped at grade 3.
--   patterns        1.NBT.1 counting sequences and 2.NBT.2 skip counting,
--                   through 5.OA.3. Capped at grade 5.
--
-- Both are `?`, never `x`: the notation is what keeps them clear of `algebra`,
-- which is 6.EE.7 and gated to grade 6.
--
-- THE ROW IS THE POINT OF THIS MIGRATION, not a detail of it.
-- `record_topic_attempt` resolves a question's topic by joining
-- `math_topics.topic_name = questions.subject`, and returns without
-- attributing anything when that finds nothing -- silently, since the helper
-- never raises. So a generator shipped without its row here records questions,
-- serves them, scores them, and credits the student's work to nothing at all.
-- That is exactly what `20260907000000` had to repair for `rationals`, and it
-- would be a new instance of it rather than a lesson learned.
--
-- Idempotent: `math_topics_topic_name_key` makes the conflict a no-op, so this
-- is safe against a row added by hand before it ran.

INSERT INTO "public"."math_topics" ("topic_name")
VALUES ('missing_number'), ('patterns')
ON CONFLICT ("topic_name") DO NOTHING;
