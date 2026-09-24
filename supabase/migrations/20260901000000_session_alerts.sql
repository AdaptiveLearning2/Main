-- Operational alerts for teachers about a *session*, never claims about a
-- student: every kind must be checkable against the database without
-- interpreting a person. Inferred states such as "stressed" do not belong here.
CREATE TABLE IF NOT EXISTS "public"."session_alerts" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "session_id" "uuid" NOT NULL,
    -- Whitelisted, so a typo'd kind cannot insert.
    "kind" "text" NOT NULL,
    -- Kind-specific context written by the backend; never user input.
    "detail" "jsonb" DEFAULT '{}'::"jsonb" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    CONSTRAINT "session_alerts_kind_check" CHECK ("kind" = ANY (ARRAY[
        'session_auto_closed'::"text",
        'signals_missing'::"text"
    ]))
);

ALTER TABLE "public"."session_alerts" OWNER TO "postgres";

ALTER TABLE ONLY "public"."session_alerts"
    ADD CONSTRAINT "session_alerts_pkey" PRIMARY KEY ("id");

-- Cascade: empty sessions are discarded routinely, which is why alerts are
-- raised after the discard check.
ALTER TABLE ONLY "public"."session_alerts"
    ADD CONSTRAINT "session_alerts_session_id_fkey"
    FOREIGN KEY ("session_id") REFERENCES "public"."sessions"("id") ON DELETE CASCADE;

-- One alert of a kind per session; backstop to _claim_session_close.
CREATE UNIQUE INDEX IF NOT EXISTS "session_alerts_session_kind_idx"
    ON "public"."session_alerts" USING "btree" ("session_id", "kind");

CREATE INDEX IF NOT EXISTS "session_alerts_user_created_idx"
    ON "public"."session_alerts" USING "btree" ("user_id", "created_at" DESC);

-- No client grants: read through the backend only. RLS with no policies too,
-- though RLS never filters TRUNCATE.
REVOKE ALL ON TABLE "public"."session_alerts" FROM "anon";
REVOKE ALL ON TABLE "public"."session_alerts" FROM "authenticated";
GRANT ALL ON TABLE "public"."session_alerts" TO "service_role";

ALTER TABLE "public"."session_alerts" ENABLE ROW LEVEL SECURITY;


-- ── retention: expired_signal_cutoff(), deliberately without the rollup guard
-- Nothing summarises alerts, so that guard would mean they never expire.
-- Not batched: a couple of rows per session.
CREATE OR REPLACE FUNCTION "public"."expire_session_alerts"()
RETURNS "jsonb"
LANGUAGE "plpgsql"
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
DECLARE
    cutoff date;
    tz     text;
    n      integer;
BEGIN
    cutoff := expired_signal_cutoff();
    -- No window row: nothing is deleted.
    IF cutoff IS NULL THEN
        RETURN jsonb_build_object('deleted', 0, 'skipped_no_window', true);
    END IF;

    SELECT w.timezone INTO tz FROM retention_window w LIMIT 1;
    tz := COALESCE(tz, 'UTC');

    -- Bucketed in the school's timezone.
    DELETE FROM session_alerts
     WHERE ("created_at" AT TIME ZONE tz)::date <= cutoff;
    GET DIAGNOSTICS n = ROW_COUNT;

    RETURN jsonb_build_object('deleted', n, 'skipped_no_window', false);
END;
$$;

REVOKE ALL ON FUNCTION "public"."expire_session_alerts"() FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."expire_session_alerts"() FROM "anon";
REVOKE ALL ON FUNCTION "public"."expire_session_alerts"() FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."expire_session_alerts"() TO "service_role";

-- Own job, so expire_signal_rows' return shape is unchanged. cron.schedule
-- upserts on the name.
CREATE EXTENSION IF NOT EXISTS "pg_cron";

SELECT cron.schedule('expire-session-alerts', '35 3 * * *',
                     $job$SELECT public.expire_session_alerts();$job$);

NOTIFY pgrst, 'reload schema';
