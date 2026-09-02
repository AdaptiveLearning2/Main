-- `quadratics` (A-REI.4b) and `functions` (F-IF.2, F-BF.1c) -- the first two
-- topics here whose concept is above grade 8 at all.
--
-- Every other topic tops out at grade 8 by the CCSS grade of what it can
-- score, so an audit of 640 generated questions found 81% of grade-9 questions
-- three or more grades below grade. Stating a harder requirement in every
-- `advanced` prompt tier moved that by two points, because harder numbers
-- inside 8.EE.7b are still 8.EE.7b. Closing it needed solvers; see
-- `backend/hs_solvers.py`.
--
-- The rows are the point of this migration: `record_topic_attempt` joins
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
VALUES ('quadratics'), ('functions')
ON CONFLICT ("topic_name") DO NOTHING;
