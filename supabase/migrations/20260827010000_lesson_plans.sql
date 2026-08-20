-- Lesson-plan context for question generation. Grounds the Ollama prompts in
-- an actual curriculum sequence instead of only difficulty/grade heuristics.
-- Keyed by (topic_name, grade_band) at the same granularity as
-- LLM_*_generation.py's own GRADE_COMPLEXITY bands (early/middle/upper/advanced),
-- not by exact grade -- one lesson plan already covers a band of grades there.

CREATE TABLE IF NOT EXISTS "public"."lesson_plans" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "topic_name" "text" NOT NULL,
    "grade_band" "text" NOT NULL,
    "objectives" "text" NOT NULL,
    "notes" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    CONSTRAINT "lesson_plans_grade_band_check" CHECK (("grade_band" = ANY (ARRAY['early'::"text", 'middle'::"text", 'upper'::"text", 'advanced'::"text"])))
);

ALTER TABLE "public"."lesson_plans" OWNER TO "postgres";

ALTER TABLE ONLY "public"."lesson_plans"
    ADD CONSTRAINT "lesson_plans_pkey" PRIMARY KEY ("id");

ALTER TABLE ONLY "public"."lesson_plans"
    ADD CONSTRAINT "lesson_plans_topic_grade_band_key" UNIQUE ("topic_name", "grade_band");

ALTER TABLE "public"."lesson_plans" ENABLE ROW LEVEL SECURITY;

-- Same shape as math_topics/questions: reference content, public read.
CREATE POLICY "lesson_plans: public read" ON "public"."lesson_plans" FOR SELECT USING (true);

-- Supabase already grants every table privilege to anon and authenticated by
-- name, so revoke first -- a bare GRANT on top wouldn't narrow anything.
REVOKE ALL ON TABLE "public"."lesson_plans" FROM "anon";
REVOKE ALL ON TABLE "public"."lesson_plans" FROM "authenticated";

GRANT SELECT ON TABLE "public"."lesson_plans" TO "anon";
GRANT SELECT ON TABLE "public"."lesson_plans" TO "authenticated";
GRANT ALL ON TABLE "public"."lesson_plans" TO "service_role";

NOTIFY pgrst, 'reload schema';
