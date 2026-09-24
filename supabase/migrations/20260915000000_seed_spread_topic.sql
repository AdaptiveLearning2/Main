-- `spread` (S-ID.2), standard deviation only: IQR and MAD are 6.SP.5c.
-- Without a math_topics row, attempts credit nothing.
-- By name, never an explicit id, or a regenerated seed.sql collides.

INSERT INTO "public"."math_topics" ("topic_name")
VALUES ('spread')
ON CONFLICT ("topic_name") DO NOTHING;
