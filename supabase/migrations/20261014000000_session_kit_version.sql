-- The student kit version a lesson's page reported when its headband started streaming (push), for the admin
-- Sensors kit page. The client's claim: shown, never used to gate anything. Clients hold no write grant on sessions.
-- `kit_reported_at`: when the push report carried the sidecar's answer, a version or none. Only that answer writes
-- it, so it alone tells a lesson that named its kit from a pull one, one from before it, or one never answered.

ALTER TABLE "public"."sessions" ADD COLUMN IF NOT EXISTS "kit_version" text;
ALTER TABLE "public"."sessions" ADD COLUMN IF NOT EXISTS "kit_reported_at" timestamptz;

ALTER TABLE "public"."sessions" DROP CONSTRAINT IF EXISTS "sessions_kit_version_format";
ALTER TABLE "public"."sessions" ADD CONSTRAINT "sessions_kit_version_format"
    CHECK ("kit_version" IS NULL OR "kit_version" ~ '^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$');


-- Since `p_since`: students by the newest kit version their lessons reported, and apart from them the lessons whose
-- sidecar answered with none, with how many students had only those. Counts only, never a student. Only answered
-- lessons count: a pull lesson, one from before it, or one never answered is not "no version".
CREATE OR REPLACE FUNCTION "public"."admin_kit_versions"("p_since" timestamptz)
RETURNS "jsonb"
LANGUAGE "sql"
STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
    WITH headband AS (
        SELECT user_id, kit_version, started_at FROM sessions
         WHERE kit_reported_at IS NOT NULL AND started_at >= p_since
    ), newest AS (
        SELECT DISTINCT ON (user_id) user_id, kit_version FROM headband
         WHERE kit_version IS NOT NULL
         ORDER BY user_id, started_at DESC
    )
    SELECT jsonb_build_object(
        'students_by_version', COALESCE((SELECT jsonb_object_agg(kit_version, n)
                                           FROM (SELECT kit_version, count(*) AS n FROM newest GROUP BY kit_version) v),
                                        '{}'::jsonb),
        'lessons_unreported', (SELECT count(*) FROM headband WHERE kit_version IS NULL),
        'students_unreported', (SELECT count(DISTINCT h.user_id) FROM headband h
                                 WHERE NOT EXISTS (SELECT 1 FROM newest n WHERE n.user_id = h.user_id))
    );
$$;

REVOKE ALL ON FUNCTION "public"."admin_kit_versions"(timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."admin_kit_versions"(timestamptz) FROM "anon";
REVOKE ALL ON FUNCTION "public"."admin_kit_versions"(timestamptz) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."admin_kit_versions"(timestamptz) TO "service_role";

NOTIFY pgrst, 'reload schema';
