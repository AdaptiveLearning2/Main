-- A short-lived, single-use code the child generates to let a parent link; a
-- user id is not a secret. Still "notify, not block". Deleted on use:
-- parent_child_links.created_at is the record, with no redemption history.

CREATE TABLE IF NOT EXISTS "public"."parent_link_codes" (
    -- Uppercase, no O/0/I/1. In the clear: service_role only, and the child
    -- must be able to see it again.
    "code"       text        PRIMARY KEY,

    -- One outstanding code per child; UNIQUE makes a new one an upsert.
    "student_id" uuid        NOT NULL UNIQUE
        REFERENCES "auth"."users"("id") ON DELETE CASCADE,

    "created_at" timestamptz NOT NULL DEFAULT now(),

    -- Set by the backend from _LINK_CODE_TTL_SEC; no column default.
    "expires_at" timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS "parent_link_codes_expires_at_idx"
    ON "public"."parent_link_codes" ("expires_at");

COMMENT ON TABLE "public"."parent_link_codes" IS
    'Short-lived single-use codes a student generates to let a parent link to '
    'their account. Backend-only: no role but service_role can read a code, '
    'and reading one is enough to become that child''s parent.';

-- Nothing granted back: a client that can read a code can become a parent.
REVOKE ALL ON TABLE "public"."parent_link_codes" FROM "anon";
REVOKE ALL ON TABLE "public"."parent_link_codes" FROM "authenticated";
GRANT ALL ON TABLE "public"."parent_link_codes" TO "service_role";

-- RLS with no policies; the revokes cover TRUNCATE.
ALTER TABLE "public"."parent_link_codes" ENABLE ROW LEVEL SECURITY;


-- ─── the sweep ───────────────────────────────────────────────────────────
-- Hygiene, not a control: the backend refuses expired codes anyway. No rollup
-- guard, or codes would never expire.
CREATE OR REPLACE FUNCTION "public"."expire_parent_link_codes"()
RETURNS "jsonb"
LANGUAGE "plpgsql"
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
DECLARE
    n integer;
BEGIN
    DELETE FROM parent_link_codes WHERE "expires_at" < now();
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN jsonb_build_object('deleted', n);
END;
$$;

REVOKE ALL ON FUNCTION "public"."expire_parent_link_codes"() FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."expire_parent_link_codes"() FROM "anon";
REVOKE ALL ON FUNCTION "public"."expire_parent_link_codes"() FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."expire_parent_link_codes"() TO "service_role";

-- After the 03:30-03:40 sweeps. cron.schedule upserts on the job name.
SELECT cron.schedule(
    'expire-parent-link-codes',
    '45 3 * * *',
    $$SELECT public.expire_parent_link_codes();$$
);

NOTIFY pgrst, 'reload schema';
