-- Head pose on `face_signals`.
--
-- `gaze_x`/`gaze_y` are the iris's offset within the eye opening, so they say
-- where the eyes point relative to the head, not where the head points.
-- Point-of-regard is head pose plus eye offset, and only the second term was
-- stored -- so a student with their head turned 30 degrees away and eyes
-- centred read as `gaze_x ~ 0`, indistinguishable from looking straight ahead.
--
-- `face_geometry.head_pose` already computes these and is unit-tested; this
-- is the storage half.
--
-- Degrees, named `head_*` rather than bare `yaw`/`pitch`/`roll` since this
-- table may carry other angles later.
--
-- Conventions, copied here because a column is read far from the module that
-- writes it (`face_geometry`'s docstring is the source of truth):
--
--   head_yaw   > 0  the subject turns toward the image right = their own left
--   head_pitch > 0  the face points up (chin away from chest)
--   head_roll  > 0  the head tips so the subject's right eye rises
--
-- Nullable with no default, like every other measurement here. Null means
-- "not measured" (channel off, landmarker unavailable, or the pose fit
-- refused). 0.0 is a valid reading meaning square on, so a default of 0
-- would record every unmeasured window as a student facing the camera.

ALTER TABLE "public"."face_signals"
    ADD COLUMN IF NOT EXISTS "head_yaw" double precision,
    ADD COLUMN IF NOT EXISTS "head_pitch" double precision,
    ADD COLUMN IF NOT EXISTS "head_roll" double precision;

-- No grant changes needed: ADD COLUMN inherits the table's ACL, and the
-- existing read-your-own RLS policy covers the new columns without naming
-- them.

-- Deliberately not added to `signal_daily_rollup`. Averaging an angle over a
-- school day is close to meaningless -- a student swinging between +40 and
-- -40 degrees averages the same 0 as one who never moved -- so the useful
-- aggregate is a fraction of time past some threshold, a decision that
-- belongs with whatever first renders this. Until then head pose is
-- per-sample only, so it's deleted by `expire_signal_rows` with nothing
-- summarising it, the same position `gaze_x`/`gaze_y` are already in.

NOTIFY pgrst, 'reload schema';
