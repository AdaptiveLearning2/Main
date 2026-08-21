-- Rename "stats: own write" to "stats: own row".
--
-- The policy is USING (auth.uid() = user_id) with no FOR clause, so it
-- applies to ALL commands -- including the SELECT of a student's own stats.
-- The name says "write", which undersells what it does.
--
-- "stats: own row" matches the behaviour and matches "perf: own" and
-- "sessions: own" on the neighbouring tables, neither of which claims to be
-- write-only either.

-- ALTER ... RENAME rather than DROP + CREATE. A rename preserves the policy
-- definition exactly, so there's no chance of the re-created version
-- drifting from the original, and no moment -- even inside the migration's
-- transaction -- where the table has no own-row policy on it.
--
-- Wrapped because ALTER POLICY has no IF EXISTS, and this has to be a no-op
-- rather than an error on a database where the rename already happened.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'user_stats'
      AND policyname = 'stats: own write'
  ) THEN
    ALTER POLICY "stats: own write" ON "public"."user_stats"
      RENAME TO "stats: own row";
  END IF;
END
$$;
