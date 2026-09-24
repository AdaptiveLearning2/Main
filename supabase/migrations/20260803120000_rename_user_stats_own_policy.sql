-- Rename "stats: own write" to "stats: own row": it has no FOR clause, so it
-- covers SELECT too.

-- RENAME, not DROP + CREATE, so the definition cannot drift. Wrapped because
-- ALTER POLICY has no IF EXISTS.
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
