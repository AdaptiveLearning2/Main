-- A keyset for paging counter reads past PostgREST's silent row cap (`ops_metrics.read`).
-- Its own migration, so a stack that applied the table without it still gets it; an upsert keeps a cell's id.

ALTER TABLE "public"."ops_counters"
    ADD COLUMN IF NOT EXISTS "id" bigint GENERATED ALWAYS AS IDENTITY;

CREATE UNIQUE INDEX IF NOT EXISTS "ops_counters_id_key" ON "public"."ops_counters" ("id");

-- The identity's implicit sequence is a grantable object too.
REVOKE ALL ON SEQUENCE "public"."ops_counters_id_seq" FROM "anon";
REVOKE ALL ON SEQUENCE "public"."ops_counters_id_seq" FROM "authenticated";
GRANT ALL ON SEQUENCE "public"."ops_counters_id_seq" TO "service_role";

NOTIFY pgrst, 'reload schema';
