-- One body for the signal summary, fanned out, instead of two copies of it.
--
-- `student_signal_summary` and `student_signal_summary_many` computed the same
-- six averages and four counts from the same three tables, written out twice in
-- different shapes -- CTEs in one, correlated subqueries in the other. Nothing
-- tied them together, so every change had to be made twice by hand and noticed
-- twice by review.
--
-- That is not hypothetical here. 20260823000000 fixed `face_samples` counting a
-- column with no producer, and had to apply `count(f.attention)` ->
-- `count(f.emotion)` separately in each body -- the duplication that produced
-- the bug being used to repair it. A third copy of the fix is exactly what this
-- removes the room for.
--
-- `_many` is now a fan-out: unnest the ids, call the single-student function
-- once per id through a LATERAL join, and project its columns. The single
-- function keeps the whole definition of what a summary *is*.
--
-- Deliberately in this direction. The other way round -- the single function
-- calling `_many` with a one-element array -- would have to add
-- `dominant_emotion` to `_many`'s RETURNS TABLE, and changing a return type
-- means DROP + CREATE, a fresh ACL, and a window where deployed code reads a
-- column the database does not have yet. This way neither signature nor return
-- type moves, so `CREATE OR REPLACE` keeps the existing ACLs, no caller
-- changes, and there is no in-between state to sequence a deploy around.
--
-- The work is what it already was: the old body ran ten correlated subqueries
-- per student, this runs one function call per student. `p_include_heart` and
-- `p_include_emotion` still gate inside the single body, so an excluded channel
-- is still never read -- which is the property the facial opt-out rests on, and
-- it would have been quietly lost by projecting nulls out here instead.

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

-- Same signature, so CREATE OR REPLACE kept the existing ACL rather than
-- creating one. Restated anyway: a revoke that is assumed rather than written
-- is the one nobody notices is missing.
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean, "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean, "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean, "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean, "text") TO "service_role";

NOTIFY pgrst, 'reload schema';
