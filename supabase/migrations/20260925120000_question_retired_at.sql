-- A question with a wrong stored answer that students have already answered is retired, not
-- rewritten: its answers keep the text and options they were given. The bank and reuse skip it.
ALTER TABLE "public"."questions" ADD COLUMN IF NOT EXISTS "retired_at" timestamp with time zone;

NOTIFY pgrst, 'reload schema';
