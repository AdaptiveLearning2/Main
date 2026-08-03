-- Rename "stats: own write" to "stats: own row".
--
-- The policy is USING (auth.uid() = user_id) with no FOR clause, so it applies
-- to ALL commands -- including the SELECT of a student's own stats. The name
-- says "write", which is how 20260803000000 came to be reviewed on the
-- assumption that own-row *reads* were unprotected and needed a fourth policy
-- adding. They were not, and it did not; that migration ended up carrying a
-- comment explaining the mismatch instead of fixing it.
--
-- "stats: own row" matches the behaviour and matches "perf: own" and
-- "sessions: own" on the neighbouring tables, neither of which claims to be
-- write-only either.
--
-- The idea and the name are from AkashR348's earlier branch
-- restrict-user-stats-read (818164c), which fixed the same anon-read hole
-- 20260803000000 fixed and additionally got this right. That branch predates
-- the applied migration and cannot be merged as-is -- its timestamp is older
-- than 20260803000000, so it would apply out of order, and it does not know
-- about "stats: parent read". This is the one piece of it worth keeping.

-- ALTER ... RENAME rather than DROP + CREATE. A rename preserves the policy
-- definition exactly, so there is no chance of the re-created version drifting
-- from the original, and no moment -- even inside the migration's transaction
-- -- where the table has no own-row policy on it.
--
-- Wrapped because ALTER POLICY has no IF EXISTS, and this has to be a no-op
-- rather than an error on a database where the rename already happened (a
-- local stack that had restrict-user-stats-read checked out, for instance).
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
