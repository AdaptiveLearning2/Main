-- Head pose on face_signals, in degrees (gaze_x/y is eye-in-head only).
-- Signs per face_geometry.head_pose: yaw > 0 toward image right, pitch > 0
-- face up, roll > 0 subject's right eye rises.
-- No default: null is "not measured", 0.0 is a real square-on reading.

ALTER TABLE "public"."face_signals"
    ADD COLUMN IF NOT EXISTS "head_yaw" double precision,
    ADD COLUMN IF NOT EXISTS "head_pitch" double precision,
    ADD COLUMN IF NOT EXISTS "head_roll" double precision;

-- Not rolled up: a day's mean angle is meaningless.

NOTIFY pgrst, 'reload schema';
