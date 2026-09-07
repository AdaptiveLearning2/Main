-- A question carries the Common Core standard it is scored against.
--
-- Every topic was already grade-gated against a CCSS code, but only in a
-- comment beside an integer (`TOPIC_MIN_GRADE`, `SCENARIO_MIN_GRADE`). This is
-- the stored copy, resolved by `ccss_standards.ccss_for()` from the topic,
-- scenario and grade that generated the question, so a teacher reviewing a
-- session or the bank can see which standard a question exercised.
--
-- NULLABLE WITH NO DEFAULT, the same four-state rule as `figure` above it: a
-- code, or SQL NULL for a row written before this column existed. Every topic
-- has a standard, so NULL means "not resolved", never "no standard applies";
-- a default would claim every earlier question was resolved and found none.
--
-- No grant or policy changes: a column on `questions`, which is public-read
-- like the question text beside it and identifies no student.

ALTER TABLE "public"."questions"
    ADD COLUMN IF NOT EXISTS "ccss_standard" "text";

COMMENT ON COLUMN "public"."questions"."ccss_standard" IS
    'CCSS code the question is scored against, resolved by ccss_standards.py '
    'from topic, scenario and grade. NULL means not resolved, not "none".';
