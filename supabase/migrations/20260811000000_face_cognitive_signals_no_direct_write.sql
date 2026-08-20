-- face_signals and cognitive_signals stop being directly writable by
-- authenticated.
--
-- "cog: own" and "face: own" were bare FOR ALL policies with USING
-- (auth.uid() = user_id) and no WITH CHECK, so Postgres reused that same
-- USING clause as the insert check -- meaning the only check that ran was
-- "is this my row", never "have I consented to this sensor". A student's own
-- browser session could INSERT straight into either table with no
-- signal_consent row ever consulted. heart_signals never had this hole; this
-- migration brings the other two in line with it.
--
-- The fix is not a WITH CHECK against signal_consent -- that would still let
-- a consented student's own client fabricate arbitrary readings, with none of
-- the backend's consent-recheck cadence, rate limiting, or batch bounds in
-- the way. Nothing legitimate writes these tables through PostgREST at all:
-- ingestion always goes through the service-role client, which bypasses RLS.
-- So the correct rule is "authenticated needs SELECT and nothing else" -- no
-- write door at all, not a narrower one.

REVOKE ALL ON TABLE "public"."cognitive_signals" FROM "authenticated";
REVOKE ALL ON TABLE "public"."face_signals" FROM "authenticated";

GRANT SELECT ON TABLE "public"."cognitive_signals" TO "authenticated";
GRANT SELECT ON TABLE "public"."face_signals" TO "authenticated";

-- authenticated no longer needs sequence USAGE now that it can't INSERT into
-- either table. Not a hole on its own, but an unjustified grant is exactly
-- what a future ACL audit shouldn't have to puzzle out.
REVOKE ALL ON SEQUENCE "public"."cognitive_signals_id_seq" FROM "authenticated";
REVOKE ALL ON SEQUENCE "public"."face_signals_id_seq" FROM "authenticated";

-- Replacing the bare FOR ALL policies with FOR SELECT matters beyond the
-- grant: with no WITH CHECK, re-adding INSERT to the grant later would
-- silently reopen this same gap if the policy still covered every command.
DROP POLICY "cog: own" ON "public"."cognitive_signals";
CREATE POLICY "cog: own" ON "public"."cognitive_signals"
    FOR SELECT USING (("auth"."uid"() = "user_id"));

DROP POLICY "face: own" ON "public"."face_signals";
CREATE POLICY "face: own" ON "public"."face_signals"
    FOR SELECT USING (("auth"."uid"() = "user_id"));

NOTIFY pgrst, 'reload schema';
