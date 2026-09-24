-- face_signals and cognitive_signals: authenticated gets SELECT only. A
-- WITH CHECK on consent would still let a client fabricate readings; all
-- ingestion goes through the backend.

REVOKE ALL ON TABLE "public"."cognitive_signals" FROM "authenticated";
REVOKE ALL ON TABLE "public"."face_signals" FROM "authenticated";

GRANT SELECT ON TABLE "public"."cognitive_signals" TO "authenticated";
GRANT SELECT ON TABLE "public"."face_signals" TO "authenticated";

-- No INSERT, so no sequence USAGE.
REVOKE ALL ON SEQUENCE "public"."cognitive_signals_id_seq" FROM "authenticated";
REVOKE ALL ON SEQUENCE "public"."face_signals_id_seq" FROM "authenticated";

-- FOR SELECT, so a later INSERT grant cannot reuse USING as a write check.
DROP POLICY "cog: own" ON "public"."cognitive_signals";
CREATE POLICY "cog: own" ON "public"."cognitive_signals"
    FOR SELECT USING (("auth"."uid"() = "user_id"));

DROP POLICY "face: own" ON "public"."face_signals";
CREATE POLICY "face: own" ON "public"."face_signals"
    FOR SELECT USING (("auth"."uid"() = "user_id"));

NOTIFY pgrst, 'reload schema';
