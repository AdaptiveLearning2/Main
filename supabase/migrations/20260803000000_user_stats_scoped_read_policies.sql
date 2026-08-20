-- Scope user_stats reads to the relationships that justify them.
--
-- The table shipped with a USING (true) policy, which permits everything --
-- so the anon key in the frontend bundle could read every user's user_id,
-- total_questions, total_correct, current_streak and best_streak with no
-- authentication at all. Every sibling table holding student data is scoped
-- (compare "perf: own" / "perf: parent read" / "perf: teacher read" on
-- user_math_performance, and "sessions: own" / "sessions: teacher read");
-- this one was missed.
--
-- The GRANT ALL ... TO "anon" on this table is deliberately left alone: it's
-- Supabase's template grant, present on every table, so it isn't what made
-- this table different. RLS is the gate, and with no permissive policy
-- matching an unauthenticated caller (auth.uid() is NULL for anon), anon now
-- reads nothing.

-- The blanket read goes.
DROP POLICY IF EXISTS "stats: public read" ON "public"."user_stats";

-- Own rows are already covered and are deliberately not re-created here:
-- "stats: own write" is USING (auth.uid() = user_id) with no FOR clause, so
-- it applies to ALL commands including SELECT, despite the name.

-- A teacher may read the stats of a student in a class they own. Written as
-- an inline EXISTS over class_memberships JOIN classes, matching "perf:
-- teacher read" and "sessions: teacher read" -- the is_member_of_class /
-- is_teacher_of_class helpers answer "am I in *this class*" and take a
-- class_id, so they don't fit a student-scoped check.
--
-- This is the one policy with a live browser-client caller:
-- frontend/src/pages/teacher/Students.jsx reads a student's user_stats
-- through the browser client, so without it the teacher student list goes
-- blank.
CREATE POLICY "stats: teacher read" ON "public"."user_stats"
  FOR SELECT USING (EXISTS (
    SELECT 1
    FROM "public"."class_memberships" "cm"
    JOIN "public"."classes" "c" ON "c"."id" = "cm"."class_id"
    WHERE "cm"."student_id" = "user_stats"."user_id"
      AND "c"."teacher_id" = "auth"."uid"()
  ));

-- A linked parent may read their own child's stats.
--
-- No browser-client caller today -- the parent dashboard reads these figures
-- through /api/parent/children, which uses the service-role client and
-- bypasses RLS. Included anyway so this isn't the one student-data table
-- without the parent relationship expressed, which is what would push the
-- next parent-facing page toward the service-role client instead.
CREATE POLICY "stats: parent read" ON "public"."user_stats"
  FOR SELECT USING (EXISTS (
    SELECT 1
    FROM "public"."parent_child_links"
    WHERE "parent_child_links"."child_id" = "user_stats"."user_id"
      AND "parent_child_links"."parent_id" = "auth"."uid"()
  ));

-- /api/leaderboard is unaffected: it reads through the service-role client,
-- which bypasses RLS, so it still shows other students' totals. That makes
-- it the only remaining path by which one student sees another's figures.

NOTIFY pgrst, 'reload schema';
