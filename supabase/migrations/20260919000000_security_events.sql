-- Append-only record that a security-relevant thing happened, written by the
-- backend (which has resolved the actor). Never a reading, a request body or
-- an IP address: `detail` is the minimum context to act on. Every kind must
-- be checkable without interpreting a person.

CREATE TABLE IF NOT EXISTS "public"."security_events" (
    "id"         bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- Whitelisted; must equal main._SECURITY_EVENT_KINDS.
    "kind"       text        NOT NULL
        CHECK ("kind" IN (
            'authz_denied',      -- a _verify_* helper refused a relationship
            'admin_denied',      -- a non-admin reached an /api/admin/* route
            'rate_limited',      -- a per-caller limiter returned 429
            'consent_changed'    -- a signal_consent write landed
        )),

    -- SET NULL, so deleting an account cannot erase what it did.
    "actor_user_id"   uuid   REFERENCES "auth"."users"("id") ON DELETE SET NULL,

    -- Whose data was involved; null when there is no subject (rate limits).
    "subject_user_id" uuid   REFERENCES "auth"."users"("id") ON DELETE SET NULL,

    "detail"     jsonb       NOT NULL DEFAULT '{}'::jsonb,

    "created_at" timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS "security_events_created_at_idx"
    ON "public"."security_events" ("created_at" DESC);
CREATE INDEX IF NOT EXISTS "security_events_kind_created_at_idx"
    ON "public"."security_events" ("kind", "created_at" DESC);

COMMENT ON TABLE "public"."security_events" IS
    'Append-only audit of authorization denials, rate limits and consent '
    'changes. Written by the backend. Never contains sensor readings, request '
    'bodies or IP addresses -- see the migration header for why.';

-- No client grants: read via an admin-gated backend endpoint only.
REVOKE ALL ON TABLE "public"."security_events" FROM "anon";
REVOKE ALL ON TABLE "public"."security_events" FROM "authenticated";
GRANT ALL ON TABLE "public"."security_events" TO "service_role";

-- RLS with no policies; the revokes cover TRUNCATE.
ALTER TABLE "public"."security_events" ENABLE ROW LEVEL SECURITY;


-- ─── retention ───────────────────────────────────────────────────────────
-- Rolling 180 days, not the school-year cutoff: the log must not vanish with
-- the signals it describes, and an unbounded one is its own liability.
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

-- After the 03:30 and 03:35 sweeps. cron.schedule upserts on the job name.
SELECT cron.schedule(
    'expire-security-events',
    '40 3 * * *',
    $$SELECT public.expire_security_events();$$
);

NOTIFY pgrst, 'reload schema';
