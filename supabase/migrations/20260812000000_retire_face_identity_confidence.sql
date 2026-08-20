-- Retire face_signals.identity_confidence. Nothing ever computed a face
-- identity -- FER+ expression classification is the only model that runs, and
-- this column was always null.
--
-- Retired rather than implemented: matching a child's face against a stored
-- identity is a different purpose from what the camera consent asks about
-- ("works out how they are finding the questions"). Shipping identity under
-- that consent would record something nobody agreed to -- it needs its own
-- consent channel and copy before it needs a model.
--
-- Removing it also closes a live bug, not just dead weight: the table carried
-- two confidences answering different questions, and signal_fusion's face
-- channel read the wrong one -- a clearly identified face with a garbage FER+
-- label withheld a difficulty increase, while a well-classified expression on
-- a poorly identified face was silently discarded.
--
-- No data loss: every row's value was already null.
--
-- Deploy order: the code that stops selecting this column must ship first --
-- PostgREST errors on a select naming a dropped column.
--
-- attention, gaze_x and gaze_y are deliberately not dropped here. They also
-- have no producer yet, but unlike identity they're wanted once a landmark
-- model exists to fill them.

ALTER TABLE "public"."face_signals" DROP COLUMN IF EXISTS "identity_confidence";

-- No function signature changed -- student_signal_summary never selected this
-- column. The reload is still needed since PostgREST caches the table shape.
NOTIFY pgrst, 'reload schema';
