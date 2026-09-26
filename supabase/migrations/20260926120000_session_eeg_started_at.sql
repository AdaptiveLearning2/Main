-- When /api/eeg/start began recording for this session; NULL if no headband was ever started.
-- `signals_missing` needs it, or every sensorless session of a consented student raised one.
ALTER TABLE "public"."sessions" ADD COLUMN IF NOT EXISTS "eeg_started_at" timestamp with time zone;

NOTIFY pgrst, 'reload schema';
