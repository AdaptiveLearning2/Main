-- student_signal_summary_many fans out to student_signal_summary via LATERAL,
-- so the summary has one body. The include flags still gate inside it, so an
-- excluded channel is never read.

CREATE OR REPLACE FUNCTION "public"."student_signal_summary_many"(
  "p_student_ids" "uuid"[],
  "p_days" integer DEFAULT 7,
  "p_include_heart" boolean DEFAULT true,
  "p_include_emotion" boolean DEFAULT true,
  "p_timezone" "text" DEFAULT 'UTC'
)
RETURNS TABLE (
  "student_id" "uuid",
  "focus" double precision,
  "stress" double precision,
  "engagement" double precision,
  "face_attention" double precision,
  "heart_rate_bpm" double precision,
  "rmssd_ms" double precision,
  "sessions" bigint,
  "cognitive_samples" bigint,
  "face_samples" bigint,
  "heart_samples" bigint
)
LANGUAGE "sql"
STABLE
AS $$
  SELECT ids.sid,
         s.focus, s.stress, s.engagement, s.face_attention,
         s.heart_rate_bpm, s.rmssd_ms,
         s.sessions, s.cognitive_samples, s.face_samples, s.heart_samples
  FROM unnest(p_student_ids) AS ids(sid)
  CROSS JOIN LATERAL "public"."student_signal_summary"(
    ids.sid, p_days, p_include_heart, p_include_emotion, p_timezone) s;
$$;

-- ACL kept by CREATE OR REPLACE; restated.
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean, "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean, "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean, "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean, "text") TO "service_role";

NOTIFY pgrst, 'reload schema';
