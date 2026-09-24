-- Turn strategy_llm_enabled on in the seeded row (the Python default only
-- applies when no row exists). Skipped if any admin change to the key is
-- recorded, so a deliberate "off" is never overwritten.

UPDATE "public"."feature_flags"
SET "enabled" = true,
    "updated_at" = "now"()
WHERE "key" = 'strategy_llm_enabled'
  AND "enabled" = false
  AND NOT EXISTS (
      SELECT 1 FROM "public"."feature_flag_changes"
      WHERE "feature_flag_changes"."key" = 'strategy_llm_enabled'
  );
