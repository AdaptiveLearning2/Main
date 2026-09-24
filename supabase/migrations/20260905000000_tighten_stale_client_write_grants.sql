-- Seven backend-written tables: authenticated keeps SELECT only. frontend/src
-- has no Supabase-client write; every write goes through the backend's
-- service-role client. The FOR ALL "own" policies become inert for writes.

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

NOTIFY pgrst, 'reload schema';
