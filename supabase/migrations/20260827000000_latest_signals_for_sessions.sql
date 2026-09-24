-- Newest row per session from each of four tables, in one round trip for the
-- whole class. Rows as jsonb, since one function cannot return four shapes.
-- Makes no access decision: the backend checks the class first.

CREATE OR REPLACE FUNCTION "public"."latest_signals_for_sessions"(
  "p_session_ids" "uuid"[]
) RETURNS TABLE (
  "session_id" "uuid",
  "channel" "text",
  "payload" "jsonb"
)
LANGUAGE "sql"
STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
  (SELECT DISTINCT ON (c."session_id") c."session_id", 'cognitive'::text, "to_jsonb"(c)
     FROM "public"."cognitive_signals" c
    WHERE c."session_id" = ANY(p_session_ids)
    ORDER BY c."session_id", c."ts" DESC)
  UNION ALL
  (SELECT DISTINCT ON (f."session_id") f."session_id", 'face'::text, "to_jsonb"(f)
     FROM "public"."face_signals" f
    WHERE f."session_id" = ANY(p_session_ids)
    ORDER BY f."session_id", f."ts" DESC)
  UNION ALL
  (SELECT DISTINCT ON (h."session_id") h."session_id", 'heart'::text, "to_jsonb"(h)
     FROM "public"."heart_signals" h
    WHERE h."session_id" = ANY(p_session_ids)
    ORDER BY h."session_id", h."ts" DESC)
  UNION ALL
  (SELECT DISTINCT ON (a."session_id") a."session_id", 'answer'::text, "to_jsonb"(a)
     FROM "public"."session_answers" a
    WHERE a."session_id" = ANY(p_session_ids)
    ORDER BY a."session_id", a."answered_at" DESC);
$$;

REVOKE ALL ON FUNCTION "public"."latest_signals_for_sessions"("uuid"[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."latest_signals_for_sessions"("uuid"[]) FROM "anon";
REVOKE ALL ON FUNCTION "public"."latest_signals_for_sessions"("uuid"[]) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."latest_signals_for_sessions"("uuid"[]) TO "service_role";

NOTIFY pgrst, 'reload schema';
