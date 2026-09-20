-- An append-only record that a security-relevant thing happened.
--
-- There was no audit surface of any kind: a refused read, a rate-limited
-- caller and a consent change left no trace anywhere an administrator could
-- look. Each of those is individually visible in a log line at best, and log
-- lines are not queryable, not retained, and not available to the person who
-- would act on them.
--
-- Modelled on `feature_flag_changes` rather than on `session_alerts`: written
-- by the backend, which has already resolved the acting identity, rather than
-- by a trigger that would have to re-derive it; append-only, with no update or
-- delete path for anyone.
--
-- ─── what may go in here ─────────────────────────────────────────────────
--
-- **Never a reading, never a request body.** `detail` records *that* something
-- happened and the minimum needed to act on it -- which endpoint, which
-- relationship failed, which limiter fired. A focus score, a heart rate, an
-- emotion label or a posted payload has no business in an audit row, and a row
-- that carried one would outlive the retention rules that govern the tables
-- those values actually live in.
--
-- **No IP address.** Two reasons and either is sufficient: it would be new
-- personal data collected about children for a purpose no consent channel
-- covers, and behind a proxy it is whatever `X-Forwarded-For` says, which
-- nothing here is configured to trust.
--
-- **Every kind is checkable without interpreting a person.** Same rule as
-- `session_alerts`: a `stressed` label or a "suspicious behaviour" score would
-- turn an operational log into a judgement about a child, and a timestamped
-- row reads as more objective than it is. If that ever changes it needs a
-- labelled reference first, not a column.

CREATE TABLE IF NOT EXISTS "public"."security_events" (
    "id"         bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- Whitelisted, so a typo cannot create a kind that reads back as recorded
    -- and is filtered out by every reader. Adding one is a migration, which is
    -- the point: it forces the "is this checkable without judging someone"
    -- question through review.
    "kind"       text        NOT NULL
        CHECK ("kind" IN (
            'authz_denied',      -- a _verify_* helper refused a relationship
            'admin_denied',      -- a non-admin reached an /api/admin/* route
            'rate_limited',      -- a per-caller limiter returned 429
            'consent_changed'    -- a signal_consent write landed
        )),

    -- Who acted. Nullable because `ON DELETE SET NULL` outlives the account:
    -- an audit row whose actor has since been deleted is still evidence that
    -- the event happened, and deleting the row instead would let account
    -- deletion erase the log of what that account did.
    "actor_user_id"   uuid   REFERENCES "auth"."users"("id") ON DELETE SET NULL,

    -- Whose data was involved, where that is a different person from the
    -- actor. Null for events with no subject -- a rate limit is about the
    -- caller alone.
    "subject_user_id" uuid   REFERENCES "auth"."users"("id") ON DELETE SET NULL,

    -- Minimal context. See the header: no readings, no bodies.
    "detail"     jsonb       NOT NULL DEFAULT '{}'::jsonb,

    "created_at" timestamptz NOT NULL DEFAULT now()
);

-- The admin view reads newest-first, optionally narrowed to one kind.
CREATE INDEX IF NOT EXISTS "security_events_created_at_idx"
    ON "public"."security_events" ("created_at" DESC);
CREATE INDEX IF NOT EXISTS "security_events_kind_created_at_idx"
    ON "public"."security_events" ("kind", "created_at" DESC);

COMMENT ON TABLE "public"."security_events" IS
    'Append-only audit of authorization denials, rate limits and consent '
    'changes. Written by the backend. Never contains sensor readings, request '
    'bodies or IP addresses -- see the migration header for why.';

-- Revoke before granting: a new table arrives as anon=arwdDxtm,
-- authenticated=arwdDxtm, so a narrow GRANT on top narrows nothing. Nothing is
-- granted back to either role -- this is read through the backend's
-- service-role client by an admin-gated endpoint, and RLS does not filter
-- TRUNCATE.
REVOKE ALL ON TABLE "public"."security_events" FROM "anon";
REVOKE ALL ON TABLE "public"."security_events" FROM "authenticated";
GRANT ALL ON TABLE "public"."security_events" TO "service_role";

-- RLS on with no policies: with no policy for a command, that command is
-- denied, so PostgREST cannot reach this table whatever JWT it carries --
-- including the anon key that ships in the frontend bundle. The only correct
-- writer is the backend.
ALTER TABLE "public"."security_events" ENABLE ROW LEVEL SECURITY;


-- ─── retention ───────────────────────────────────────────────────────────
--
-- A rolling window, deliberately unlike every other expiry here, which hangs
-- off `expired_signal_cutoff()` and the school year. Two reasons:
--
--   - The value of an audit row is retrospective and does not end when a term
--     does. "Who looked at this child's record in March" is a question asked
--     in June.
--   - It must not be possible for the log to outlive the data it describes by
--     accident, nor to vanish with it. Tying it to the same cutoff would do
--     the second: the term ends, the signals expire, and the record of who
--     read them goes at the same moment.
--
-- Bounded rather than kept forever, because these rows are still behavioural
-- data about children -- who accessed whose record, when. An unbounded
-- append-only log of that is its own liability, and "we kept it in case" is
-- not a retention policy.
CREATE OR REPLACE FUNCTION "public"."expire_security_events"()
RETURNS "jsonb"
LANGUAGE "plpgsql"
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
DECLARE
    n integer;
BEGIN
    DELETE FROM security_events
     WHERE "created_at" < now() - interval '180 days';
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN jsonb_build_object('deleted', n);
END;
$$;

REVOKE ALL ON FUNCTION "public"."expire_security_events"() FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."expire_security_events"() FROM "anon";
REVOKE ALL ON FUNCTION "public"."expire_security_events"() FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."expire_security_events"() TO "service_role";

-- 03:40, after the signal sweep at 03:30 and the alert sweep at 03:35, so the
-- three read as one nightly pass in the job log. `cron.schedule` upserts on
-- the job name, so re-running this migration re-points the job rather than
-- creating a second one that would delete twice.
SELECT cron.schedule(
    'expire-security-events',
    '40 3 * * *',
    $$SELECT public.expire_security_events();$$
);

NOTIFY pgrst, 'reload schema';
