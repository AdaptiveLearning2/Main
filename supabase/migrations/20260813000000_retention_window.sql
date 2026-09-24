-- The school-year retention window: recording starts on starts_on and
-- per-sample rows are deleted on ends_on. The gate itself is backend code.
-- `id boolean PRIMARY KEY CHECK (id)` admits exactly one row.

CREATE TABLE IF NOT EXISTS "public"."retention_window" (
    "id"         boolean     PRIMARY KEY DEFAULT true,
    "starts_on"  date        NOT NULL,
    "ends_on"    date        NOT NULL,
    -- School timezone for window boundaries and report day buckets. Validated
    -- by the backend on read (a CHECK cannot reference pg_timezone_names).
    "timezone"   text        NOT NULL DEFAULT 'UTC',
    "updated_by" uuid        REFERENCES "auth"."users"("id") ON DELETE SET NULL,
    "updated_at" timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT "retention_window_single_row" CHECK ("id"),
    CONSTRAINT "retention_window_dates_ordered" CHECK ("ends_on" > "starts_on")
);

COMMENT ON TABLE "public"."retention_window" IS
    'Single-row school-year config. Outside [starts_on, ends_on] nothing is '
    'recorded, whatever consent says; on ends_on the per-sample rows are '
    'deleted. Edited through the dashboard SQL editor -- there is no admin '
    'role, and inventing one for this table is not worth the surface.';

-- No client role reads or writes it; the consent payload carries its states.

REVOKE ALL ON TABLE "public"."retention_window" FROM "anon";
REVOKE ALL ON TABLE "public"."retention_window" FROM "authenticated";
GRANT ALL ON TABLE "public"."retention_window" TO "service_role";

-- RLS with no policies; the revokes above are what stop TRUNCATE.
ALTER TABLE "public"."retention_window" ENABLE ROW LEVEL SECURITY;

-- No seed row: an unconfigured window records nothing.

NOTIFY pgrst, 'reload schema';
