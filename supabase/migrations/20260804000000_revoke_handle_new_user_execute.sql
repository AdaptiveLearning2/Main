-- Revoke EXECUTE on handle_new_user from the application roles.
--
-- It is the one function in the public schema whose permissive ACL is not
-- deliberate. 20260625000000_init.sql:682-684 grants ALL on it to anon,
-- authenticated and service_role explicitly, on top of Postgres's automatic
-- grant of EXECUTE to PUBLIC on any new function and Supabase's ALTER DEFAULT
-- PRIVILEGES granting the same two roles by name. Those named grants are why
-- REVOKE ... FROM PUBLIC alone is not sufficient: an explicit grant to a named
-- role survives a revoke aimed at the PUBLIC pseudo-role.
--
-- Harmless today, which is why this is defence in depth rather than a fix: the
-- function returns trigger, Postgres refuses to invoke such a function
-- directly, and PostgREST will not expose it as an RPC. The value is in not
-- leaving a permissive ACL sitting next to the ones that matter, where it reads
-- as though nobody checked.
--
-- No matching GRANT is needed. Trigger functions execute as the table owner
-- rather than as the caller, so nothing about who may CALL this affects what it
-- does when fired.
--
-- Which, as of 2026-08-05, is nothing: **no migration in this repo creates a
-- trigger on auth.users**, and neither the frontend (AuthContext.jsx:37 calls
-- supabase.auth.signUp and stops) nor the backend inserts into public.profiles.
-- The function's body reads raw_user_meta_data->>'display_name' and 'role',
-- which is exactly what signUp puts there, so it was plainly written to be the
-- signup path and was never wired to one. Confirmed empirically: a user created
-- through the admin API gets no profiles row.
--
-- Not fixed here, because the right fix depends on something only the
-- production database can answer -- whether a trigger was created by hand in
-- the dashboard, in which case the migrations do not describe production and
-- that drift is the actual problem:
--
--   SELECT tgname, tgrelid::regclass, tgenabled FROM pg_trigger
--   WHERE NOT tgisinternal AND tgrelid = 'auth.users'::regclass;
REVOKE ALL ON FUNCTION "public"."handle_new_user"() FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."handle_new_user"() FROM "anon";
REVOKE ALL ON FUNCTION "public"."handle_new_user"() FROM "authenticated";

-- Deliberately NOT paired with an ALTER DEFAULT PRIVILEGES making EXECUTE
-- deny-by-default for future functions. That was the original intent here and
-- it does not work: tested against a local Supabase stack on 2026-08-04, the
-- pg_default_acl row records correctly and the anon/authenticated named grants
-- do disappear from new functions, but the PUBLIC grant (`=X`) survives and
-- both roles can still execute. Reproduced with three throwaway functions, with
-- the grantees combined into one statement and separated, and with no event
-- trigger re-granting afterwards.
--
-- The per-function REVOKE block stays the only mechanism, and
-- scripts/check_function_grants.py is what stops it being forgotten.

NOTIFY pgrst, 'reload schema';
