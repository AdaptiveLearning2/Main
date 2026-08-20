-- The live monitor's per-session reads, as one query for the whole class.
--
-- `class_live` used to call `_latest_session_signals` once per student, fanned
-- into a four-worker pool -- so a class of thirty cost 120 queries per poll,
-- and `Live.jsx` polls every second, which is how one poll came to take
-- longer than the interval between polls.
--
-- Widening the pool doesn't fix it: fanning the outer loop into the same pool
-- the work waits on deadlocks (the same trap `_admin_live_pool` was created
-- to avoid). The fix is asking for the right rows once, not more concurrency.
--
-- `DISTINCT ON (session_id) ... ORDER BY session_id, ts DESC` is the newest
-- row per session. Four of those unioned into one result means the whole
-- roster costs one round trip regardless of class size.
--
-- Rows come back as jsonb rather than four typed result sets, since a single
-- function can't return four different row shapes and four functions would
-- be four round trips again. `to_jsonb(t)` preserves every column, so adding
-- one to a signal table needs no change here.
--
-- `session_answers` carries `answered_at` rather than `ts`, and is ordered on
-- that. Included because the staleness check takes the newest of all four.
--
-- SECURITY INVOKER, granted to service_role only: the backend resolves who
-- may see a class before calling this, and the function itself makes no
-- access decision.

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
