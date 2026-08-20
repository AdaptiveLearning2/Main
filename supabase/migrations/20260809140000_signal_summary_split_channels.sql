-- Split p_include_face into p_include_heart and p_include_emotion, and teach
-- the summaries about heart_signals. Headband and camera are consented
-- separately, so one boolean can no longer express what the caller may read.
--
-- p_include_emotion gates every read of face_signals, not just the emotion
-- column: attention and gaze come off the same camera under the same consent
-- flag, so the sensor is the real boundary.
--
-- Both flags lead their predicates so a false value excludes rows before
-- they reach an aggregate, rather than nulling a value afterward -- an
-- opt-out has to skip the read, not just hide the result.

-- The old signature must be dropped explicitly. CREATE OR REPLACE can't
-- change a parameter list, so without this the old version survives as an
-- overload -- still granted, still callable, unaware of the new flag, and a
-- named-argument call matching both signatures would be rejected as
-- ambiguous.
DROP FUNCTION IF EXISTS "public"."student_signal_summary"("uuid", integer, boolean);
DROP FUNCTION IF EXISTS "public"."student_signal_summary_many"("uuid"[], integer, boolean);


CREATE OR REPLACE FUNCTION "public"."student_signal_summary"(
  "p_student_id" "uuid",
  "p_days" integer DEFAULT 7,
  "p_include_heart" boolean DEFAULT true,
  "p_include_emotion" boolean DEFAULT true
)
RETURNS TABLE (
  "focus" double precision,
  "stress" double precision,
  "engagement" double precision,
  "face_attention" double precision,
  "heart_rate_bpm" double precision,
  "rmssd_ms" double precision,
  "sessions" bigint,
  "cognitive_samples" bigint,
  "face_samples" bigint,
  "heart_samples" bigint,
  "dominant_emotion" "text"
)
LANGUAGE "sql"
STABLE
AS $$
  WITH bounds AS (
    SELECT now() - (GREATEST(p_days, 1) || ' days')::interval AS since
  ),
  cog AS (
    -- count(c.focus), not count(*): a row with a null focus (bad electrode
    -- contact) contributes nothing to avg(), so counting it too would report
    -- a nonzero sample count beside a null average.
    SELECT avg(c.focus)      AS focus,
           avg(c.stress)     AS stress,
           avg(c.engagement) AS engagement,
           count(c.focus)    AS n
    FROM cognitive_signals c, bounds b
    WHERE c.user_id = p_student_id AND c.ts >= b.since
  ),
  fac AS (
    SELECT avg(f.attention)   AS attention,
           count(f.attention) AS n,
           -- Explicit FILTER so a null emotion (nothing read) can't win the
           -- mode() vote and render as an actual mood.
           mode() WITHIN GROUP (ORDER BY f.emotion)
             FILTER (WHERE f.emotion IS NOT NULL) AS emotion
    FROM face_signals f, bounds b
    WHERE p_include_emotion AND f.user_id = p_student_id AND f.ts >= b.since
  ),
  hrt AS (
    -- Only trusted samples reach the average -- an untrusted one still carries
    -- a heart rate, it's just not worth reporting. `trusted IS TRUE` excludes
    -- null (unset) as well as false, rather than treating null as falsy by
    -- accident.
    SELECT avg(h.heart_rate_bpm)   AS bpm,
           avg(h.rmssd_ms)         AS rmssd,
           count(h.heart_rate_bpm) AS n
    FROM heart_signals h, bounds b
    WHERE p_include_heart AND h.user_id = p_student_id AND h.ts >= b.since
      AND h.trusted IS TRUE
  ),
  ses AS (
    SELECT count(*) AS n
    FROM sessions s, bounds b
    WHERE s.user_id = p_student_id AND s.started_at >= b.since
  )
  SELECT cog.focus, cog.stress, cog.engagement, fac.attention,
         hrt.bpm, hrt.rmssd,
         ses.n, cog.n, fac.n, hrt.n, fac.emotion
  FROM cog, fac, hrt, ses;
$$;


-- No dominant_emotion here: this feeds the parent dashboard, which has no
-- emotion tile, so computing mode() per child on every load would cost
-- something for nothing.
CREATE OR REPLACE FUNCTION "public"."student_signal_summary_many"(
  "p_student_ids" "uuid"[],
  "p_days" integer DEFAULT 7,
  "p_include_heart" boolean DEFAULT true,
  "p_include_emotion" boolean DEFAULT true
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
  WITH bounds AS (
    SELECT now() - (GREATEST(p_days, 1) || ' days')::interval AS since
  ),
  ids AS (
    SELECT unnest(p_student_ids) AS sid
  )
  SELECT ids.sid,
         (SELECT avg(c.focus)      FROM cognitive_signals c, bounds b
           WHERE c.user_id = ids.sid AND c.ts >= b.since),
         (SELECT avg(c.stress)     FROM cognitive_signals c, bounds b
           WHERE c.user_id = ids.sid AND c.ts >= b.since),
         (SELECT avg(c.engagement) FROM cognitive_signals c, bounds b
           WHERE c.user_id = ids.sid AND c.ts >= b.since),
         (SELECT avg(f.attention)  FROM face_signals f, bounds b
           WHERE p_include_emotion AND f.user_id = ids.sid AND f.ts >= b.since),
         (SELECT avg(h.heart_rate_bpm) FROM heart_signals h, bounds b
           WHERE p_include_heart AND h.user_id = ids.sid AND h.ts >= b.since
             AND h.trusted IS TRUE),
         (SELECT avg(h.rmssd_ms)   FROM heart_signals h, bounds b
           WHERE p_include_heart AND h.user_id = ids.sid AND h.ts >= b.since
             AND h.trusted IS TRUE),
         (SELECT count(*)          FROM sessions s, bounds b
           WHERE s.user_id = ids.sid AND s.started_at >= b.since),
         (SELECT count(c.focus)    FROM cognitive_signals c, bounds b
           WHERE c.user_id = ids.sid AND c.ts >= b.since),
         (SELECT count(f.attention) FROM face_signals f, bounds b
           WHERE p_include_emotion AND f.user_id = ids.sid AND f.ts >= b.since),
         (SELECT count(h.heart_rate_bpm) FROM heart_signals h, bounds b
           WHERE p_include_heart AND h.user_id = ids.sid AND h.ts >= b.since
             AND h.trusted IS TRUE)
  FROM ids;
$$;


-- New signatures carry fresh ACLs, so the revokes have to be repeated. Both
-- Postgres's PUBLIC grant and Supabase's named anon/authenticated grants need
-- revoking -- a named grant survives a PUBLIC-only revoke.
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean, boolean) FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean, boolean) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean, boolean) TO "service_role";

REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean) FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean) TO "service_role";

NOTIFY pgrst, 'reload schema';
