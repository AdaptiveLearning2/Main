-- Fixes an "infinite recursion detected in policy" error caused by the
-- classes/class_memberships SELECT policies referencing each other, and adds
-- a policy letting a teacher read the profiles of students in their classes
-- (needed for the teacher Students/Classes tabs, which otherwise show empty
-- lists).

-- 1. Helper functions -------------------------------------------------------
-- SECURITY DEFINER + a fixed search_path lets these bypass RLS on the tables
-- they query, which breaks the recursive cycle between classes and
-- class_memberships.

CREATE OR REPLACE FUNCTION "public"."is_member_of_class"("p_class_id" "uuid") RETURNS boolean
    LANGUAGE "sql" STABLE SECURITY DEFINER
    SET "search_path" TO 'public'
    AS $$
  select exists (
    select 1 from class_memberships
    where class_id = p_class_id and student_id = auth.uid()
  );
$$;

ALTER FUNCTION "public"."is_member_of_class"("p_class_id" "uuid") OWNER TO "postgres";

GRANT ALL ON FUNCTION "public"."is_member_of_class"("p_class_id" "uuid") TO "anon";
GRANT ALL ON FUNCTION "public"."is_member_of_class"("p_class_id" "uuid") TO "authenticated";
GRANT ALL ON FUNCTION "public"."is_member_of_class"("p_class_id" "uuid") TO "service_role";

CREATE OR REPLACE FUNCTION "public"."is_teacher_of_class"("p_class_id" "uuid") RETURNS boolean
    LANGUAGE "sql" STABLE SECURITY DEFINER
    SET "search_path" TO 'public'
    AS $$
  select exists (
    select 1 from classes
    where id = p_class_id and teacher_id = auth.uid()
  );
$$;

ALTER FUNCTION "public"."is_teacher_of_class"("p_class_id" "uuid") OWNER TO "postgres";

GRANT ALL ON FUNCTION "public"."is_teacher_of_class"("p_class_id" "uuid") TO "anon";
GRANT ALL ON FUNCTION "public"."is_teacher_of_class"("p_class_id" "uuid") TO "authenticated";
GRANT ALL ON FUNCTION "public"."is_teacher_of_class"("p_class_id" "uuid") TO "service_role";

-- 2. Replace the recursive class/membership policies with helper-based ones.

DROP POLICY IF EXISTS "classes: member read" ON "public"."classes";
CREATE POLICY "classes: member read" ON "public"."classes"
  FOR SELECT USING ("public"."is_member_of_class"("id"));

DROP POLICY IF EXISTS "memberships: teacher read" ON "public"."class_memberships";
CREATE POLICY "memberships: teacher read" ON "public"."class_memberships"
  FOR SELECT USING ("public"."is_teacher_of_class"("class_id"));

-- 3. Teacher can read the profiles of students in their classes.

DROP POLICY IF EXISTS "profiles: teacher reads students" ON "public"."profiles";
CREATE POLICY "profiles: teacher reads students" ON "public"."profiles"
  FOR SELECT USING ((EXISTS ( SELECT 1
     FROM ("public"."class_memberships" "cm"
       JOIN "public"."classes" "c" ON (("c"."id" = "cm"."class_id")))
    WHERE (("cm"."student_id" = "profiles"."id") AND ("c"."teacher_id" = "auth"."uid"())))));

-- Reload the PostgREST schema cache so the new policies take effect immediately.
NOTIFY pgrst, 'reload schema';
