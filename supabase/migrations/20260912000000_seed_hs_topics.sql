-- `quadratics` (A-REI.4b) and `functions` (F-IF.2, F-BF.1c), the first topics
-- above grade 8. Without a math_topics row, attempts credit nothing.
-- By name, never an explicit id, or a regenerated seed.sql collides.

INSERT INTO "public"."math_topics" ("topic_name")
VALUES ('quadratics'), ('functions')
ON CONFLICT ("topic_name") DO NOTHING;
