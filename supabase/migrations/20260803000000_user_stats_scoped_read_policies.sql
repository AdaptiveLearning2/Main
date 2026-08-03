-- Scope user_stats reads to the relationships that justify them.
--
-- The table shipped with:
--
--   CREATE POLICY "stats: public read" ON "public"."user_stats"
--     FOR SELECT USING (true);
--
-- RLS is enabled, but a policy of USING (true) permits everything, so the anon
-- key that ships in the frontend bundle read every user's user_id,
-- total_questions, total_correct, current_streak and best_streak, with no
-- authentication at all. Every sibling table holding student data is scoped --
-- compare "perf: own" / "perf: parent read" / "perf: teacher read" on
-- user_math_performance, and "sessions: own" / "sessions: teacher read". This
-- one was missed.
--
-- The GRANT ALL ... TO "anon" on this table is deliberately left alone: it is
-- Supabase's template grant and every table in the schema carries it, so the
-- grant is not what distinguished this table. RLS is the gate, and with no
-- permissive policy matching an unauthenticated caller (auth.uid() is NULL for
-- anon, and every policy below compares against it) anon now reads nothing.
-- Singling out this table's grant would diverge from the pattern without
-- closing anything the policies do not already close.

-- The blanket read goes.
DROP POLICY IF EXISTS "stats: public read" ON "public"."user_stats";

-- Own rows are already covered and are deliberately not re-created here:
-- "stats: own write" is USING (auth.uid() = user_id) with no FOR clause, so it
-- applies to ALL commands including SELECT. The name undersells it, which is
-- why this note exists rather than a fourth policy that would shadow it.

-- A teacher may read the stats of a student in a class they own. Written as an
-- inline EXISTS over class_memberships JOIN classes, matching "perf: teacher
-- read" and "sessions: teacher read" exactly -- the is_member_of_class /
-- is_teacher_of_class helpers are class-scoped (they answer "am I in *this
-- class*") and take a class_id, so they do not fit a student-scoped check.
--
-- This is the one policy with a live browser-client caller:
-- frontend/src/pages/teacher/Students.jsx reads a student's user_stats through
-- the browser client, so without it the teacher student list goes blank.
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
-- through /api/parent/children, which uses the service-role client and bypasses
-- RLS. Included anyway because it grants a parent no information they do not
-- already receive through that endpoint, and because leaving it out would make
-- this the one student-data table where the parent relationship is not
-- expressed, which is how the next parent-facing page ends up reaching for the
-- service-role client instead.
CREATE POLICY "stats: parent read" ON "public"."user_stats"
  FOR SELECT USING (EXISTS (
    SELECT 1
    FROM "public"."parent_child_links"
    WHERE "parent_child_links"."child_id" = "user_stats"."user_id"
      AND "parent_child_links"."parent_id" = "auth"."uid"()
  ));

-- /api/leaderboard is unaffected: it reads through the service-role client,
-- which bypasses RLS, so it still shows other students' totals. That is the
-- existing design and this migration does not change it -- but it does mean
-- the leaderboard is now the only path by which one student sees another's
-- figures, which is worth knowing if that endpoint is ever revisited.

NOTIFY pgrst, 'reload schema';
