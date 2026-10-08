-- Hourly operational counters for the admin console, flushed by `backend/ops_metrics.py`.
-- Counts and durations only: a key never names a person, an address or a reading.

CREATE TABLE IF NOT EXISTS "public"."ops_counters" (
    "hour" timestamptz      NOT NULL,   -- UTC hour the events fell in
    "kind" text             NOT NULL,
    "key"  text             NOT NULL,
    "n"    bigint           NOT NULL DEFAULT 0,
    "sum"  double precision,            -- null for a plain count
    "max"  double precision,
    PRIMARY KEY ("hour", "kind", "key")
);

CREATE INDEX IF NOT EXISTS "ops_counters_kind_hour_idx"
    ON "public"."ops_counters" ("kind", "hour" DESC);

REVOKE ALL ON TABLE "public"."ops_counters" FROM "anon";
REVOKE ALL ON TABLE "public"."ops_counters" FROM "authenticated";
GRANT ALL ON TABLE "public"."ops_counters" TO "service_role";

-- RLS with no policies; the revokes cover TRUNCATE.
ALTER TABLE "public"."ops_counters" ENABLE ROW LEVEL SECURITY;


-- One upsert per flush. Adds `n` and `sum`, keeps the larger `max`.
CREATE OR REPLACE FUNCTION "public"."ops_counters_add"("p_rows" "jsonb")
RETURNS integer
LANGUAGE "plpgsql"
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
DECLARE
    n integer;
BEGIN
    INSERT INTO ops_counters AS c ("hour", "kind", "key", "n", "sum", "max")
    SELECT r."hour", r."kind", r."key", r."n", r."sum", r."max"
      FROM jsonb_to_recordset(p_rows)
           AS r("hour" timestamptz, "kind" text, "key" text, "n" bigint,
                "sum" double precision, "max" double precision)
    ON CONFLICT ("hour", "kind", "key") DO UPDATE
       SET "n"   = c."n" + EXCLUDED."n",
           "sum" = CASE WHEN EXCLUDED."sum" IS NULL THEN c."sum"
                        ELSE coalesce(c."sum", 0) + EXCLUDED."sum" END,
           "max" = greatest(c."max", EXCLUDED."max");
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$;

REVOKE ALL ON FUNCTION "public"."ops_counters_add"("jsonb") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."ops_counters_add"("jsonb") FROM "anon";
REVOKE ALL ON FUNCTION "public"."ops_counters_add"("jsonb") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."ops_counters_add"("jsonb") TO "service_role";


-- ─── retention ───────────────────────────────────────────────────────────
-- Rolling 90 days: long enough to compare a term's weeks, short enough to stay small.
CREATE OR REPLACE FUNCTION "public"."expire_ops_counters"()
RETURNS "jsonb"
LANGUAGE "plpgsql"
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
DECLARE
    n integer;
BEGIN
    DELETE FROM ops_counters WHERE "hour" < now() - interval '90 days';
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN jsonb_build_object('deleted', n);
END;
$$;

REVOKE ALL ON FUNCTION "public"."expire_ops_counters"() FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."expire_ops_counters"() FROM "anon";
REVOKE ALL ON FUNCTION "public"."expire_ops_counters"() FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."expire_ops_counters"() TO "service_role";

-- After the other nightly jobs (03:30-03:45). cron.schedule upserts on the job name.
SELECT cron.schedule(
    'expire-ops-counters',
    '50 3 * * *',
    $$SELECT public.expire_ops_counters();$$
);

NOTIFY pgrst, 'reload schema';
