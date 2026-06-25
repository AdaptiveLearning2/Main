


SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;


CREATE SCHEMA IF NOT EXISTS "public";


ALTER SCHEMA "public" OWNER TO "pg_database_owner";


COMMENT ON SCHEMA "public" IS 'standard public schema';



CREATE OR REPLACE FUNCTION "public"."handle_new_user"() RETURNS "trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
begin
  insert into public.profiles (id, display_name, email, role)
  values (
    new.id,
    coalesce(new.raw_user_meta_data->>'display_name', split_part(new.email, '@', 1)),
    new.email,
    coalesce(new.raw_user_meta_data->>'role', 'student')
  )
  on conflict (id) do nothing;
  return new;
end;
$$;


ALTER FUNCTION "public"."handle_new_user"() OWNER TO "postgres";

SET default_tablespace = '';

SET default_table_access_method = "heap";


CREATE TABLE IF NOT EXISTS "public"."class_memberships" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "class_id" "uuid" NOT NULL,
    "student_id" "uuid" NOT NULL,
    "joined_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."class_memberships" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."classes" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "teacher_id" "uuid" NOT NULL,
    "name" "text" NOT NULL,
    "grade_level" "text",
    "join_code" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."classes" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."cognitive_signals" (
    "id" bigint NOT NULL,
    "session_id" "uuid" NOT NULL,
    "user_id" "uuid" NOT NULL,
    "ts" timestamp with time zone DEFAULT "now"() NOT NULL,
    "focus" double precision,
    "stress" double precision,
    "engagement" double precision,
    "alpha" double precision,
    "beta" double precision,
    "theta" double precision,
    "delta" double precision,
    "gamma" double precision,
    "raw" "jsonb"
);


ALTER TABLE "public"."cognitive_signals" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."cognitive_signals_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."cognitive_signals_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."cognitive_signals_id_seq" OWNED BY "public"."cognitive_signals"."id";



CREATE TABLE IF NOT EXISTS "public"."face_signals" (
    "id" bigint NOT NULL,
    "session_id" "uuid" NOT NULL,
    "user_id" "uuid" NOT NULL,
    "ts" timestamp with time zone DEFAULT "now"() NOT NULL,
    "emotion" "text",
    "attention" double precision,
    "gaze_x" double precision,
    "gaze_y" double precision,
    "identity_confidence" double precision,
    "raw" "jsonb"
);


ALTER TABLE "public"."face_signals" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."face_signals_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."face_signals_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."face_signals_id_seq" OWNED BY "public"."face_signals"."id";



CREATE TABLE IF NOT EXISTS "public"."math_topics" (
    "id" integer NOT NULL,
    "topic_name" "text" NOT NULL
);


ALTER TABLE "public"."math_topics" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."math_topics_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."math_topics_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."math_topics_id_seq" OWNED BY "public"."math_topics"."id";



CREATE TABLE IF NOT EXISTS "public"."parent_child_links" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "parent_id" "uuid" NOT NULL,
    "child_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."parent_child_links" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."profiles" (
    "id" "uuid" NOT NULL,
    "display_name" "text",
    "email" "text",
    "role" "text" DEFAULT 'student'::"text" NOT NULL,
    "grade_level" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    CONSTRAINT "profiles_role_check" CHECK (("role" = ANY (ARRAY['student'::"text", 'teacher'::"text", 'parent'::"text"])))
);


ALTER TABLE "public"."profiles" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."questions" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "subject" "text",
    "difficulty" "text",
    "question_text" "text" NOT NULL,
    "options" "jsonb",
    "correct_answer" "text",
    "grade_level" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    CONSTRAINT "questions_difficulty_check" CHECK (("difficulty" = ANY (ARRAY['easy'::"text", 'medium'::"text", 'hard'::"text"])))
);


