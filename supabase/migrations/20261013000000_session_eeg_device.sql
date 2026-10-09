-- The headband station a session last started recording on, written by `/api/eeg/start` (pull only).
-- A released station leaves no `station_pairings` row, so this is how Stations says how its last lesson ended.

ALTER TABLE "public"."sessions" ADD COLUMN IF NOT EXISTS "eeg_device_id" text;

-- Stations reads today's ended headband lessons, newest first, every 5 s poll; only those rows are indexed.
CREATE INDEX IF NOT EXISTS "sessions_eeg_device_ended_idx" ON "public"."sessions" ("ended_at" DESC)
    WHERE "eeg_device_id" IS NOT NULL;

NOTIFY pgrst, 'reload schema';
