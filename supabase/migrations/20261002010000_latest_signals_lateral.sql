-- The newest row per channel for each open session, read by class_live once a poll.
-- One index probe per session and channel, instead of DISTINCT ON reading every row
-- of every open session. Payloads name the fields the live view reads, nothing more.

-- The DROP INDEX below takes ACCESS EXCLUSIVE on face_signals; fail rather than queue reads.
SET LOCAL lock_timeout = '5s';

CREATE OR REPLACE FUNCTION "public"."latest_signals_for_sessions"("p_session_ids" "uuid"[])
RETURNS TABLE ("session_id" "uuid", "channel" "text", "payload" "jsonb")
LANGUAGE "sql" STABLE SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
  WITH ids AS (SELECT DISTINCT u.id FROM unnest(p_session_ids) AS u(id))
  SELECT ids.id, 'cognitive'::text,
         jsonb_build_object('ts', c.ts, 'focus', c.focus, 'stress', c.stress,
                            'engagement', c.engagement,
                            'raw', jsonb_build_object(
                                'signal_quality', c.raw -> 'signal_quality',
                                'quality_basis', c.raw -> 'quality_basis'))
    FROM ids CROSS JOIN LATERAL (
      SELECT x.ts, x.focus, x.stress, x.engagement, x.raw
        FROM "public"."cognitive_signals" x
       WHERE x.session_id = ids.id ORDER BY x.ts DESC LIMIT 1) c
  UNION ALL
  SELECT ids.id, 'face'::text, jsonb_build_object('ts', f.ts, 'emotion', f.emotion)
    FROM ids CROSS JOIN LATERAL (
      SELECT x.ts, x.emotion FROM "public"."face_signals" x
       WHERE x.session_id = ids.id ORDER BY x.ts DESC LIMIT 1) f
  UNION ALL
  SELECT ids.id, 'heart'::text,
         jsonb_build_object('ts', h.ts, 'source', h.source, 'trusted', h.trusted,
                            'heart_rate_bpm', h.heart_rate_bpm, 'rmssd_ms', h.rmssd_ms)
    FROM ids CROSS JOIN LATERAL (
      -- Unique per (session, source, ts), so two sensors can share a ts: id breaks the tie.
      SELECT x.ts, x.source, x.trusted, x.heart_rate_bpm, x.rmssd_ms
        FROM "public"."heart_signals" x
       WHERE x.session_id = ids.id ORDER BY x.ts DESC, x.id DESC LIMIT 1) h
  UNION ALL
  SELECT ids.id, 'answer'::text, jsonb_build_object('answered_at', a.answered_at)
    FROM ids CROSS JOIN LATERAL (
      SELECT x.answered_at FROM "public"."session_answers" x
       WHERE x.session_id = ids.id ORDER BY x.answered_at DESC LIMIT 1) a;
$$;

REVOKE ALL ON FUNCTION "public"."latest_signals_for_sessions"("uuid"[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."latest_signals_for_sessions"("uuid"[]) FROM "anon";
REVOKE ALL ON FUNCTION "public"."latest_signals_for_sessions"("uuid"[]) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."latest_signals_for_sessions"("uuid"[]) TO "service_role";

-- The face lookup scans face_session_ts_key (session_id, ts) backwards, so this
-- (session_id, ts DESC) copy of it only adds a write per insert.
DROP INDEX IF EXISTS "public"."face_session_ts_idx";

NOTIFY pgrst, 'reload schema';
