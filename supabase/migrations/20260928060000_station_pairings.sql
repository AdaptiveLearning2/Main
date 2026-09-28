-- Who paired each station's headband. In the database, not the backend's memory, so a restart
-- or a second worker cannot hand a paired headband to another student.

CREATE TABLE IF NOT EXISTS "public"."station_pairings" (
    -- The sidecar's device id, e.g. "default" or "station1".
    "device_id" "text" NOT NULL,
    "user_id" "uuid" NOT NULL,
    -- Refreshed by the pairer's status polls; a pairing nobody has polled for a while is released.
    "seen_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    CONSTRAINT "station_pairings_pkey" PRIMARY KEY ("device_id")
);

ALTER TABLE "public"."station_pairings" OWNER TO "postgres";

ALTER TABLE ONLY "public"."station_pairings"
    ADD CONSTRAINT "station_pairings_user_id_fkey"
    FOREIGN KEY ("user_id") REFERENCES "public"."profiles"("id") ON DELETE CASCADE;

-- RLS with no policies plus revokes (RLS never filters TRUNCATE). Backend only.
ALTER TABLE "public"."station_pairings" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."station_pairings" FROM "anon";
REVOKE ALL ON TABLE "public"."station_pairings" FROM "authenticated";
GRANT ALL ON TABLE "public"."station_pairings" TO "service_role";

NOTIFY pgrst, 'reload schema';
