-- missing_number (1.OA.8 to 3.OA.4) and patterns (1.NBT.1 to 5.OA.3) for the
-- youngest grades; both use `?`, never `x`, to stay clear of algebra.
-- Without a math_topics row, record_topic_attempt silently credits nothing.

INSERT INTO "public"."math_topics" ("topic_name")
VALUES ('missing_number'), ('patterns')
ON CONFLICT ("topic_name") DO NOTHING;
