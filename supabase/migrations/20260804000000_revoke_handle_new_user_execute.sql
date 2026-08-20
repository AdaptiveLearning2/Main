-- Postgres grants EXECUTE on new functions to PUBLIC automatically, and
-- Supabase also grants it to anon/authenticated by name. A named grant
-- survives a REVOKE ... FROM PUBLIC, so both have to be revoked explicitly.
--
-- Harmless either way: this function returns trigger, so Postgres won't let
-- anyone call it directly and PostgREST won't expose it as an RPC. Revoking
-- anyway keeps this ACL from looking like an oversight next to the ones that
-- actually matter.
--
-- No GRANT is needed back: trigger functions run as the table owner, not as
-- the caller, so who may call this has no effect on what it does when it
-- fires -- and nothing currently creates a trigger that fires it.
REVOKE ALL ON FUNCTION "public"."handle_new_user"() FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."handle_new_user"() FROM "anon";
REVOKE ALL ON FUNCTION "public"."handle_new_user"() FROM "authenticated";

-- Not paired with ALTER DEFAULT PRIVILEGES to deny EXECUTE by default for
-- future functions -- tested and it doesn't work. The pg_default_acl row is
-- written correctly and the named anon/authenticated grants do disappear, but
-- Postgres's own PUBLIC grant survives regardless, so both roles can still
-- execute. Revoking per function is the only mechanism that works;
-- scripts/check_function_grants.py enforces it so a new function can't skip it.

NOTIFY pgrst, 'reload schema';
