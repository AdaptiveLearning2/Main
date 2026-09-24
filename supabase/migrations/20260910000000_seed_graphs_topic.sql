-- `graphs`: reading a bar graph (1.MD.4, 2.MD.10, through 3.MD.3); needs
-- questions.figure. Without a math_topics row, attempts credit nothing.

INSERT INTO "public"."math_topics" ("topic_name")
VALUES ('graphs')
ON CONFLICT ("topic_name") DO NOTHING;
