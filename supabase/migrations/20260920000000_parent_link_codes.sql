-- A code the child generates, so that linking a parent needs an act by the
-- child rather than a string about them.
--
-- `POST /api/parent/link-child` took the child's **user id**, and the flow in
-- `LinkChild.jsx` told the parent to ask the child to read it off their own
-- profile. The intent was that possession of the id is the child's consent to
-- the link. It is not, because the id is not a secret: it is on every roster
-- payload a teacher of that child reads, in the URL of every report page about
-- them, and in the admin student search. Anyone holding one and an account
-- with `role = 'parent'` could link themselves, and from that moment read the
-- child's reports and re-enable a sensor the child had switched off.
--
-- A code fixes what the id cannot be: the child has to do something to bring
-- it into existence, it is short-lived, it is single-use, and it is readable
-- by nothing but the backend. **This does not change "notify, not block"** --
-- `ParentLinkedBanner` still tells the child a link was made, and no endpoint
-- waits on the acknowledgement. What changes is that the handover is now an
-- act, which is what the old flow assumed it already was.
--
-- Deliberately not kept: there is no redemption history here. The row is
-- deleted when the code is used, and `parent_child_links.created_at` is the
-- record that the link happened. Keeping spent codes would add a second,
-- permanent log of which adult linked which child, which is data this product
-- has no reader for -- the same argument the `security_events` migration makes
-- for bounding its own retention.

CREATE TABLE IF NOT EXISTS "public"."parent_link_codes" (
    -- The code itself, uppercase, from an alphabet with no O/0/I/1. Stored in
    -- the clear rather than hashed: only `service_role` can read this table at
    -- all, and the child has to be able to see the code again after navigating
    -- away from the page that made it. Same reasoning as `classes.join_code`.
    "code"       text        PRIMARY KEY,

    -- One outstanding code per child, so generating a new one replaces the
    -- old rather than leaving both live. UNIQUE is what makes that an upsert
    -- instead of a delete-then-insert the backend could half-finish.
    "student_id" uuid        NOT NULL UNIQUE
        REFERENCES "auth"."users"("id") ON DELETE CASCADE,

    "created_at" timestamptz NOT NULL DEFAULT now(),

    -- Written by the backend rather than defaulted here, so the lifetime is
    -- one number in one place (`_LINK_CODE_TTL_SEC`) instead of a column
    -- default and a constant that can disagree.
    "expires_at" timestamptz NOT NULL
);

-- The sweep below reads this; the redemption path looks up by primary key.
CREATE INDEX IF NOT EXISTS "parent_link_codes_expires_at_idx"
    ON "public"."parent_link_codes" ("expires_at");

COMMENT ON TABLE "public"."parent_link_codes" IS
    'Short-lived single-use codes a student generates to let a parent link to '
    'their account. Backend-only: no role but service_role can read a code, '
    'and reading one is enough to become that child''s parent.';

-- Revoke before granting: a new table arrives as anon=arwdDxtm,
-- authenticated=arwdDxtm, so a narrow GRANT on top narrows nothing. Nothing is
-- granted back. **`authenticated` reading this table would undo the change
-- outright** -- `profiles` is readable by a teacher through PostgREST, which
-- is exactly how the user id stopped being a secret, and a code is worth more
-- than an id.
REVOKE ALL ON TABLE "public"."parent_link_codes" FROM "anon";
REVOKE ALL ON TABLE "public"."parent_link_codes" FROM "authenticated";
GRANT ALL ON TABLE "public"."parent_link_codes" TO "service_role";

-- RLS on with no policies: with no policy for a command, that command is
-- denied, so PostgREST cannot reach this table whatever JWT it carries. Both
-- this and the revokes are needed -- RLS does not filter TRUNCATE.
ALTER TABLE "public"."parent_link_codes" ENABLE ROW LEVEL SECURITY;


-- ─── the sweep ───────────────────────────────────────────────────────────
--
-- An expired code is refused by the backend whether or not it is still here,
-- so this is hygiene rather than a control: without it the table keeps one row
-- per code any child ever generated and never used. Unlike the signal expiry
-- there is no rollup to check first -- nothing summarises a code and nothing
-- should, so a guard copied from `expire_signal_rows` would mean these never
-- expire at all. Same argument `expire_session_alerts` makes.
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

-- 03:45, after the three sweeps already scheduled at 03:30, 03:35 and 03:40,
-- so the nightly pass stays one block in the job log. `cron.schedule` upserts
-- on the job name, so re-running this migration re-points the job rather than
-- creating a second one.
SELECT cron.schedule(
    'expire-parent-link-codes',
    '45 3 * * *',
    $$SELECT public.expire_parent_link_codes();$$
);

NOTIFY pgrst, 'reload schema';
