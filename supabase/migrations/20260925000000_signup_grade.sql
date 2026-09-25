-- Sign-up now carries a student's grade. raw_user_meta_data is client-supplied, so the grade is
-- kept only if it is one of the sign-up dropdown's labels (frontend lib/grades.js); anything
-- else, and any grade on a teacher or parent, is stored as no grade rather than raising.
-- The display name is cut to the backend's _NAME_MAX (100), which a profile edit already enforces.

CREATE OR REPLACE FUNCTION "public"."handle_new_user"() RETURNS "trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    SET "search_path" TO 'public'
    AS $$
declare
  requested text := new.raw_user_meta_data->>'role';
  chosen    text := case when requested in ('student', 'teacher', 'parent') then requested
                         else 'student' end;
  grade     text := new.raw_user_meta_data->>'grade_level';
begin
  insert into public.profiles (id, display_name, email, role, grade_level)
  values (
    new.id,
    left(coalesce(new.raw_user_meta_data->>'display_name', split_part(new.email, '@', 1)), 100),
    new.email,
    chosen,
    case
      when chosen = 'student' and grade in ('Kindergarten', '1st Grade', '2nd Grade', '3rd Grade',
                                            '4th Grade', '5th Grade', '6th Grade', '7th Grade',
                                            '8th Grade', 'Highschool', 'College') then grade
    end
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

-- ACL kept by CREATE OR REPLACE; restated.
REVOKE ALL ON FUNCTION "public"."handle_new_user"() FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."handle_new_user"() FROM "anon";
REVOKE ALL ON FUNCTION "public"."handle_new_user"() FROM "authenticated";

NOTIFY pgrst, 'reload schema';
