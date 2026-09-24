-- retention_window.enforced: no row records nothing; true is a real school
-- year needing both dates; false deliberately records year-round. Consent
-- still applies in every case.

ALTER TABLE "public"."retention_window"
    ADD COLUMN IF NOT EXISTS "enforced" boolean NOT NULL DEFAULT true;

COMMENT ON COLUMN "public"."retention_window"."enforced" IS
    'When false, the school-year dates are not applied and recording is allowed '
    'year-round (consent still gates it). For prototyping and for deployments '
    'that do not run on a term. An absent row still records nothing -- this '
    'column is how you say "not gating on a year", which is different from '
    'never having decided.';

-- Nullable for unenforced rows; enforced with a missing date is unconfigured
-- and denies.
ALTER TABLE "public"."retention_window" ALTER COLUMN "starts_on" DROP NOT NULL;
ALTER TABLE "public"."retention_window" ALTER COLUMN "ends_on"   DROP NOT NULL;

-- Both dates or neither: a NULL comparison would pass the old CHECK.
ALTER TABLE "public"."retention_window"
    DROP CONSTRAINT IF EXISTS "retention_window_dates_ordered";
ALTER TABLE "public"."retention_window"
    ADD CONSTRAINT "retention_window_dates_ordered" CHECK (
        ("starts_on" IS NULL AND "ends_on" IS NULL)
        OR ("starts_on" IS NOT NULL AND "ends_on" IS NOT NULL
            AND "ends_on" > "starts_on")
    );

-- Still no seed row.

NOTIFY pgrst, 'reload schema';
