-- strategy_llm_enabled was seeded false in 20260824000000_admin_feature_flags.sql
-- and nothing in this codebase ever flips it -- it's an admin-only toggle
-- via POST /api/admin/flags/{key}. In practice that means the model pass
-- behind /api/students/{id}/learning-strategies has never run in any
-- deployment that didn't have an admin manually turn it on: every request
-- has returned the deterministic rule-based list.
--
-- The Python-side default (_FEATURE_FLAG_DEFAULTS in main.py) only matters
-- when the table has no row for the key at all -- once 20260824000000
-- seeded a row, _feature_flags() reads that row, not the Python constant.
-- So flipping the Python default alone does nothing for a database that has
-- already run that migration; this UPDATE is what actually changes the live
-- value.
--
-- Guarded on there being no recorded admin change for this key: if an admin
-- ever explicitly turned it off after trying it, that is a deliberate
-- decision this migration must not silently overwrite. Absence of any
-- feature_flag_changes row for this key means it is still sitting at the
-- original seeded default, which is the only case this is meant to touch.

UPDATE "public"."feature_flags"
SET "enabled" = true,
    "updated_at" = "now"()
WHERE "key" = 'strategy_llm_enabled'
  AND "enabled" = false
  AND NOT EXISTS (
      SELECT 1 FROM "public"."feature_flag_changes"
      WHERE "feature_flag_changes"."key" = 'strategy_llm_enabled'
  );
