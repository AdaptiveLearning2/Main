-- Take back the default table grants on every remaining public table.
--
-- Supabase ships ALTER DEFAULT PRIVILEGES granting every table privilege to
-- anon and authenticated by name in public, so every table in this schema
-- arrived as anon=arwdDxtm,authenticated=arwdDxtm regardless of what its own
-- migration granted. heart_signals and signal_consent were fixed in
-- 20260805000000 and 20260805100000; this is the rest.
--
-- What is actually exposed, probed rather than assumed: INSERT, UPDATE and
-- DELETE are all filtered by RLS, so the permissive grant buys a caller
-- nothing there. TRUNCATE is not filtered by RLS, and as anon it succeeded on
-- every table here. PostgREST does not expose TRUNCATE, so the anon key in the
-- frontend bundle is not a path to it -- it needs a direct Postgres connection.
--
-- ── What this deliberately does NOT do ──────────────────────────────────────
--
-- It does not take DML away from authenticated. Most of these tables carry a
-- FOR ALL "own" policy that RLS evaluates against auth.uid(), and the frontend
-- relies on one of them: Adaptive.jsx:290 upserts user_math_performance
-- directly through PostgREST under "perf: own". Revoking INSERT/UPDATE would
-- break the adaptive page, and it would not close anything -- RLS is already
-- constraining those to the caller's own rows. The privilege that RLS does not
-- constrain is TRUNCATE, and that is what goes.
--
-- REVOKE ALL followed by a re-grant of the four DML privileges, rather than
-- REVOKE TRUNCATE on its own: it also drops REFERENCES, TRIGGER and MAINTAIN,
-- none of which a client role has any business holding, and it does so without
-- naming MAINTAIN, which does not exist before Postgres 17.

-- ── anon ────────────────────────────────────────────────────────────────────
-- anon holds nothing anywhere, with two deliberate exceptions below. Every
-- other policy in this schema is auth.uid()-scoped and auth.uid() is null for
-- anon, so the grants were returning nothing while reading as anonymous access
-- paths into student data.
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

-- The two exceptions. Both carry a "public read" policy with USING (true) --
-- deliberately readable without a session -- so revoking anon here would change
-- behaviour rather than tighten it.
GRANT SELECT ON TABLE "public"."math_topics" TO "anon";
GRANT SELECT ON TABLE "public"."questions" TO "anon";

-- ── authenticated ───────────────────────────────────────────────────────────
-- Keeps the DML that RLS filters; loses TRUNCATE, REFERENCES, TRIGGER and
-- MAINTAIN, which it never needed and which RLS does not constrain.
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

-- math_topics and questions are reference data with public-read policies and no
-- write policy for anyone, so authenticated gets SELECT only rather than the
-- DML the others keep.

-- ── sequences ───────────────────────────────────────────────────────────────
-- anon only. Sequence privileges are USAGE/SELECT/UPDATE with no TRUNCATE
-- equivalent, so there is no RLS-shaped hole here -- but anon has no reason to
-- advance a sequence on a table it cannot write. authenticated is left alone
-- deliberately: it needs USAGE to insert into the tables above that have serial
-- keys, and getting that wrong breaks inserts in a way that surfaces as a
-- constraint error rather than a permission one.
-- The four that exist. questions and session_answers use uuid keys and have no
-- sequence; heart_signals_id_seq was handled in its own migration.
REVOKE ALL ON SEQUENCE "public"."cognitive_signals_id_seq" FROM "anon";
REVOKE ALL ON SEQUENCE "public"."face_signals_id_seq" FROM "anon";
REVOKE ALL ON SEQUENCE "public"."math_topics_id_seq" FROM "anon";

NOTIFY pgrst, 'reload schema';
