-- `admin` becomes a fourth `profiles.role`, and sign-up loses the ability to
-- choose a role that isn't one of the three a person picks for themselves.
--
-- The two halves are one change. Widening the CHECK alone would allow
-- self-service admin signup: `handle_new_user` copies
-- `raw_user_meta_data->>'role'` straight into the column, which is whatever
-- the registration form sent -- so `signUp({data:{role:'admin'}})` from a
-- browser console would have produced an administrator. The whitelist below
-- is what makes the wider CHECK safe.
--
-- Admin can be a role at all only because the previous migration revoked
-- UPDATE and INSERT on this column from `anon` and `authenticated`. Before
-- that it was client-writable and would have been the worst possible place
-- to record who administers the platform.

ALTER TABLE "public"."profiles" DROP CONSTRAINT IF EXISTS "profiles_role_check";
ALTER TABLE "public"."profiles" ADD CONSTRAINT "profiles_role_check"
    CHECK ("role" = ANY (ARRAY['student'::text, 'teacher'::text,
                               'parent'::text, 'admin'::text]));

-- Sign-up may no longer name the role it likes. Three values, listed rather
-- than excluded -- a blacklist of just 'admin' would admit every future
-- privileged role by default.
--
-- An unrecognised value becomes 'student' rather than raising: this runs
-- inside the auth transaction, so raising would fail the whole sign-up over
-- a malformed role. 'student' is the value that grants nothing.
--
-- `SET search_path` added while here, since this is SECURITY DEFINER and was
-- unpinned -- a classic escalation vector.

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

-- CREATE OR REPLACE keeps the existing ACL, so the earlier revokes survive.
-- Restated anyway, so a reader can see the current state without diffing two
-- migrations.
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
