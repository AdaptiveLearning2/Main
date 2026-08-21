-- Make the school year's enforcement an explicit switch.
--
-- An unconfigured window still records nothing by default -- that's
-- unchanged. What was missing is a way to run a deployment that isn't on a
-- school year yet: without this, prototyping had to invent term dates, which
-- produces a row that looks like a real configured school year and can't be
-- told apart from one.
--
-- So: `enforced`, defaulting to true, and the dates become optional since a
-- row that isn't enforcing has nothing to say about them. Three states to
-- keep apart:
--
--   no row              -- nobody has decided. Records nothing. (unchanged)
--   enforced = true     -- a real school year. Needs both dates; outside them,
--                          and before/after, records nothing. (unchanged)
--   enforced = false    -- deliberately not gating on a term yet. Records.
--
-- The last state is deliberately noisy to arrive at -- it takes an explicit
-- UPDATE naming the column, so it can't be reached by forgetting to do
-- something. Consent still applies in every case; this gate and the consent
-- gate are independent, and `_may_record` needs both.

ALTER TABLE "public"."retention_window"
    ADD COLUMN IF NOT EXISTS "enforced" boolean NOT NULL DEFAULT true;

COMMENT ON COLUMN "public"."retention_window"."enforced" IS
    'When false, the school-year dates are not applied and recording is allowed '
    'year-round (consent still gates it). For prototyping and for deployments '
    'that do not run on a term. An absent row still records nothing -- this '
    'column is how you say "not gating on a year", which is different from '
    'never having decided.';

-- Both dates become nullable, so an unenforced row doesn't have to invent
-- them. Enforcement with a missing date is not treated as unbounded -- the
-- backend reads that as unconfigured and denies, since "enforce the year"
-- with no year given is a half-finished edit, not a request to record forever.
ALTER TABLE "public"."retention_window" ALTER COLUMN "starts_on" DROP NOT NULL;
ALTER TABLE "public"."retention_window" ALTER COLUMN "ends_on"   DROP NOT NULL;

-- The ordering CHECK is replaced rather than kept: `ends_on > starts_on`
-- evaluates to NULL (which passes a CHECK) when either side is NULL, so it
-- would admit a half-configured row. Restated so it says: either both dates
-- are present and ordered, or neither is.
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
