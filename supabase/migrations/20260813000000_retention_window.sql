-- The school-year retention window (Phase 9).
--
-- An explicit term window rather than a rolling age: recording begins on the
-- first day of school and per-sample rows are deleted on the last day. Both
-- dates are admin-editable. This migration adds only the configuration and its
-- access rules -- the recording gate is backend code in the same change, and
-- the daily rollup and the delete job are separate migrations that depend on
-- this table existing.
--
-- Single row, enforced rather than assumed. `id boolean PRIMARY KEY CHECK (id)`
-- admits exactly one row: `true`. A second INSERT collides on the key instead
-- of quietly creating a second window that some query picks between by
-- `ORDER BY`, which is the failure this shape exists to prevent -- two windows
-- disagreeing about whether recording is allowed is not a state any caller
-- could resolve.

CREATE TABLE IF NOT EXISTS "public"."retention_window" (
    "id"         boolean     PRIMARY KEY DEFAULT true,
    "starts_on"  date        NOT NULL,
    "ends_on"    date        NOT NULL,
    -- One configured school timezone, used for **both** the window boundaries
    -- and the weekly report's day buckets. Those have to agree: a day that the
    -- report counts as Tuesday and the window counts as Wednesday is a row that
    -- exists on one surface and not the other.
    --
    -- Not validated by a CHECK here. `pg_timezone_names` cannot be referenced
    -- from one (no subqueries), and a trigger to do it is more machinery than a
    -- value edited twice a year deserves. The backend validates on read and
    -- refuses to record if it cannot resolve the zone -- see `_retention_window`
    -- for why that direction is the safe one.
    "timezone"   text        NOT NULL DEFAULT 'UTC',
    "updated_by" uuid        REFERENCES "auth"."users"("id") ON DELETE SET NULL,
    "updated_at" timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT "retention_window_single_row" CHECK ("id"),
    -- Strictly greater: a zero-length window is not a school year, and an
    -- inverted one would silently deny all recording with nothing to point at.
    CONSTRAINT "retention_window_dates_ordered" CHECK ("ends_on" > "starts_on")
);

COMMENT ON TABLE "public"."retention_window" IS
    'Single-row school-year config. Outside [starts_on, ends_on] nothing is '
    'recorded, whatever consent says; on ends_on the per-sample rows are '
    'deleted. Edited through the dashboard SQL editor -- there is no admin '
    'role, and inventing one for this table is not worth the surface.';

-- ── access ──────────────────────────────────────────────────────────────────
--
-- No application role touches this, in either direction. Supabase's
-- ALTER DEFAULT PRIVILEGES grants every table privilege to anon and
-- authenticated by name, so the revokes are what actually narrows it -- a bare
-- GRANT on top would read like a restriction and change nothing (CLAUDE.md).
--
-- Deliberately not readable by `authenticated` either. The window's dates are
-- not secret, but nothing in the frontend needs them: the states a student or
-- parent must be able to see ("recording has not started", "the year has
-- ended") are already carried on the consent payload the backend builds, and
-- serving the raw row as well would be a second source for the same fact.

REVOKE ALL ON TABLE "public"."retention_window" FROM "anon";
REVOKE ALL ON TABLE "public"."retention_window" FROM "authenticated";
GRANT ALL ON TABLE "public"."retention_window" TO "service_role";

-- RLS on with **no policies at all**: with none defined, every command is
-- denied for any role that is not BYPASSRLS. That is the whole access model
-- here -- service_role bypasses it, the dashboard connects as postgres, and
-- there is no third caller. Note this does not cover TRUNCATE, which RLS never
-- filters; the revokes above are what stop that, which is why both are present.
ALTER TABLE "public"."retention_window" ENABLE ROW LEVEL SECURITY;

-- No seed row, deliberately. An unconfigured window records nothing, matching
-- the consent default -- an unset date is not an open-ended licence, and
-- inserting a plausible-looking default here would be exactly that. A school
-- that has not configured its year records nothing until someone says when the
-- year is, which is a visible, fixable state rather than a silent assumption.

NOTIFY pgrst, 'reload schema';
