-- Take back the table privileges signal_consent was created with.
--
-- 20260804120000 granted SELECT to authenticated and said "no grant to anon at
-- all". The database disagreed: Supabase ships ALTER DEFAULT PRIVILEGES
-- granting every table privilege to anon and authenticated **by name** in the
-- public schema, so the table arrived as
--
--   anon=arwdDxtm/postgres, authenticated=arwdDxtm/postgres
--
-- before that file granted anything, and a narrow GRANT on top narrowed
-- nothing. This is the table-level twin of the function trap CLAUDE.md
-- documents, and it has the same shape: an explicit grant to a named role is
-- not undone by adding a smaller one, only by revoking.
--
-- What that actually exposed, probed rather than assumed:
--
--   INSERT/UPDATE/DELETE  denied -- the table has no policy for them, and RLS
--                         is what was doing the work all along
--   TRUNCATE              SUCCEEDED as anon -- RLS does not filter TRUNCATE
--
-- Truncating signal_consent fails closed rather than open: with no row, every
-- channel reads as denied, so the result is that recording stops for everyone
-- rather than starting for anyone. That is the right direction and still not an
-- acceptable thing for anon to be able to do. Reaching it needs a direct
-- Postgres connection -- PostgREST does not expose TRUNCATE, so the anon key in
-- the frontend bundle is not a path to it.
--
-- face_signals, cognitive_signals, profiles and sessions all carry the same
-- ACL. Fixing those is a repo-wide change with its own review, and belongs with
-- a CI check mirroring scripts/check_function_grants.py rather than being
-- smuggled in here. This file covers only the table whose own migration made
-- the claim.

REVOKE ALL ON TABLE "public"."signal_consent" FROM "anon";
REVOKE ALL ON TABLE "public"."signal_consent" FROM "authenticated";

-- Re-granting the read the policies exist to serve. The student, a linked
-- parent and a teacher of the student's class each have a SELECT policy;
-- without this grant those policies would have nothing to filter.
GRANT SELECT ON TABLE "public"."signal_consent" TO "authenticated";
GRANT ALL ON TABLE "public"."signal_consent" TO "service_role";

NOTIFY pgrst, 'reload schema';
