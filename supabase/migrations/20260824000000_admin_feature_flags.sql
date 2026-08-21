-- Admin dashboard: the flags an administrator can flip.
--
-- Until now the only global configuration was `retention_window`, edited
-- directly through the dashboard SQL editor with no admin role. That was fine
-- for one table; a second thing to configure and a third person who needs to
-- configure it is what this migration's surface is for.
--
-- Deliberately not per-school. There is no school entity in this system --
-- "school" appears only as the school year -- so every flag here is global.
-- Adding a `school_id` now would mean a tenancy key every query has to
-- filter on, for a tenancy that doesn't exist.
--
-- Who may administer is `profiles.role = 'admin'`. That's only safe because
-- the client roles can't write the column and sign-up can't choose the value
-- (both enforced elsewhere in this migration series).

-- Key/value rather than one column per flag: adding a flag is an INSERT
-- rather than a migration, and the admin API is one endpoint instead of one
-- per switch. The cost is that the set of valid keys lives in backend code,
-- so the backend reads through a declared default map -- an unknown key here
-- is inert, and a key the table is missing still has a value.
--
-- `bypass_until` serves exactly one flag (consent_enforcement_enabled) and is
-- null for the rest. Kept on the shared table rather than one of its own, so
-- there's no way for the two to disagree about which flag it describes.

CREATE TABLE IF NOT EXISTS "public"."feature_flags" (
    "key"          text        PRIMARY KEY,
    "enabled"      boolean     NOT NULL DEFAULT false,
    "description"  text,
    -- Meaningful only while `enabled` is false, and only on the consent flag:
    -- the instant the bypass stops applying and enforcement resumes. Expiry
    -- is evaluated at read time, not by a scheduled job, so a job that fails
    -- to run can't leave consent enforcement off indefinitely.
    "bypass_until" timestamptz,
    "updated_by"   uuid        REFERENCES "auth"."users"("id") ON DELETE SET NULL,
    "updated_at"   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE "public"."feature_flags" IS
    'Global runtime feature switches, edited from the admin dashboard. Global '
    'rather than per-school: this deployment serves one school and has no '
    'school entity. Read through a backend default map, so an unrecognised key '
    'is inert and a missing row still has a value.';

REVOKE ALL ON TABLE "public"."feature_flags" FROM "anon";
REVOKE ALL ON TABLE "public"."feature_flags" FROM "authenticated";
GRANT ALL ON TABLE "public"."feature_flags" TO "service_role";

ALTER TABLE "public"."feature_flags" ENABLE ROW LEVEL SECURITY;


-- Append-only, written by the backend rather than a trigger, since the
-- backend has already resolved the admin's identity to admit the request --
-- a trigger would have to re-derive that from session context.
--
-- The flag this exists for is consent_enforcement_enabled: a bypass turned on
-- and forgotten is the failure mode, `bypass_until` bounds it and this says
-- who turned it on. Every other flag is logged too, since a log with
-- exceptions is one someone has to reason about before trusting.

CREATE TABLE IF NOT EXISTS "public"."feature_flag_changes" (
    "id"           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    "key"          text        NOT NULL,
    -- Nullable: the first write against a key with no previous value would
    -- otherwise have to invent a `false`.
    "old_enabled"  boolean,
    "new_enabled"  boolean     NOT NULL,
    -- On the row rather than inferred from the flag, so a bypass window that
    -- was set and later superseded stays reconstructable.
    "bypass_until" timestamptz,
    "changed_by"   uuid        REFERENCES "auth"."users"("id") ON DELETE SET NULL,
    "changed_at"   timestamptz NOT NULL DEFAULT now()
);

-- The history view reads one key at a time, newest first.
CREATE INDEX IF NOT EXISTS "feature_flag_changes_key_changed_at_idx"
    ON "public"."feature_flag_changes" ("key", "changed_at" DESC);

COMMENT ON TABLE "public"."feature_flag_changes" IS
    'Append-only audit of feature_flags writes. Written by the backend, which '
    'has already resolved the admin identity, rather than by a trigger that '
    'would have to re-derive it.';

REVOKE ALL ON TABLE "public"."feature_flag_changes" FROM "anon";
REVOKE ALL ON TABLE "public"."feature_flag_changes" FROM "authenticated";
GRANT ALL ON TABLE "public"."feature_flag_changes" TO "service_role";

ALTER TABLE "public"."feature_flag_changes" ENABLE ROW LEVEL SECURITY;


-- Unlike retention_window this table is seeded. An absent window is a
-- decision nobody made, so it denies; an absent flag row is asking the same
-- question about behavior that already has an established default in the
-- code being replaced, so each row below reproduces what the system does
-- today. Applying this migration changes nothing until an admin touches
-- something.
--
--   strategy_llm_enabled        false -- STRATEGY_LLM_ENABLED's env default
--   recording_*_enabled         true  -- no such kill switch existed; on = today
--   consent_enforcement_enabled true  -- consent is enforced today, and the
--                                        default here has to be the safe one
--
-- ON CONFLICT DO NOTHING so re-running never resets a flag an admin has set.

INSERT INTO "public"."feature_flags" ("key", "enabled", "description") VALUES
    ('strategy_llm_enabled',        false,
     'Let the model pass try to improve on the rule-based learning strategies. Off = rules only, no socket opened.'),
    ('recording_eeg_enabled',       true,
     'Platform-wide EEG recording switch. ANDed with each student''s own consent -- it can withhold recording, never grant it.'),
    ('recording_heart_enabled',     true,
     'Platform-wide headband heart-rate recording switch. ANDed with consent.'),
    ('recording_camera_enabled',    true,
     'Platform-wide camera recording switch. ANDed with consent.'),
    ('consent_enforcement_enabled', true,
     'Enforce per-student consent before recording. Turning this OFF records signals from students who have not consented -- for prototyping only, and it expires on its own.')
ON CONFLICT ("key") DO NOTHING;

NOTIFY pgrst, 'reload schema';
