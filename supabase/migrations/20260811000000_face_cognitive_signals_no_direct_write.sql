-- face_signals and cognitive_signals stop being directly writable by
-- authenticated. Closes #47.
--
-- 20260805110000 left both tables' full DML grant in place, reasoning that RLS
-- was already constraining every table's "own" policy to the caller's own row
-- and TRUNCATE was the only real hole. That reasoning holds for tables like
-- user_math_performance, where "it is my row" is the entire write rule. It does
-- not hold here: "cog: own" and "face: own" are bare FOR ALL policies with
-- USING (auth.uid() = user_id) and no WITH CHECK, so Postgres reuses that same
-- USING clause as the insert check. The check that runs is "is this my row",
-- never "have I consented to this sensor" -- so a student's own session,
-- holding nothing but the anon key that ships in the frontend bundle, can
-- INSERT straight into either table with no signal_consent row ever consulted.
-- heart_signals was built after this was known and deliberately did not copy
-- it (see its own migration's comment); this file brings the other two in line.
--
-- The fix is not a WITH CHECK against signal_consent. That was the first shape
-- considered and it is weaker than it looks: a *consented* student's own client
-- could still construct arbitrary readings for itself -- a fabricated calm
-- score, an invented emotion -- with none of the backend's consent-recheck
-- cadence, rate limiting, batch bounds or row shape enforcement in the way,
-- since none of that lives in Postgres. Nothing legitimate writes these tables
-- through PostgREST at all: `eeg_poller` and the push-ingest endpoints both
-- write through the service-role client, which bypasses RLS entirely. So the
-- correct rule is the one heart_signals already states -- "authenticated needs
-- SELECT and nothing else" -- not a narrower door, no door.

REVOKE ALL ON TABLE "public"."cognitive_signals" FROM "authenticated";
REVOKE ALL ON TABLE "public"."face_signals" FROM "authenticated";

GRANT SELECT ON TABLE "public"."cognitive_signals" TO "authenticated";
GRANT SELECT ON TABLE "public"."face_signals" TO "authenticated";

-- 20260805110000 left authenticated's sequence USAGE grant in place
-- "deliberately: it needs USAGE to insert into the tables above that have
-- serial keys" -- true when it wrote that, and no longer true for these two
-- now that authenticated cannot INSERT into either table at all. Not a hole
-- on its own (PostgREST has no path to a bare nextval() call, same footnote
-- as the TRUNCATE one above), but a grant with no remaining justification is
-- exactly what the next ACL audit shouldn't have to puzzle out.
REVOKE ALL ON SEQUENCE "public"."cognitive_signals_id_seq" FROM "authenticated";
REVOKE ALL ON SEQUENCE "public"."face_signals_id_seq" FROM "authenticated";

-- The bare FOR ALL policies are what let the revoked grant matter again the
-- next time someone widens it "just for now" -- with no WITH CHECK, Postgres
-- reuses the USING expression as the insert/update check, so re-adding INSERT
-- would silently reopen this exact gap. Replaced with the FOR SELECT form
-- heart_signals uses, so the policy and the grant say the same thing.
DROP POLICY "cog: own" ON "public"."cognitive_signals";
CREATE POLICY "cog: own" ON "public"."cognitive_signals"
    FOR SELECT USING (("auth"."uid"() = "user_id"));

DROP POLICY "face: own" ON "public"."face_signals";
CREATE POLICY "face: own" ON "public"."face_signals"
    FOR SELECT USING (("auth"."uid"() = "user_id"));

NOTIFY pgrst, 'reload schema';
