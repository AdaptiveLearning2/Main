-- last_active_for_users: a session's start, its answers and the newest session's newest
-- sample; never ended_at, which a sweep stamps when it runs, weeks after the student left.
-- Newest session only: each max(ts) is then one (session_id, ts) index lookup.
CREATE OR REPLACE FUNCTION "public"."last_active_for_users"(
  "p_user_ids" "uuid"[]
) RETURNS TABLE (
  "user_id" "uuid",
  "last_active" timestamp with time zone
)
LANGUAGE "sql"
STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
  SELECT "u"."id" AS "user_id",
         GREATEST(
           (SELECT max("s"."started_at")
              FROM "public"."sessions" "s"
             WHERE "s"."user_id" = "u"."id"),
           (SELECT max("a"."answered_at")
              FROM "public"."session_answers" "a"
             WHERE "a"."user_id" = "u"."id"),
           -- Work with no answer (a headband or camera session) is still activity.
           (SELECT GREATEST(
                     (SELECT max("c"."ts") FROM "public"."cognitive_signals" "c"
                       WHERE "c"."session_id" = "ls"."id"),
                     (SELECT max("f"."ts") FROM "public"."face_signals" "f"
                       WHERE "f"."session_id" = "ls"."id"),
                     (SELECT max("h"."ts") FROM "public"."heart_signals" "h"
                       WHERE "h"."session_id" = "ls"."id"))
              FROM (SELECT "s"."id" FROM "public"."sessions" "s"
                     WHERE "s"."user_id" = "u"."id"
                     ORDER BY "s"."started_at" DESC LIMIT 1) "ls")
         ) AS "last_active"
    FROM unnest("p_user_ids") AS "u"("id");
$$;

REVOKE ALL ON FUNCTION "public"."last_active_for_users"("uuid"[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."last_active_for_users"("uuid"[]) FROM "anon";
REVOKE ALL ON FUNCTION "public"."last_active_for_users"("uuid"[]) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."last_active_for_users"("uuid"[]) TO "service_role";

NOTIFY pgrst, 'reload schema';
