-- `sessions` is written by the backend only, so stop granting clients writes.
--
-- Found while reviewing the archived-chart endpoint, which now derives the
-- object path instead of trusting `chart_paths`. That endpoint fix is one
-- layer; this is the second, and it closes a wider hole than the one that
-- led here.
--
-- The state this replaces: `sessions` carried `authenticated=arwd` -- SELECT,
-- INSERT, UPDATE, DELETE -- alongside a `sessions: own` policy with no `FOR`
-- clause, so `FOR ALL`, `USING (auth.uid() = user_id)`. Every command was
-- therefore permitted on a student's own rows through PostgREST with the anon
-- key that ships in the frontend bundle.
--
-- That is not a hypothetical reach. It let a student rewrite any column of
-- their own sessions: `chart_paths` to address another child's chart object,
-- but equally `started_at`/`ended_at` (which drive the daily rollup's school-day
-- bucketing and the end-of-year expiry cutoff), or a DELETE that cascades
-- `cognitive_signals`, `face_signals` and `heart_signals` away. None of those
-- were reachable through any code we ship; all of them were reachable.
--
-- Nothing loses a capability it was using. Grepped `frontend/src` for
-- `from('sessions')` and there is no client-side write, or read, at all --
-- sessions reach the browser through the backend, which uses the service-role
-- client and bypasses RLS entirely. The convention this restores is the one in
-- CLAUDE.md: a table written only by the backend gets `SELECT` for
-- `authenticated` and nothing more.
--
-- `SELECT` is kept rather than revoked outright. The two policies on the table
-- are read policies -- a student's own rows, and a teacher's students' rows --
-- and they are the kind of thing a future client-side read would rely on. A
-- read that leaks nothing beyond what the backend already serves is a different
-- risk from a write.
--
-- REVOKE and then re-GRANT, not GRANT alone: Supabase's `ALTER DEFAULT
-- PRIVILEGES` granted every privilege to these roles by name, and an explicit
-- named grant is not narrowed by adding another one. Only a REVOKE removes it.

REVOKE ALL ON TABLE "public"."sessions" FROM "anon";
REVOKE ALL ON TABLE "public"."sessions" FROM "authenticated";

GRANT SELECT ON TABLE "public"."sessions" TO "authenticated";
GRANT ALL ON TABLE "public"."sessions" TO "service_role";

-- `anon` gets nothing back. It held the same four privileges and there is no
-- policy for an unauthenticated caller, so RLS denied every row -- but RLS does
-- not filter TRUNCATE, which needs only the table privilege and a direct
-- Postgres connection. "Not reachable from the client we ship" is a weaker
-- property than a revoked grant.

NOTIFY pgrst, 'reload schema';
