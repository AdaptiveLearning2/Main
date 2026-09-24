-- `shape_fractions`: reading a fraction off a partitioned shape (1.G.3, 2.G.3,
-- 3.NF.1); recognition, unlike `rationals` arithmetic. Without a math_topics
-- row, attempts credit nothing.

INSERT INTO "public"."math_topics" ("topic_name")
VALUES ('shape_fractions')
ON CONFLICT ("topic_name") DO NOTHING;
