-- Take back the table privileges signal_consent was created with. Supabase
-- grants every table privilege to anon and authenticated by name on a new
-- table, so it arrived wide open regardless of what the previous migration's
-- GRANT said -- a narrow GRANT on top doesn't narrow an existing wide one,
-- only a REVOKE does.
--
-- Checked what that actually exposed: INSERT/UPDATE/DELETE were denied by RLS
-- (no policy for them), but TRUNCATE succeeded as anon -- RLS doesn't filter
-- TRUNCATE. A truncated signal_consent fails closed (no row reads as no
-- consent, so recording stops rather than starts), but anon still shouldn't
-- be able to do it. Reaching it needs a direct Postgres connection --
-- PostgREST doesn't expose TRUNCATE, so the frontend's anon key can't reach it
-- either way.
--
-- face_signals, cognitive_signals, profiles and sessions carry the same ACL
-- gap; fixing those is a separate repo-wide change.

REVOKE ALL ON TABLE "public"."signal_consent" FROM "anon";
REVOKE ALL ON TABLE "public"."signal_consent" FROM "authenticated";

-- Re-granting the read the policies exist to serve. The student, a linked
-- parent and a teacher of the student's class each have a SELECT policy;
-- without this grant those policies would have nothing to filter.
GRANT SELECT ON TABLE "public"."signal_consent" TO "authenticated";
GRANT ALL ON TABLE "public"."signal_consent" TO "service_role";

NOTIFY pgrst, 'reload schema';
