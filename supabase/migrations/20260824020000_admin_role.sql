-- `admin` becomes a fourth `profiles.role`, and sign-up loses the ability to
-- choose a role that is not one of the three a person picks for themselves.
--
-- The two halves are one change. Widening the CHECK on its own would be a
-- self-service admin signup: `handle_new_user` copies
-- `raw_user_meta_data->>'role'` straight into the column, and that metadata is
-- whatever the registration form sent -- so `signUp({data:{role:'admin'}})`
-- from a browser console would have produced an administrator. The whitelist
-- below is what makes the wider CHECK safe, and neither should be applied
-- without the other.
--
-- Admin can be a role at all only because 20260824010000 revoked UPDATE and
-- INSERT on this column from `anon` and `authenticated`. Before that it was a
-- client-writable field and would have been the worst possible place to record
-- who administers the platform.

-- ── the role ────────────────────────────────────────────────────────────────

ALTER TABLE "public"."profiles" DROP CONSTRAINT IF EXISTS "profiles_role_check";
ALTER TABLE "public"."profiles" ADD CONSTRAINT "profiles_role_check"
    CHECK ("role" = ANY (ARRAY['student'::text, 'teacher'::text,
                               'parent'::text, 'admin'::text]));

-- ── sign-up may no longer name the role it likes ────────────────────────────
--
-- Three values, listed rather than excluded. A blacklist of 'admin' would admit
-- every *future* privileged role by default, and the next one would be added by
-- someone who never reads this file.
--
-- An unrecognised value becomes 'student' rather than raising. This runs inside
-- the auth transaction, so raising here fails the whole sign-up -- and a
-- malformed role is not a reason to refuse someone an account. 'student' is the
-- value that grants nothing, which is the right direction to fail in.
--
-- `SET search_path` added while here: this is SECURITY DEFINER and was unpinned,
-- which CLAUDE.md calls out as the classic escalation vector.
--
-- **This function may not be wired to anything.** 20260804000000 records that no
-- migration creates a trigger on auth.users and that a user created through the
-- admin API got no profiles row -- so production either has a hand-made trigger
-- that the migrations do not describe, or profiles rows arrive some other way.
-- Replacing the function is still right: if a trigger exists this closes the
-- hole, and if one is added later it is closed in advance. It is not a
-- substitute for answering the drift question:
--
--   SELECT tgname, tgrelid::regclass, tgenabled FROM pg_trigger
--   WHERE NOT tgisinternal AND tgrelid = 'auth.users'::regclass;

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

-- CREATE OR REPLACE keeps the existing ACL, so 20260804000000's revokes survive.
-- Restated anyway: this function's grants are the one set in this schema that
-- were permissive by accident, and a reader should be able to see the current
-- state without diffing two migrations.
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
