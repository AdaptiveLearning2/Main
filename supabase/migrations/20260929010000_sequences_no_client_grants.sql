-- No client writes any table, so no client role needs a sequence; the identity sequences behind
-- feature_flag_changes, security_events and the rest still carried init.sql's default ALL.
-- Unlike functions, PUBLIC holds no default grant on sequences, so the default privilege does narrow.

REVOKE ALL ON ALL SEQUENCES IN SCHEMA "public" FROM "anon";
REVOKE ALL ON ALL SEQUENCES IN SCHEMA "public" FROM "authenticated";

ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" REVOKE ALL ON SEQUENCES FROM "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" REVOKE ALL ON SEQUENCES FROM "authenticated";
