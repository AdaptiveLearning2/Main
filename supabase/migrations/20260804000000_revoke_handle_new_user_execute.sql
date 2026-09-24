-- Named anon/authenticated grants survive a revoke FROM PUBLIC, so each is
-- revoked. A trigger function is not callable directly, so no grant back.
REVOKE ALL ON FUNCTION "public"."handle_new_user"() FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."handle_new_user"() FROM "anon";
REVOKE ALL ON FUNCTION "public"."handle_new_user"() FROM "authenticated";

-- ALTER DEFAULT PRIVILEGES cannot deny EXECUTE (the PUBLIC grant survives);
-- revoke per function. scripts/check_function_grants.py enforces it.

NOTIFY pgrst, 'reload schema';
