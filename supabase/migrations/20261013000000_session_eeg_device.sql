-- The headband station a session last started recording on, written by `/api/eeg/start` (pull only).
-- A released station leaves no `station_pairings` row, so this is how Stations says how its last lesson ended.

ALTER TABLE "public"."sessions" ADD COLUMN IF NOT EXISTS "eeg_device_id" text;

NOTIFY pgrst, 'reload schema';
