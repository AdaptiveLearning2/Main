-- Take back signal_consent's default grants: a new table arrives fully granted
-- and only a REVOKE narrows it. RLS never filters TRUNCATE.

REVOKE ALL ON TABLE "public"."signal_consent" FROM "anon";
REVOKE ALL ON TABLE "public"."signal_consent" FROM "authenticated";

-- The SELECT policies need this grant to have anything to filter.
GRANT SELECT ON TABLE "public"."signal_consent" TO "authenticated";
GRANT ALL ON TABLE "public"."signal_consent" TO "service_role";

NOTIFY pgrst, 'reload schema';
