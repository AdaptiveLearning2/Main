-- The school-year retention window. Recording begins on the first day of
-- school and per-sample rows are deleted on the last day; both dates are
-- admin-editable. This migration adds only the table and its access rules --
-- the recording gate is backend code, and the rollup and delete job are
-- separate migrations depending on this table.
--
-- Single row, enforced rather than assumed: `id boolean PRIMARY KEY CHECK
-- (id)` admits exactly one row. A second INSERT collides on the key instead
-- of quietly creating a second window that some query has to pick between.

CREATE TABLE IF NOT EXISTS "public"."retention_window" (
    "id"         boolean     PRIMARY KEY DEFAULT true,
    "starts_on"  date        NOT NULL,
    "ends_on"    date        NOT NULL,
    -- One configured school timezone, used for both the window boundaries and
    -- the weekly report's day buckets. Those must agree, or a day could count
    -- as Tuesday on one surface and Wednesday on the other.
    --
    -- Not validated by a CHECK -- `pg_timezone_names` can't be referenced from
    -- one, and a trigger is more machinery than a twice-a-year edit deserves.
    -- The backend validates on read and refuses to record if it can't resolve
    -- the zone.
    "timezone"   text        NOT NULL DEFAULT 'UTC',
    "updated_by" uuid        REFERENCES "auth"."users"("id") ON DELETE SET NULL,
    "updated_at" timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT "retention_window_single_row" CHECK ("id"),
    -- Strictly greater: a zero-length or inverted window would silently deny
    -- all recording with nothing to point at.
    CONSTRAINT "retention_window_dates_ordered" CHECK ("ends_on" > "starts_on")
);

COMMENT ON TABLE "public"."retention_window" IS
    'Single-row school-year config. Outside [starts_on, ends_on] nothing is '
    'recorded, whatever consent says; on ends_on the per-sample rows are '
    'deleted. Edited through the dashboard SQL editor -- there is no admin '
    'role, and inventing one for this table is not worth the surface.';

-- No application role touches this table, in either direction. Supabase grants
-- every table privilege to anon and authenticated by name, so the revokes are
-- what actually narrows it -- a bare GRANT on top would change nothing.
--
-- Not readable by `authenticated` either: the states a student or parent need
-- ("recording has not started", "the year has ended") are already carried on
-- the consent payload the backend builds, so exposing the raw row too would
-- be a second source for the same fact.

REVOKE ALL ON TABLE "public"."retention_window" FROM "anon";
REVOKE ALL ON TABLE "public"."retention_window" FROM "authenticated";
GRANT ALL ON TABLE "public"."retention_window" TO "service_role";

-- RLS on with no policies at all: every command is denied for any role that
-- isn't BYPASSRLS. service_role bypasses it, the dashboard connects as
-- postgres, and there's no third caller. RLS doesn't cover TRUNCATE though --
-- the revokes above are what stop that.
ALTER TABLE "public"."retention_window" ENABLE ROW LEVEL SECURITY;

-- No seed row, deliberately. An unconfigured window records nothing, matching
-- the consent default -- an unset date is not an open licence to record. A
-- school that hasn't configured its year records nothing until someone sets
-- the dates, a visible and fixable state rather than a silent assumption.

NOTIFY pgrst, 'reload schema';
