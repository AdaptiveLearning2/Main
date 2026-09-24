-- A question may carry a figure: a spec the browser draws, not stored SVG, so
-- the picture and its screen-reader text derive from one object and renderer
-- fixes apply to old rows. No default: NULL means no figure.

ALTER TABLE "public"."questions"
    ADD COLUMN IF NOT EXISTS "figure" "jsonb";

COMMENT ON COLUMN "public"."questions"."figure" IS
    'Figure specification the client renders, derived by question_figures.py '
    'from the same variables the solver reads. NULL means no figure.';
