-- Split p_include_face into p_include_heart and p_include_emotion, and teach
-- the summaries about heart_signals.
--
-- One flag covered one channel when there was only one optional channel. There
-- are now two, consented separately -- a student may permit the headband and
-- refuse the camera, or the reverse -- so a single boolean cannot express what
-- the caller is allowed to read.
--
-- `p_include_emotion` gates every read of `face_signals`, not only the emotion
-- column. Attention and gaze come off the same camera under the same consent
-- flag, so the sensor is the boundary; the name follows the product's language
-- for the channel rather than the column list.
--
-- Both flags lead their predicates, as p_include_face did. False therefore
-- excludes every row before any value reaches an aggregate, which is what makes
-- this an opt-out from *reading* rather than a null applied on the way out --
-- the distinction the reporting rules in CLAUDE.md insist on.

-- The old signature must go explicitly. CREATE OR REPLACE cannot change a
-- parameter list, so without this the 3-argument version survives as an
-- overload: still granted, still callable, and unaware of the flag that now
-- decides whether heart rows are read. Worse, a named-argument call matching
-- both is rejected as ambiguous rather than picking one.
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
    -- count(c.focus), not count(*): a row with a NULL focus contributes
    -- nothing to avg(), and the pipeline deliberately writes rows with NULL
    -- measurements when electrode contact is bad, keeping the session timeline
    -- intact. Counting those would report a nonzero sample count beside a NULL
    -- average.
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
           -- FILTERed explicitly rather than relying on how the ordered-set
           -- aggregate treats NULLs: emotion is nullable, and "no emotion was
           -- read" must not be able to win the vote and render as a mood.
           mode() WITHIN GROUP (ORDER BY f.emotion)
             FILTER (WHERE f.emotion IS NOT NULL) AS emotion
    FROM face_signals f, bounds b
    WHERE p_include_emotion AND f.user_id = p_student_id AND f.ts >= b.since
  ),
  hrt AS (
    -- Only trusted samples reach the average. An untrusted one carries a heart
    -- rate; it is just not one worth reporting, and averaging it in would let a
    -- known-bad reading move a number a parent reads. `trusted IS TRUE` rather
    -- than `trusted`, so a NULL -- written before the producer set the column --
    -- is excluded rather than treated as false-y by accident.
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


-- Deliberately without dominant_emotion, as before: this one feeds the parent
-- dashboard, which has no emotion tile, and a mode() per child on every load
-- costs something for nothing. Add it here only alongside somewhere that
-- renders it.
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


-- New signatures carry fresh ACLs, so the revokes are repeated rather than
-- inherited. Postgres grants EXECUTE to PUBLIC on every new function, and
-- Supabase additionally grants it to anon and authenticated *by name* -- an
-- explicit grant to a named role survives a revoke aimed at PUBLIC, so all
-- three are needed.
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean, boolean) FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean, boolean) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary"("uuid", integer, boolean, boolean) TO "service_role";

REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean) FROM "anon";
REVOKE ALL ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."student_signal_summary_many"("uuid"[], integer, boolean, boolean) TO "service_role";

NOTIFY pgrst, 'reload schema';
