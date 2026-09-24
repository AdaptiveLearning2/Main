-- Take back the default table grants on every remaining public table. RLS
-- never filters TRUNCATE. REVOKE ALL then re-grant DML, so TRUNCATE,
-- REFERENCES and TRIGGER go.

-- anon holds nothing, except the two public-read tables below.
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

-- Both carry a USING (true) public-read policy.
GRANT SELECT ON TABLE "public"."math_topics" TO "anon";
GRANT SELECT ON TABLE "public"."questions" TO "anon";

-- authenticated keeps the DML that RLS filters.
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

-- authenticated keeps sequence USAGE for inserts into serial-keyed tables.
REVOKE ALL ON SEQUENCE "public"."cognitive_signals_id_seq" FROM "anon";
REVOKE ALL ON SEQUENCE "public"."face_signals_id_seq" FROM "anon";
REVOKE ALL ON SEQUENCE "public"."math_topics_id_seq" FROM "anon";

NOTIFY pgrst, 'reload schema';
