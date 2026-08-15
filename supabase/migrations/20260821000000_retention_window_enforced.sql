-- Make the school year's enforcement an explicit switch.
--
-- `20260813000000` deliberately has no seed row: an unconfigured window records
-- nothing, because an unset date is not an open-ended licence. That default is
-- unchanged here and must stay unchanged -- an absent row still denies.
--
-- What it did not allow for is a deployment that is not a school year yet.
-- Prototyping has no term dates, and the only way to record anything was to
-- invent a pair -- which produces a row that *looks* like a configured school
-- year, on a system where the whole point of the row is to say when recording
-- is permitted. A fake September-to-June is worse than no window at all,
-- because the next reader cannot tell it from a real one.
--
-- So: `enforced`, defaulting to true, and the dates become optional because a
-- row that is not enforcing has nothing to say about them. The three states a
-- reader has to keep apart:
--
--   no row              -- nobody has decided. Records nothing. (unchanged)
--   enforced = true     -- a real school year. Needs both dates; outside them,
--                          and before/after, records nothing. (unchanged)
--   enforced = false    -- deliberately not gating on a term yet. Records.
--
-- The last of those is the new one, and it is deliberately noisy to arrive at:
-- it takes an explicit UPDATE naming the column, so it cannot be reached by
-- forgetting to do something. Consent still applies in every case -- this gate
-- and the consent gate are independent, and `_may_record` needs both.

ALTER TABLE "public"."retention_window"
    ADD COLUMN IF NOT EXISTS "enforced" boolean NOT NULL DEFAULT true;

COMMENT ON COLUMN "public"."retention_window"."enforced" IS
    'When false, the school-year dates are not applied and recording is allowed '
    'year-round (consent still gates it). For prototyping and for deployments '
    'that do not run on a term. An absent row still records nothing -- this '
    'column is how you say "not gating on a year", which is different from '
    'never having decided.';

-- Both dates become nullable, so an unenforced row does not have to invent
-- them. Enforcement with a missing date is not silently treated as unbounded:
-- the backend reads that as unconfigured and denies, because "enforce the year"
-- with no year given is a half-finished edit, not a request to record for ever.
ALTER TABLE "public"."retention_window" ALTER COLUMN "starts_on" DROP NOT NULL;
ALTER TABLE "public"."retention_window" ALTER COLUMN "ends_on"   DROP NOT NULL;

-- The ordering CHECK has to be replaced rather than kept: `ends_on > starts_on`
-- evaluates to NULL when either side is NULL, and a CHECK passes on NULL, so it
-- would admit the half-configured row above at the database level. Restated so
-- the constraint says what it means -- either both dates are present and
-- ordered, or neither is.
ALTER TABLE "public"."retention_window"
    DROP CONSTRAINT IF EXISTS "retention_window_dates_ordered";
ALTER TABLE "public"."retention_window"
    ADD CONSTRAINT "retention_window_dates_ordered" CHECK (
        ("starts_on" IS NULL AND "ends_on" IS NULL)
        OR ("starts_on" IS NOT NULL AND "ends_on" IS NOT NULL
            AND "ends_on" > "starts_on")
    );

-- Still no seed row. Adding one here would be the open-ended licence the
-- original migration refused to grant, just spelled differently.

NOTIFY pgrst, 'reload schema';
