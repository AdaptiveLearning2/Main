-- `sessions` is written by the backend only: authenticated gets SELECT.
-- The FOR ALL own-row policy let a student rewrite chart_paths, started_at /
-- ended_at, or DELETE and cascade the signal tables.

REVOKE ALL ON TABLE "public"."sessions" FROM "anon";
REVOKE ALL ON TABLE "public"."sessions" FROM "authenticated";

GRANT SELECT ON TABLE "public"."sessions" TO "authenticated";
GRANT ALL ON TABLE "public"."sessions" TO "service_role";

-- anon gets nothing back: RLS never filters TRUNCATE.

NOTIFY pgrst, 'reload schema';
