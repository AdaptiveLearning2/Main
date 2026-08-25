-- Operational alerts for teachers: things that went wrong with a *session*,
-- never claims about a student.
--
-- The scope is the whole design, so it is stated first. An alert here says a
-- lesson ended without the student finishing it, or that recording was
-- expected and nothing arrived. It never says a child is stressed,
-- struggling, or inattentive.
--
-- `signal_fusion` does produce a "stressed" label and no teacher surface
-- consumes it. Routing that here was considered and rejected: it is an
-- inference from signals this codebase already treats as weak, and rendering
-- it to a teacher as a discrete, timestamped event would give it exactly the
-- unearned authority that retired `identity_confidence` and the `attention`
-- tiles (#86). FER+ is not validated on this product's users, and
-- `signal_fusion`'s own rule is that emotion may withhold and never trigger.
-- If that ever changes it needs a labelled reference first, not a table.
--
-- So every kind in the whitelist below is checkable against the database
-- without interpreting a person.

-- Named `session_alerts`, not `alerts`, because `session_id` is NOT NULL and
-- both kinds are facts about one sitting. A future alert that is not about a
-- session does not belong in this table under a widened name -- it has a
-- different retention story (below) and a different access rule.
CREATE TABLE IF NOT EXISTS "public"."session_alerts" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "session_id" "uuid" NOT NULL,
    -- Whitelisted rather than free text, for the reason `_FEATURE_FLAG_DEFAULTS`
    -- is a whitelist: a typo'd kind would otherwise insert cleanly, read back
    -- as a real alert, and render as nothing. Adding a kind is a migration,
    -- which is the right amount of friction for something a teacher is asked
    -- to act on.
    "kind" "text" NOT NULL,
    -- Kind-specific context, e.g. which channels were consented but silent.
    -- Bounded by what the writer puts in it; nothing here is user input.
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

-- Cascade, because an alert about a session that no longer exists is an
-- orphan nothing can resolve or explain. `_discard_if_nothing_recorded`
-- deletes sessions routinely, so this is an ordinary path rather than a
-- corner case -- and it is the reason the emitters below run *after* the
-- discard check rather than before it.
ALTER TABLE ONLY "public"."session_alerts"
    ADD CONSTRAINT "session_alerts_session_id_fkey"
    FOREIGN KEY ("session_id") REFERENCES "public"."sessions"("id") ON DELETE CASCADE;

-- One alert of a kind per session. `_claim_session_close` already makes the
-- close sequence run once, so this is not the primary guard -- it is what
-- makes emission idempotent if that ever fails, or if a kind gains a second
-- emitter later. A duplicate alert is not a small bug on a surface whose
-- whole value is that each row means something happened.
CREATE UNIQUE INDEX IF NOT EXISTS "session_alerts_session_kind_idx"
    ON "public"."session_alerts" USING "btree" ("session_id", "kind");

-- The read path is "this class's roster, recently", so the index leads on the
-- student and orders by time.
CREATE INDEX IF NOT EXISTS "session_alerts_user_created_idx"
    ON "public"."session_alerts" USING "btree" ("user_id", "created_at" DESC);

-- Grants: revoke before granting, since Supabase's ALTER DEFAULT PRIVILEGES
-- hands anon and authenticated every privilege by name before this runs, and
-- a bare GRANT on top would read as a restriction while changing nothing.
--
-- Nothing is granted back to a client role. Teachers read this through the
-- backend, which resolves class ownership first; there is no PostgREST path
-- to it at all. RLS is on with no policies as the second lock -- but note it
-- is not sufficient on its own, since RLS does not filter TRUNCATE.
REVOKE ALL ON TABLE "public"."session_alerts" FROM "anon";
REVOKE ALL ON TABLE "public"."session_alerts" FROM "authenticated";
GRANT ALL ON TABLE "public"."session_alerts" TO "service_role";

ALTER TABLE "public"."session_alerts" ENABLE ROW LEVEL SECURITY;


-- ── retention: the same cutoff as the signal rows, and deliberately not the
--    same guard ───────────────────────────────────────────────────────────
--
-- Alerts expire with the school year, on `expired_signal_cutoff()` -- the
-- same function `expire_signal_rows` uses, so there is one definition of
-- "this year is over" rather than two that can drift.
--
-- **No rollup guard, and that is the important line here.**
-- `expire_signal_rows` refuses a day with no `signal_daily_rollup` row,
-- because the rows it deletes are the only copy and a broken rollup writer
-- would otherwise become silent permanent loss. Nothing summarises alerts and
-- nothing is meant to: they are a short-lived operational surface, not a
-- record anyone reads back at the end of a year. Copying that guard here --
-- the obvious thing to do when modelling this on its neighbour -- would mean
-- alerts never expire at all, since no rollup row will ever exist for them.
--
-- Not batched, also deliberately. `expire_signal_rows` batches because a
-- year of 4 Hz samples is millions of rows; this table takes at most a couple
-- of rows per session, so a full year for a large school is thousands. A
-- plain DELETE holds its lock for milliseconds. Revisit if a kind ever fires
-- per answer rather than per session.
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
    -- Fails closed exactly like the signal job: no window row means no cutoff
    -- and nothing is deleted, rather than a null comparison quietly matching
    -- everything or nothing by accident.
    IF cutoff IS NULL THEN
        RETURN jsonb_build_object('deleted', 0, 'skipped_no_window', true);
    END IF;

    SELECT w.timezone INTO tz FROM retention_window w LIMIT 1;
    tz := COALESCE(tz, 'UTC');

    -- Bucketed at the school's timezone, like every other date boundary here.
    -- Against a UTC clock the last day of the year ends mid-afternoon or runs
    -- into the next day, depending which side of the meridian the school is.
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

-- Its own job rather than a step inside `expire_signal_rows`, because adding
-- a step there would change that function's return shape and the callers
-- reading `deleted`/`skipped_days_without_rollup` from it. Same schedule, a
-- few minutes later, so the two are readable as one nightly sweep in the job
-- log. `cron.schedule` upserts on the name, so re-running this migration
-- re-points the job instead of creating a second one.
CREATE EXTENSION IF NOT EXISTS "pg_cron";

SELECT cron.schedule('expire-session-alerts', '35 3 * * *',
                     $job$SELECT public.expire_session_alerts();$job$);

NOTIFY pgrst, 'reload schema';
