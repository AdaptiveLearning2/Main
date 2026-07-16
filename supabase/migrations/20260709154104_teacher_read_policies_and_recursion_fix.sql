-- Reconciles version control with schema changes that were applied directly to
-- the remote database (via the Supabase SQL editor) on 2026-07-09 but never
-- committed as a migration. Recovered by dumping the live schema, since the
-- legacy `db pull` diff engine does not detect function bodies, policy
-- replacements, or grants.
--
-- Captures three related changes:
--   1. is_teacher_of_class / is_member_of_class SECURITY DEFINER helper functions.
--   2. The classes/class_memberships SELECT policies rewritten to call those
--      helpers -- replacing the original recursive policies from the init
--      migration that caused "infinite recursion detected in policy" errors.
--   3. The "profiles: teacher reads students" policy that lets a teacher read the
--      profile rows of students enrolled in any class they teach (required by the
--      teacher Students/Classes tabs; without it those lists come back empty).

-- 1. Helper functions -------------------------------------------------------
-- SECURITY DEFINER + fixed search_path lets these bypass RLS on the tables they
-- query, breaking the policy-evaluation cycle between classes and
-- class_memberships that caused the recursion error.

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

-- 2. Replace the recursive class/membership policies with helper-based ones --

DROP POLICY IF EXISTS "classes: member read" ON "public"."classes";
CREATE POLICY "classes: member read" ON "public"."classes"
  FOR SELECT USING ("public"."is_member_of_class"("id"));

DROP POLICY IF EXISTS "memberships: teacher read" ON "public"."class_memberships";
CREATE POLICY "memberships: teacher read" ON "public"."class_memberships"
  FOR SELECT USING ("public"."is_teacher_of_class"("class_id"));

-- 3. Teacher can read the profiles of students in their classes -------------

DROP POLICY IF EXISTS "profiles: teacher reads students" ON "public"."profiles";
CREATE POLICY "profiles: teacher reads students" ON "public"."profiles"
  FOR SELECT USING ((EXISTS ( SELECT 1
     FROM ("public"."class_memberships" "cm"
       JOIN "public"."classes" "c" ON (("c"."id" = "cm"."class_id")))
    WHERE (("cm"."student_id" = "profiles"."id") AND ("c"."teacher_id" = "auth"."uid"())))));

-- Reload the PostgREST schema cache so the new policies take effect immediately.
NOTIFY pgrst, 'reload schema';
