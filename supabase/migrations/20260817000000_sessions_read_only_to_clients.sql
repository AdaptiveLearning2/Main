-- `sessions` is written by the backend only, so stop granting clients writes.
--
-- Before this, `sessions` carried `authenticated=arwd` alongside a `sessions:
-- own` policy with no FOR clause (so FOR ALL, USING auth.uid() = user_id),
-- which meant every command was permitted on a student's own rows through
-- PostgREST with the frontend's anon key.
--
-- That was a real reach, not hypothetical: it let a student rewrite any
-- column of their own sessions -- `chart_paths` to point at another child's
-- chart object, `started_at`/`ended_at` which drive the daily rollup's
-- bucketing and the end-of-year expiry cutoff, or a DELETE that cascades
-- `cognitive_signals`, `face_signals` and `heart_signals` away.
--
-- Nothing loses a capability it was using -- there's no client-side write or
-- read of `sessions` anywhere in the frontend; it reaches the browser only
-- through the backend's service-role client, which bypasses RLS. This
-- restores the usual rule: a table written only by the backend gets SELECT
-- for `authenticated` and nothing more.
--
-- SELECT is kept rather than revoked: the table's two read policies (a
-- student's own rows, a teacher's students' rows) are the kind of thing a
-- future client-side read would rely on, and a read leaks nothing beyond
-- what the backend already serves.
--
-- REVOKE then re-GRANT, not GRANT alone: Supabase already granted every
-- privilege to these roles by name, and a named grant isn't narrowed by
-- adding a smaller one -- only a REVOKE removes it.

REVOKE ALL ON TABLE "public"."sessions" FROM "anon";
REVOKE ALL ON TABLE "public"."sessions" FROM "authenticated";

GRANT SELECT ON TABLE "public"."sessions" TO "authenticated";
GRANT ALL ON TABLE "public"."sessions" TO "service_role";

-- `anon` gets nothing back. RLS already denied every row for it, but RLS
-- doesn't filter TRUNCATE, which only needs the table privilege and a direct
-- Postgres connection -- "not reachable from the client we ship" is a weaker
-- guarantee than a revoked grant.

NOTIFY pgrst, 'reload schema';
