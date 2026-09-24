-- Scope user_stats reads to the relationships that justify them, replacing a
-- USING (true) policy. RLS is the gate: with no policy matching auth.uid()
-- NULL, anon reads nothing.

DROP POLICY IF EXISTS "stats: public read" ON "public"."user_stats";

-- Own rows: "stats: own write" has no FOR clause, so it already covers SELECT.

-- Inline EXISTS: the is_*_of_class helpers take a class_id, not a student.
CREATE POLICY "stats: teacher read" ON "public"."user_stats"
  FOR SELECT USING (EXISTS (
    SELECT 1
    FROM "public"."class_memberships" "cm"
    JOIN "public"."classes" "c" ON "c"."id" = "cm"."class_id"
    WHERE "cm"."student_id" = "user_stats"."user_id"
      AND "c"."teacher_id" = "auth"."uid"()
  ));

CREATE POLICY "stats: parent read" ON "public"."user_stats"
  FOR SELECT USING (EXISTS (
    SELECT 1
    FROM "public"."parent_child_links"
    WHERE "parent_child_links"."child_id" = "user_stats"."user_id"
      AND "parent_child_links"."parent_id" = "auth"."uid"()
  ));

-- /api/leaderboard reads via service_role, so it still shows others' totals.

NOTIFY pgrst, 'reload schema';
