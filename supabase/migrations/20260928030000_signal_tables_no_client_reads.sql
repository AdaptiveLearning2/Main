-- The per-sample signal tables are read by the backend only, which applies consent per channel.
-- The teacher-read policies gave any class teacher every row and column (raw jsonb included)
-- whatever the student had declined, and no client reads these tables at all.
DROP POLICY IF EXISTS "cog: teacher read" ON "public"."cognitive_signals";
DROP POLICY IF EXISTS "face: teacher read" ON "public"."face_signals";
DROP POLICY IF EXISTS "heart: teacher read" ON "public"."heart_signals";

REVOKE ALL ON TABLE "public"."cognitive_signals" FROM "authenticated";
REVOKE ALL ON TABLE "public"."face_signals" FROM "authenticated";
REVOKE ALL ON TABLE "public"."heart_signals" FROM "authenticated";
REVOKE ALL ON TABLE "public"."cognitive_signals" FROM "anon";
REVOKE ALL ON TABLE "public"."face_signals" FROM "anon";
REVOKE ALL ON TABLE "public"."heart_signals" FROM "anon";

NOTIFY pgrst, 'reload schema';
