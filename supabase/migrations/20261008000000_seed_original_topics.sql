-- The original ten topics, seeded before migrations tracked this table; a stack built
-- from migrations alone had none, so record_topic_attempt credited their answers to nothing.
-- By name, never an explicit id, or a regenerated seed.sql collides.

INSERT INTO "public"."math_topics" ("topic_name")
VALUES ('geometry'), ('algebra'), ('expressions'), ('ordering'), ('rationals'),
       ('mean'), ('median'), ('mode'), ('probability'), ('angle_relationships')
ON CONFLICT ("topic_name") DO NOTHING;
