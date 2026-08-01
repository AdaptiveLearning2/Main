-- Closes the world-readable user_stats table.
--
-- The init migration shipped:
--
--   CREATE POLICY "stats: public read" ON "public"."user_stats"
--     FOR SELECT USING (true);
--
-- combined with GRANT ALL ON TABLE "public"."user_stats" TO "anon". The anon
-- key is VITE_-prefixed and therefore baked into the published frontend bundle,
-- so that pair let anyone -- signed in or not -- read every row: user_id,
-- the four counters, and last_session_at.
--
-- Every neighbouring table got a scoped policy (cog/face/perf all have
-- own-row plus a teacher or parent relationship check). user_stats is the one
-- that did not, and USING (true) is not a decision anyone appears to have
-- made deliberately.
--
-- Impact was limited rather than nil: profiles RLS is auth.uid()-scoped, so an
-- anonymous reader cannot put a name to any row. But /api/leaderboard returns
-- user_id alongside display_name, which gave any signed-in user a UUID -> name
-- map for the top scorers, and from there their full history. That endpoint is
-- tightened in the same change.

-- 1. Own row -------------------------------------------------------------
-- Renamed from "stats: own write". It has no FOR clause, so it always covered
-- SELECT too -- the name implied reads were somebody else's problem, which is
-- the misreading that invites a "stats: public read" to be added next to it.
--
-- The expression is unchanged, and so is write behaviour: Postgres reuses
-- USING as WITH CHECK when the latter is omitted, so INSERT/UPDATE/DELETE stay
-- restricted to auth.uid() = user_id exactly as before.
DROP POLICY IF EXISTS "stats: own write" ON "public"."user_stats";
DROP POLICY IF EXISTS "stats: own row" ON "public"."user_stats";
CREATE POLICY "stats: own row" ON "public"."user_stats"
  USING (("auth"."uid"() = "user_id"));

-- 2. Teacher of a class the student is in --------------------------------
-- Mirrors "cog: teacher read" and "face: teacher read" from the init
-- migration, including their shape. Required by the teacher Students tab,
-- which reads user_stats through the browser client
-- (frontend/src/pages/teacher/Students.jsx).
--
-- No recursion risk despite the subquery touching classes and
-- class_memberships: the SELECT policies on both were rewritten in
-- 20260709154104 to call the SECURITY DEFINER helpers, which is what broke the
-- policy-evaluation cycle. The two signal-table policies have the same body
-- and are exercised in production today.
DROP POLICY IF EXISTS "stats: teacher read" ON "public"."user_stats";
CREATE POLICY "stats: teacher read" ON "public"."user_stats"
  FOR SELECT USING ((EXISTS ( SELECT 1
     FROM ("public"."class_memberships" "cm"
       JOIN "public"."classes" "c" ON (("c"."id" = "cm"."class_id")))
    WHERE (("cm"."student_id" = "user_stats"."user_id") AND ("c"."teacher_id" = "auth"."uid"())))));

-- Deliberately no parent policy. Parents reach their children's stats through
-- the backend, which uses the service-role client and bypasses RLS entirely --
-- the same reason cognitive_signals and face_signals have no parent policy.
-- Adding one here would widen direct table access past anything the app reads.

-- 3. Drop the open read --------------------------------------------------
-- Last, so the replacements above are in place first and no window exists
-- where a legitimate reader is locked out. (The whole migration runs in one
-- transaction regardless; this is for anyone reading it top to bottom.)
DROP POLICY IF EXISTS "stats: public read" ON "public"."user_stats";

NOTIFY pgrst, 'reload schema';
