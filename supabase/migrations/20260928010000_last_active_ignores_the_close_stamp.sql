-- last_active_for_users: a session's start and its answers, never ended_at. A sweep stamps
-- ended_at when it runs, hours or weeks after the student left, and nothing records which
-- closes were the sweep's; a student's own close lands seconds after their last answer.
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
             WHERE "a"."user_id" = "u"."id")
         ) AS "last_active"
    FROM unnest("p_user_ids") AS "u"("id");
$$;

REVOKE ALL ON FUNCTION "public"."last_active_for_users"("uuid"[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."last_active_for_users"("uuid"[]) FROM "anon";
REVOKE ALL ON FUNCTION "public"."last_active_for_users"("uuid"[]) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."last_active_for_users"("uuid"[]) TO "service_role";

NOTIFY pgrst, 'reload schema';
