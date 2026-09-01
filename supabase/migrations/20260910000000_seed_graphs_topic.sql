-- `graphs` -- reading a bar graph. 1.MD.4 ("ask and answer questions about how
-- many more or less"), 2.MD.10 (a bar graph with up to four categories and
-- compare problems using it), through 3.MD.3.
--
-- The first topic whose questions are unanswerable without their figure, which
-- is why it waited for `questions.figure` (20260909000000). It is also the
-- visual precursor to mean/median/mode: counts read off a graph at grade 1-2,
-- statistics over a listed dataset at grade 6.
--
-- The row is the point of this migration. `record_topic_attempt` resolves a
-- question's topic by joining `math_topics.topic_name = questions.subject` and
-- attributes nothing when that finds none -- silently, since the helper never
-- raises. A topic shipped without its row serves and scores questions while
-- crediting the student's work to nothing, which is what 20260907000000 had to
-- repair for `rationals`.

INSERT INTO "public"."math_topics" ("topic_name")
VALUES ('graphs')
ON CONFLICT ("topic_name") DO NOTHING;
