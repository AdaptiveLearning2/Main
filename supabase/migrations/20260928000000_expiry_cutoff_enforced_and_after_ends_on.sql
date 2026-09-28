-- expired_signal_cutoff: an unenforced year expires nothing, and ends_on expires only once it
-- has passed. Recording is open on ends_on (the last day of school is a school day), so a
-- cutoff of ends_on on that day would delete rows the same day goes on recording.
CREATE OR REPLACE FUNCTION "public"."expired_signal_cutoff"()
RETURNS date
LANGUAGE "sql"
STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
    SELECT CASE
        -- Not gating on a term: nothing is a finished year. Dates can linger from before.
        WHEN w.enforced IS FALSE THEN NULL
        WHEN (now() AT TIME ZONE w.timezone)::date > w.ends_on THEN w.ends_on
        ELSE w.starts_on - 1
    END
    FROM retention_window w
    LIMIT 1;
$$;

REVOKE ALL ON FUNCTION "public"."expired_signal_cutoff"() FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."expired_signal_cutoff"() FROM "anon";
REVOKE ALL ON FUNCTION "public"."expired_signal_cutoff"() FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."expired_signal_cutoff"() TO "service_role";

NOTIFY pgrst, 'reload schema';
