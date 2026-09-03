-- `spread` -- comparing the spread of data sets (S-ID.2). The third topic for
-- grades 9-12, after `quadratics` and `functions` in 20260912000000.
--
-- Standard deviation only, and deliberately: the interquartile range and mean
-- absolute deviation S-ID.2 also names are exactly scoreable and are
-- **6.SP.5c**, so offering them would put grade-6 content inside a topic added
-- to serve grades 9-12 -- measured against the very audit that motivated it.
--
-- The row is the point of this migration: `record_topic_attempt` joins
-- `math_topics.topic_name = questions.subject` and attributes nothing when it
-- finds none, silently, because that helper never raises. A topic shipped
-- without a row serves and scores questions while crediting the student's work
-- to nothing. See 20260907000000, which had to repair exactly that.
--
-- By name with ON CONFLICT DO NOTHING, never an explicit id: `supabase/seed.sql`
-- is a per-developer `--data-only` dump carrying explicit ids, and `db reset`
-- applies migrations first, so a row taking an id from the sequence collides
-- with the dump and kills the seed part way through.

INSERT INTO "public"."math_topics" ("topic_name")
VALUES ('spread')
ON CONFLICT ("topic_name") DO NOTHING;
