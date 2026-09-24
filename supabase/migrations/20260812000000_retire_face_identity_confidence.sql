-- Retire face_signals.identity_confidence (always null). Face identity is not
-- covered by camera consent and would need its own channel.
-- Deploy order: code that stops selecting the column ships first.

ALTER TABLE "public"."face_signals" DROP COLUMN IF EXISTS "identity_confidence";

NOTIFY pgrst, 'reload schema';
