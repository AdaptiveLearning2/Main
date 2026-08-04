-- Revoke EXECUTE on handle_new_user from the application roles.
--
-- It is the one function in the public schema whose permissive ACL is not
-- deliberate. 20260625000000_init.sql creates it and sets OWNER, but issues no
-- GRANT -- so anon and authenticated hold EXECUTE purely through Postgres's
-- automatic grant to PUBLIC on new functions, plus Supabase's ALTER DEFAULT
-- PRIVILEGES granting the two roles by name. Both are why REVOKE ... FROM
-- PUBLIC alone is not sufficient: an explicit grant to a named role survives a
-- revoke aimed at the PUBLIC pseudo-role.
--
-- Harmless today, which is why this is defence in depth rather than a fix: the
-- function returns trigger, Postgres refuses to invoke such a function
-- directly, and PostgREST will not expose it as an RPC. The value is in not
-- leaving a permissive ACL sitting next to the ones that matter, where it reads
-- as though nobody checked.
--
-- No matching GRANT is needed. Trigger functions execute as the table owner
-- rather than as the caller, so the on-signup insert into public.profiles is
-- unaffected by what anon and authenticated are allowed to call.
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
