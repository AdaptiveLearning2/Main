-- `shape_fractions` -- reading a fraction off a partitioned shape. 1.G.3
-- (halves and fourths), 2.G.3 (thirds), 3.NF.1 (a/b as a parts of b).
--
-- Distinct from `rationals`, which is 4.NF.3 onward and is fraction
-- *arithmetic*. This is recognition: the answer is read from a picture, not
-- computed. That is why it sits at grade 1 while `rationals` starts at 4.
--
-- The row is the point of this migration: `record_topic_attempt` joins
-- `math_topics.topic_name = questions.subject` and attributes nothing when it
-- finds none, silently. See 20260907000000, which had to repair exactly that.

INSERT INTO "public"."math_topics" ("topic_name")
VALUES ('shape_fractions')
ON CONFLICT ("topic_name") DO NOTHING;
