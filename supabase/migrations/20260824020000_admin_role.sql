-- `admin` becomes a fourth profiles.role. The handle_new_user whitelist below
-- is what makes the wider CHECK safe: it copies client-supplied metadata.

ALTER TABLE "public"."profiles" DROP CONSTRAINT IF EXISTS "profiles_role_check";
ALTER TABLE "public"."profiles" ADD CONSTRAINT "profiles_role_check"
    CHECK ("role" = ANY (ARRAY['student'::text, 'teacher'::text,
                               'parent'::text, 'admin'::text]));

-- Whitelist, not blacklist. Unknown values become 'student' rather than
-- raising inside the auth transaction. search_path pinned: SECURITY DEFINER.

CREATE OR REPLACE FUNCTION "public"."handle_new_user"() RETURNS "trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    SET "search_path" TO 'public'
    AS $$
declare
  requested text := new.raw_user_meta_data->>'role';
begin
  insert into public.profiles (id, display_name, email, role)
  values (
    new.id,
    coalesce(new.raw_user_meta_data->>'display_name', split_part(new.email, '@', 1)),
    new.email,
    case
      when requested in ('student', 'teacher', 'parent') then requested
      else 'student'
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

COMMENT ON COLUMN "public"."profiles"."role" IS
    'Server-controlled. student|teacher|parent are chosen at sign-up; admin is '
    'set only by service_role or the dashboard SQL editor -- handle_new_user '
    'whitelists the first three, and UPDATE/INSERT on this column are revoked '
    'from anon and authenticated. The client-writable user_metadata.role is not '
    'a gate and must not be read as one.';

NOTIFY pgrst, 'reload schema';
