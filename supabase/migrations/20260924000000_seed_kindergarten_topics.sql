-- Kindergarten's own topics (K.CC, K.OA, K.NBT, K.G), served only at grade 0.
-- Without a math_topics row, attempts credit nothing.

INSERT INTO "public"."math_topics" ("topic_name")
VALUES ('counting'), ('comparing_numbers'), ('add_and_subtract'), ('teen_numbers'), ('shapes')
ON CONFLICT ("topic_name") DO NOTHING;
