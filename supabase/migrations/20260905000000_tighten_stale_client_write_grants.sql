-- Seven tables still grant `authenticated` full INSERT/UPDATE/DELETE on a
-- "the frontend writes this directly" rationale that current code no longer
-- matches -- confirmed by a repo-wide grep of frontend/src for
-- `.insert(`/`.update(`/`.upsert(`/`.delete(` against the Supabase client:
-- zero matches. Every write in this app now goes through backend/main.py's
-- service-role client, which bypasses RLS and these grants entirely, so the
-- grants below have been dead capability for a while, not a live path.
--
-- class_memberships, classes: written only via POST /api/classes and
--   POST /api/classes/join.
-- profiles: written only via PUT /api/profile/me, including display name,
--   grade, and the learning preferences -- the exact fields
--   20260824010000_profiles_role_is_not_client_writable.sql's own comment
--   described as needing to stay client-writable. That endpoint already
--   uses the service-role client (`supabase.table("profiles").update(...)`),
--   so nothing there depends on the authenticated-role grant either; only
--   `role` had already been carved out at the column level, and this
--   migration does not touch that narrower revoke.
-- parent_child_links: written via the linking flow's backend endpoints (the
--   ack-related columns were already column-revoked in 20260828000000 /
--   20260829000000; this closes the rest of the row).
-- user_math_performance: the client-side upsert this table's original grant
--   existed for was deleted and replaced by the server-side, service-role
--   `record_topic_attempt()` RPC (20260825000000) -- so the grant this table
--   still carries is exactly the vulnerability that RPC was written to
--   close, left open one layer up. Adaptive.jsx's own comment now reads:
--   "The backend owns user_math_performance and derives the topic itself --
--   this page must not write to it directly."
-- user_stats: no dedicated write UI found; same treatment as
--   user_math_performance.
-- session_answers: written via POST /api/sessions/{id}/answer, and updated
--   atomically via the bump_session_counters / record_topic_attempt RPCs
--   (20260826000000) -- the exact read-modify-write race those RPCs exist to
--   prevent is otherwise still reachable directly through PostgREST.
--
-- Same pattern as 20260817000000 (sessions) and 20260811000000
-- (cognitive_signals/face_signals): REVOKE ALL, then GRANT SELECT back to
-- authenticated, because a named grant isn't narrowed by adding a smaller
-- one -- only a REVOKE removes it. No RLS policy changes needed: each
-- table's `FOR ALL "own"` policy becomes dead code for the commands this
-- revokes, not something that needs removing.

REVOKE ALL ON TABLE "public"."class_memberships" FROM "anon";
REVOKE ALL ON TABLE "public"."class_memberships" FROM "authenticated";
GRANT SELECT ON TABLE "public"."class_memberships" TO "authenticated";

REVOKE ALL ON TABLE "public"."classes" FROM "anon";
REVOKE ALL ON TABLE "public"."classes" FROM "authenticated";
GRANT SELECT ON TABLE "public"."classes" TO "authenticated";

REVOKE ALL ON TABLE "public"."profiles" FROM "anon";
REVOKE ALL ON TABLE "public"."profiles" FROM "authenticated";
GRANT SELECT ON TABLE "public"."profiles" TO "authenticated";

REVOKE ALL ON TABLE "public"."parent_child_links" FROM "anon";
REVOKE ALL ON TABLE "public"."parent_child_links" FROM "authenticated";
GRANT SELECT ON TABLE "public"."parent_child_links" TO "authenticated";

REVOKE ALL ON TABLE "public"."user_math_performance" FROM "anon";
REVOKE ALL ON TABLE "public"."user_math_performance" FROM "authenticated";
GRANT SELECT ON TABLE "public"."user_math_performance" TO "authenticated";

REVOKE ALL ON TABLE "public"."user_stats" FROM "anon";
REVOKE ALL ON TABLE "public"."user_stats" FROM "authenticated";
GRANT SELECT ON TABLE "public"."user_stats" TO "authenticated";

REVOKE ALL ON TABLE "public"."session_answers" FROM "anon";
REVOKE ALL ON TABLE "public"."session_answers" FROM "authenticated";
GRANT SELECT ON TABLE "public"."session_answers" TO "authenticated";

-- service_role already held ALL on every one of these from the original
-- table-creation grants and is untouched by the REVOKEs above (they name
-- anon/authenticated only).

NOTIFY pgrst, 'reload schema';
