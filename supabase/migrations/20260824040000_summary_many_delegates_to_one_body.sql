-- One body for the signal summary, fanned out, instead of two copies of it.
--
-- `student_signal_summary` and `student_signal_summary_many` computed the
-- same averages and counts from the same three tables, written out twice in
-- different shapes -- so every fix had to be made twice by hand. A previous
-- migration fixing `face_samples` had to apply the same one-line change
-- separately in each body.
--
-- `_many` is now a fan-out: unnest the ids, call the single-student function
-- once per id through a LATERAL join, and project its columns. The single
-- function keeps the whole definition of what a summary is.
--
-- Deliberately in this direction rather than the reverse, since the single
-- function calling `_many` would need to change `_many`'s return type --
-- meaning DROP + CREATE, a fresh ACL, and a window where deployed code reads
-- a column the database doesn't have yet. This way neither signature moves.
--
-- `p_include_heart` and `p_include_emotion` still gate inside the single
-- body, so an excluded channel is still never read -- the property the
-- facial opt-out rests on, which would be lost by projecting nulls out here
-- instead.

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

-- Same signature, so CREATE OR REPLACE kept the existing ACL. Restated
-- anyway: a revoke that's assumed rather than written is the one nobody
-- notices is missing.
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean, "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean, "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean, "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean, "text") TO "service_role";

NOTIFY pgrst, 'reload schema';
