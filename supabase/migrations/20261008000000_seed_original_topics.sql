-- The ten topics no other migration seeds. Without a math_topics row, attempts credit nothing.
-- By name, never an explicit id, or a regenerated seed.sql collides.

INSERT INTO "public"."math_topics" ("topic_name")
VALUES ('geometry'), ('algebra'), ('expressions'), ('ordering'), ('rationals'),
       ('mean'), ('median'), ('mode'), ('probability'), ('angle_relationships')
ON CONFLICT ("topic_name") DO NOTHING;