ALTER TABLE "public"."questions" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."session_answers" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "session_id" "uuid" NOT NULL,
    "user_id" "uuid" NOT NULL,
    "question_id" "uuid",
    "selected_index" integer,
    "correct" boolean DEFAULT false NOT NULL,
    "answered_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."session_answers" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."sessions" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "class_id" "uuid",
    "title" "text" DEFAULT 'Practice Session'::"text" NOT NULL,
    "started_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "ended_at" timestamp with time zone,
    "questions_answered" integer DEFAULT 0 NOT NULL,
    "correct_answers" integer DEFAULT 0 NOT NULL
);


ALTER TABLE "public"."sessions" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."user_math_performance" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "topic_id" integer NOT NULL,
    "attempted_questions" integer DEFAULT 0 NOT NULL,
    "correct_questions" integer DEFAULT 0 NOT NULL,
    "accuracy" double precision GENERATED ALWAYS AS (
CASE
    WHEN ("attempted_questions" = 0) THEN NULL::double precision
    ELSE (("correct_questions")::double precision / ("attempted_questions")::double precision)
END) STORED,
    "stress" double precision,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."user_math_performance" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."user_stats" (
    "user_id" "uuid" NOT NULL,
    "total_questions" integer DEFAULT 0 NOT NULL,
    "total_correct" integer DEFAULT 0 NOT NULL,
    "current_streak" integer DEFAULT 0 NOT NULL,
    "best_streak" integer DEFAULT 0 NOT NULL,
    "last_session_at" timestamp with time zone,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."user_stats" OWNER TO "postgres";


ALTER TABLE ONLY "public"."cognitive_signals" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."cognitive_signals_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."face_signals" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."face_signals_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."math_topics" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."math_topics_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."class_memberships"
    ADD CONSTRAINT "class_memberships_class_id_student_id_key" UNIQUE ("class_id", "student_id");



ALTER TABLE ONLY "public"."class_memberships"
    ADD CONSTRAINT "class_memberships_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."classes"
    ADD CONSTRAINT "classes_join_code_key" UNIQUE ("join_code");



ALTER TABLE ONLY "public"."classes"
    ADD CONSTRAINT "classes_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."cognitive_signals"
    ADD CONSTRAINT "cognitive_signals_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."face_signals"
    ADD CONSTRAINT "face_signals_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."math_topics"
    ADD CONSTRAINT "math_topics_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."math_topics"
    ADD CONSTRAINT "math_topics_topic_name_key" UNIQUE ("topic_name");



ALTER TABLE ONLY "public"."parent_child_links"
    ADD CONSTRAINT "parent_child_links_parent_id_child_id_key" UNIQUE ("parent_id", "child_id");



ALTER TABLE ONLY "public"."parent_child_links"
    ADD CONSTRAINT "parent_child_links_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."profiles"
    ADD CONSTRAINT "profiles_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."questions"
    ADD CONSTRAINT "questions_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."session_answers"
    ADD CONSTRAINT "session_answers_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."sessions"
    ADD CONSTRAINT "sessions_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."user_math_performance"
    ADD CONSTRAINT "user_math_performance_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."user_math_performance"
    ADD CONSTRAINT "user_math_performance_user_id_topic_id_key" UNIQUE ("user_id", "topic_id");



ALTER TABLE ONLY "public"."user_stats"
    ADD CONSTRAINT "user_stats_pkey" PRIMARY KEY ("user_id");



CREATE INDEX "answers_session_idx" ON "public"."session_answers" USING "btree" ("session_id");



CREATE INDEX "answers_user_idx" ON "public"."session_answers" USING "btree" ("user_id");



CREATE INDEX "classes_join_code_idx" ON "public"."classes" USING "btree" ("join_code");



CREATE INDEX "classes_teacher_idx" ON "public"."classes" USING "btree" ("teacher_id");



CREATE INDEX "cog_session_idx" ON "public"."cognitive_signals" USING "btree" ("session_id");



CREATE INDEX "cog_ts_idx" ON "public"."cognitive_signals" USING "btree" ("ts" DESC);



CREATE INDEX "cog_user_idx" ON "public"."cognitive_signals" USING "btree" ("user_id");



CREATE INDEX "face_session_idx" ON "public"."face_signals" USING "btree" ("session_id");



CREATE INDEX "face_ts_idx" ON "public"."face_signals" USING "btree" ("ts" DESC);



CREATE INDEX "face_user_idx" ON "public"."face_signals" USING "btree" ("user_id");



CREATE INDEX "memberships_class_idx" ON "public"."class_memberships" USING "btree" ("class_id");



CREATE INDEX "memberships_student_idx" ON "public"."class_memberships" USING "btree" ("student_id");



CREATE INDEX "pcl_child_idx" ON "public"."parent_child_links" USING "btree" ("child_id");



CREATE INDEX "pcl_parent_idx" ON "public"."parent_child_links" USING "btree" ("parent_id");



CREATE INDEX "perf_topic_idx" ON "public"."user_math_performance" USING "btree" ("topic_id");



CREATE INDEX "perf_user_idx" ON "public"."user_math_performance" USING "btree" ("user_id");



CREATE INDEX "questions_difficulty_idx" ON "public"."questions" USING "btree" ("difficulty");



CREATE INDEX "questions_subject_idx" ON "public"."questions" USING "btree" ("subject");



CREATE INDEX "sessions_class_idx" ON "public"."sessions" USING "btree" ("class_id");



CREATE INDEX "sessions_user_idx" ON "public"."sessions" USING "btree" ("user_id");



ALTER TABLE ONLY "public"."class_memberships"
    ADD CONSTRAINT "class_memberships_class_id_fkey" FOREIGN KEY ("class_id") REFERENCES "public"."classes"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."class_memberships"
    ADD CONSTRAINT "class_memberships_student_id_fkey" FOREIGN KEY ("student_id") REFERENCES "public"."profiles"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."classes"
    ADD CONSTRAINT "classes_teacher_id_fkey" FOREIGN KEY ("teacher_id") REFERENCES "public"."profiles"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."cognitive_signals"
    ADD CONSTRAINT "cognitive_signals_session_id_fkey" FOREIGN KEY ("session_id") REFERENCES "public"."sessions"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."cognitive_signals"
    ADD CONSTRAINT "cognitive_signals_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."profiles"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."face_signals"
    ADD CONSTRAINT "face_signals_session_id_fkey" FOREIGN KEY ("session_id") REFERENCES "public"."sessions"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."face_signals"
    ADD CONSTRAINT "face_signals_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."profiles"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."parent_child_links"
    ADD CONSTRAINT "parent_child_links_child_id_fkey" FOREIGN KEY ("child_id") REFERENCES "public"."profiles"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."parent_child_links"
    ADD CONSTRAINT "parent_child_links_parent_id_fkey" FOREIGN KEY ("parent_id") REFERENCES "public"."profiles"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."profiles"
    ADD CONSTRAINT "profiles_id_fkey" FOREIGN KEY ("id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."session_answers"
    ADD CONSTRAINT "session_answers_question_id_fkey" FOREIGN KEY ("question_id") REFERENCES "public"."questions"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."session_answers"
    ADD CONSTRAINT "session_answers_session_id_fkey" FOREIGN KEY ("session_id") REFERENCES "public"."sessions"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."session_answers"
    ADD CONSTRAINT "session_answers_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."profiles"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."sessions"
    ADD CONSTRAINT "sessions_class_id_fkey" FOREIGN KEY ("class_id") REFERENCES "public"."classes"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."sessions"
    ADD CONSTRAINT "sessions_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."profiles"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."user_math_performance"
    ADD CONSTRAINT "user_math_performance_topic_id_fkey" FOREIGN KEY ("topic_id") REFERENCES "public"."math_topics"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."user_math_performance"
    ADD CONSTRAINT "user_math_performance_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."profiles"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."user_stats"
    ADD CONSTRAINT "user_stats_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."profiles"("id") ON DELETE CASCADE;



CREATE POLICY "answers: own" ON "public"."session_answers" USING (("auth"."uid"() = "user_id"));



ALTER TABLE "public"."class_memberships" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."classes" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "classes: member read" ON "public"."classes" FOR SELECT USING ((EXISTS ( SELECT 1
   FROM "public"."class_memberships"
  WHERE (("class_memberships"."class_id" = "class_memberships"."id") AND ("class_memberships"."student_id" = "auth"."uid"())))));



CREATE POLICY "classes: teacher owns" ON "public"."classes" USING (("auth"."uid"() = "teacher_id"));



CREATE POLICY "cog: own" ON "public"."cognitive_signals" USING (("auth"."uid"() = "user_id"));



CREATE POLICY "cog: teacher read" ON "public"."cognitive_signals" FOR SELECT USING ((EXISTS ( SELECT 1
   FROM ("public"."class_memberships" "cm"
     JOIN "public"."classes" "c" ON (("c"."id" = "cm"."class_id")))
  WHERE (("cm"."student_id" = "cognitive_signals"."user_id") AND ("c"."teacher_id" = "auth"."uid"())))));



ALTER TABLE "public"."cognitive_signals" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "face: own" ON "public"."face_signals" USING (("auth"."uid"() = "user_id"));



CREATE POLICY "face: teacher read" ON "public"."face_signals" FOR SELECT USING ((EXISTS ( SELECT 1
   FROM ("public"."class_memberships" "cm"
     JOIN "public"."classes" "c" ON (("c"."id" = "cm"."class_id")))
  WHERE (("cm"."student_id" = "face_signals"."user_id") AND ("c"."teacher_id" = "auth"."uid"())))));



ALTER TABLE "public"."face_signals" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."math_topics" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "math_topics: public read" ON "public"."math_topics" FOR SELECT USING (true);



CREATE POLICY "memberships: own" ON "public"."class_memberships" USING (("auth"."uid"() = "student_id"));



CREATE POLICY "memberships: teacher read" ON "public"."class_memberships" FOR SELECT USING ((EXISTS ( SELECT 1
   FROM "public"."classes"
  WHERE (("classes"."id" = "class_memberships"."class_id") AND ("classes"."teacher_id" = "auth"."uid"())))));



ALTER TABLE "public"."parent_child_links" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "pcl: child read" ON "public"."parent_child_links" FOR SELECT USING (("auth"."uid"() = "child_id"));



CREATE POLICY "pcl: own" ON "public"."parent_child_links" USING (("auth"."uid"() = "parent_id"));



CREATE POLICY "perf: own" ON "public"."user_math_performance" USING (("auth"."uid"() = "user_id"));



CREATE POLICY "perf: parent read" ON "public"."user_math_performance" FOR SELECT USING ((EXISTS ( SELECT 1
   FROM "public"."parent_child_links"
  WHERE (("parent_child_links"."child_id" = "user_math_performance"."user_id") AND ("parent_child_links"."parent_id" = "auth"."uid"())))));



CREATE POLICY "perf: teacher read" ON "public"."user_math_performance" FOR SELECT USING ((EXISTS ( SELECT 1
   FROM ("public"."class_memberships" "cm"
     JOIN "public"."classes" "c" ON (("c"."id" = "cm"."class_id")))
  WHERE (("cm"."student_id" = "user_math_performance"."user_id") AND ("c"."teacher_id" = "auth"."uid"())))));



ALTER TABLE "public"."profiles" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "profiles: own row" ON "public"."profiles" USING (("auth"."uid"() = "id"));



ALTER TABLE "public"."questions" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "questions: public read" ON "public"."questions" FOR SELECT USING (true);



ALTER TABLE "public"."session_answers" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."sessions" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "sessions: own" ON "public"."sessions" USING (("auth"."uid"() = "user_id"));



CREATE POLICY "sessions: teacher read" ON "public"."sessions" FOR SELECT USING ((EXISTS ( SELECT 1
   FROM ("public"."class_memberships" "cm"
     JOIN "public"."classes" "c" ON (("c"."id" = "cm"."class_id")))
  WHERE (("cm"."student_id" = "sessions"."user_id") AND ("c"."teacher_id" = "auth"."uid"())))));



CREATE POLICY "stats: own write" ON "public"."user_stats" USING (("auth"."uid"() = "user_id"));



CREATE POLICY "stats: public read" ON "public"."user_stats" FOR SELECT USING (true);



ALTER TABLE "public"."user_math_performance" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."user_stats" ENABLE ROW LEVEL SECURITY;


GRANT USAGE ON SCHEMA "public" TO "postgres";
GRANT USAGE ON SCHEMA "public" TO "anon";
GRANT USAGE ON SCHEMA "public" TO "authenticated";
GRANT USAGE ON SCHEMA "public" TO "service_role";



GRANT ALL ON FUNCTION "public"."handle_new_user"() TO "anon";
GRANT ALL ON FUNCTION "public"."handle_new_user"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."handle_new_user"() TO "service_role";



GRANT ALL ON TABLE "public"."class_memberships" TO "anon";
GRANT ALL ON TABLE "public"."class_memberships" TO "authenticated";
GRANT ALL ON TABLE "public"."class_memberships" TO "service_role";



GRANT ALL ON TABLE "public"."classes" TO "anon";
GRANT ALL ON TABLE "public"."classes" TO "authenticated";
GRANT ALL ON TABLE "public"."classes" TO "service_role";



GRANT ALL ON TABLE "public"."cognitive_signals" TO "anon";
GRANT ALL ON TABLE "public"."cognitive_signals" TO "authenticated";
GRANT ALL ON TABLE "public"."cognitive_signals" TO "service_role";



GRANT ALL ON SEQUENCE "public"."cognitive_signals_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."cognitive_signals_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."cognitive_signals_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."face_signals" TO "anon";
GRANT ALL ON TABLE "public"."face_signals" TO "authenticated";
GRANT ALL ON TABLE "public"."face_signals" TO "service_role";



GRANT ALL ON SEQUENCE "public"."face_signals_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."face_signals_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."face_signals_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."math_topics" TO "anon";
GRANT ALL ON TABLE "public"."math_topics" TO "authenticated";
GRANT ALL ON TABLE "public"."math_topics" TO "service_role";



GRANT ALL ON SEQUENCE "public"."math_topics_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."math_topics_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."math_topics_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."parent_child_links" TO "anon";
GRANT ALL ON TABLE "public"."parent_child_links" TO "authenticated";
GRANT ALL ON TABLE "public"."parent_child_links" TO "service_role";



GRANT ALL ON TABLE "public"."profiles" TO "anon";
GRANT ALL ON TABLE "public"."profiles" TO "authenticated";
GRANT ALL ON TABLE "public"."profiles" TO "service_role";



GRANT ALL ON TABLE "public"."questions" TO "anon";
GRANT ALL ON TABLE "public"."questions" TO "authenticated";
GRANT ALL ON TABLE "public"."questions" TO "service_role";



GRANT ALL ON TABLE "public"."session_answers" TO "anon";
GRANT ALL ON TABLE "public"."session_answers" TO "authenticated";
GRANT ALL ON TABLE "public"."session_answers" TO "service_role";



GRANT ALL ON TABLE "public"."sessions" TO "anon";
GRANT ALL ON TABLE "public"."sessions" TO "authenticated";
GRANT ALL ON TABLE "public"."sessions" TO "service_role";



GRANT ALL ON TABLE "public"."user_math_performance" TO "anon";
GRANT ALL ON TABLE "public"."user_math_performance" TO "authenticated";
GRANT ALL ON TABLE "public"."user_math_performance" TO "service_role";



GRANT ALL ON TABLE "public"."user_stats" TO "anon";
GRANT ALL ON TABLE "public"."user_stats" TO "authenticated";
GRANT ALL ON TABLE "public"."user_stats" TO "service_role";



ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "service_role";







