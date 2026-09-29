-- The two RLS helpers are SECURITY DEFINER with search_path 'public' and bare table names, and
-- pg_temp is searched first for relations: a temp table named class_memberships would answer for it.
-- An empty search_path with qualified names closes that. Same signatures, so the grants stand.

CREATE OR REPLACE FUNCTION "public"."is_member_of_class"("p_class_id" "uuid") RETURNS boolean
    LANGUAGE "sql" STABLE SECURITY DEFINER
    SET "search_path" TO ''
    AS $$
  select exists (
    select 1 from public.class_memberships
    where class_id = p_class_id and student_id = auth.uid()
  );
$$;

CREATE OR REPLACE FUNCTION "public"."is_teacher_of_class"("p_class_id" "uuid") RETURNS boolean
    LANGUAGE "sql" STABLE SECURITY DEFINER
    SET "search_path" TO ''
    AS $$
  select exists (
    select 1 from public.classes
    where id = p_class_id and teacher_id = auth.uid()
  );
$$;

NOTIFY pgrst, 'reload schema';
