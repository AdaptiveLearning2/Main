-- Admin feature flags: global (there is no school entity), key/value, read
-- through the backend's default map, so an unknown key is inert and a missing
-- row still has a value. Admin is profiles.role = 'admin'.

CREATE TABLE IF NOT EXISTS "public"."feature_flags" (
    "key"          text        PRIMARY KEY,
    "enabled"      boolean     NOT NULL DEFAULT false,
    "description"  text,
    -- Consent flag only, while disabled. Expiry is evaluated at read time, so
    -- a failed job cannot leave enforcement off indefinitely.
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


CREATE TABLE IF NOT EXISTS "public"."feature_flag_changes" (
    "id"           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    "key"          text        NOT NULL,
    -- Null on a key's first write.
    "old_enabled"  boolean,
    "new_enabled"  boolean     NOT NULL,
    "bypass_until" timestamptz,
    "changed_by"   uuid        REFERENCES "auth"."users"("id") ON DELETE SET NULL,
    "changed_at"   timestamptz NOT NULL DEFAULT now()
);

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


-- Seeded with the pre-existing behaviour, so applying this changes nothing.
-- ON CONFLICT DO NOTHING so re-running never resets an admin's setting.

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
