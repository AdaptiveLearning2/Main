-- Head pose on `face_signals` (Phase 11, step 2b).
--
-- `gaze_x`/`gaze_y` are the iris's offset *within the eye opening*, so they say
-- where the eyes point relative to the head and nothing about where the head
-- points. Point-of-regard is head pose plus eye offset, and only the second
-- term was stored -- which means a student with their head turned 30 degrees
-- away and their eyes centred read as `gaze_x ~ 0`, indistinguishable from one
-- looking straight ahead.
--
-- `face_geometry.head_pose` already computes these, is unit-tested, and had its
-- handedness and both sign conventions confirmed against a camera on
-- 2026-08-12. This is the storage half.
--
-- Degrees, and named `head_*` rather than bare `yaw`/`pitch`/`roll`: this table
-- may yet carry angles that are not the head's, and a bare `yaw` would have to
-- be renamed the day it does.
--
-- Conventions, copied here because a column is read far from the module that
-- writes it (`face_geometry`'s docstring is the source of truth):
--
--   head_yaw   > 0  the subject turns toward the image right = their own left
--   head_pitch > 0  the face points up (chin away from chest)
--   head_roll  > 0  the head tips so the subject's right eye rises
--
-- Nullable with no default, like every other measurement here. NULL is "not
-- measured" -- the channel off, the landmarker unavailable, or the pose fit
-- refused (near profile it refuses rather than reporting a mirrored angle).
-- 0.0 is a valid reading meaning square on, so a default of 0 would record
-- every unmeasured window as a student facing the camera.

ALTER TABLE "public"."face_signals"
    ADD COLUMN IF NOT EXISTS "head_yaw" double precision,
    ADD COLUMN IF NOT EXISTS "head_pitch" double precision,
    ADD COLUMN IF NOT EXISTS "head_roll" double precision;

-- No grant changes. `ADD COLUMN` inherits the table's ACL, and `20260805110000`
-- already narrowed `face_signals` to SELECT for `authenticated` with `anon`
-- revoked outright; the read-your-own RLS policy covers the new columns without
-- naming them. Restating grants here would be noise that reads as a change.

-- Deliberately **not** added to `signal_daily_rollup`. Averaging an angle over
-- a school day is close to meaningless -- a student who spent the lesson
-- swinging between +40 and -40 degrees averages the same 0 as one who never
-- moved -- so the useful aggregate is a *fraction of time* past some threshold,
-- and picking that threshold is a decision that belongs with whatever first
-- renders this. Until then head pose is per-sample only, which means it is
-- deleted by `expire_signal_rows` at the end of the school year with nothing
-- summarising it. That is the same position `gaze_x`/`gaze_y` are already in
-- and is stated here so it is a known consequence rather than a discovery.

NOTIFY pgrst, 'reload schema';
