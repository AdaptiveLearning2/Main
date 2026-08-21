-- Take back the default table grants on every remaining public table. Supabase
-- grants every table privilege to anon and authenticated by name on every new
-- table, regardless of what its own migration granted. heart_signals and
-- signal_consent were already fixed; this covers the rest.
--
-- RLS already filters INSERT/UPDATE/DELETE, so the wide grant buys nothing
-- there -- but RLS doesn't filter TRUNCATE, and as anon it succeeded on every
-- table here. PostgREST doesn't expose TRUNCATE, so the frontend's anon key
-- can't reach it, but a direct Postgres connection could.
--
-- This does not take DML away from authenticated. Most tables carry a FOR ALL
-- "own" policy, and the frontend relies on one: Adaptive.jsx upserts
-- user_math_performance directly through PostgREST. Revoking INSERT/UPDATE
-- would break that and wouldn't close anything, since RLS already constrains
-- it to the caller's own rows. Only TRUNCATE, which RLS can't constrain, is
-- actually removed.
--
-- REVOKE ALL then re-grant the four DML privileges, rather than revoking just
-- TRUNCATE, so REFERENCES and TRIGGER go too -- neither belongs to a client
-- role either.

-- anon holds nothing anywhere, except the two public-read tables below. Every
-- other policy here is auth.uid()-scoped, which is null for anon, so those
-- grants were returning nothing while reading as an anonymous path into
-- student data.
REVOKE ALL ON TABLE "public"."class_memberships" FROM "anon";
REVOKE ALL ON TABLE "public"."classes" FROM "anon";
REVOKE ALL ON TABLE "public"."cognitive_signals" FROM "anon";
REVOKE ALL ON TABLE "public"."face_signals" FROM "anon";
REVOKE ALL ON TABLE "public"."math_topics" FROM "anon";
REVOKE ALL ON TABLE "public"."parent_child_links" FROM "anon";
REVOKE ALL ON TABLE "public"."profiles" FROM "anon";
REVOKE ALL ON TABLE "public"."questions" FROM "anon";
REVOKE ALL ON TABLE "public"."session_answers" FROM "anon";
REVOKE ALL ON TABLE "public"."sessions" FROM "anon";
REVOKE ALL ON TABLE "public"."user_math_performance" FROM "anon";
REVOKE ALL ON TABLE "public"."user_stats" FROM "anon";

-- The two exceptions carry a public-read policy meant to work without a
-- session, so revoking anon here would change behavior, not tighten it.
GRANT SELECT ON TABLE "public"."math_topics" TO "anon";
GRANT SELECT ON TABLE "public"."questions" TO "anon";

-- authenticated keeps the DML that RLS filters; loses TRUNCATE, REFERENCES
-- and TRIGGER, which it never needed.
REVOKE ALL ON TABLE "public"."class_memberships" FROM "authenticated";
REVOKE ALL ON TABLE "public"."classes" FROM "authenticated";
REVOKE ALL ON TABLE "public"."cognitive_signals" FROM "authenticated";
REVOKE ALL ON TABLE "public"."face_signals" FROM "authenticated";
REVOKE ALL ON TABLE "public"."math_topics" FROM "authenticated";
REVOKE ALL ON TABLE "public"."parent_child_links" FROM "authenticated";
REVOKE ALL ON TABLE "public"."profiles" FROM "authenticated";
REVOKE ALL ON TABLE "public"."questions" FROM "authenticated";
REVOKE ALL ON TABLE "public"."session_answers" FROM "authenticated";
REVOKE ALL ON TABLE "public"."sessions" FROM "authenticated";
REVOKE ALL ON TABLE "public"."user_math_performance" FROM "authenticated";
REVOKE ALL ON TABLE "public"."user_stats" FROM "authenticated";

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "public"."class_memberships" TO "authenticated";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "public"."classes" TO "authenticated";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "public"."cognitive_signals" TO "authenticated";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "public"."face_signals" TO "authenticated";
GRANT SELECT ON TABLE "public"."math_topics" TO "authenticated";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "public"."parent_child_links" TO "authenticated";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "public"."profiles" TO "authenticated";
GRANT SELECT ON TABLE "public"."questions" TO "authenticated";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "public"."session_answers" TO "authenticated";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "public"."sessions" TO "authenticated";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "public"."user_math_performance" TO "authenticated";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "public"."user_stats" TO "authenticated";

-- math_topics and questions are reference data with public-read policies and
-- no write policy for anyone, so authenticated gets SELECT only.

-- anon has no reason to advance a sequence on a table it can't write to.
-- authenticated is left alone -- it needs USAGE to insert into the tables
-- above that have serial keys, and revoking it would break inserts.
REVOKE ALL ON SEQUENCE "public"."cognitive_signals_id_seq" FROM "anon";
REVOKE ALL ON SEQUENCE "public"."face_signals_id_seq" FROM "anon";
REVOKE ALL ON SEQUENCE "public"."math_topics_id_seq" FROM "anon";

NOTIFY pgrst, 'reload schema';
