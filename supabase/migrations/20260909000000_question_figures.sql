-- A question may carry a figure: a specification the browser draws, not markup.
--
-- Grades 1-3 mathematics is largely visual, and the standards this system could
-- not ask were mostly the visual ones. `rectangle_area_by_counting` (2.G.2) was
-- being asked in words -- "a rectangle split into 3 rows of 4 same-size
-- squares" -- which is a description of a picture rather than the picture.
--
-- A SPEC, NOT AN SVG, and for two reasons that both matter.
--
-- The picture and the sentence a screen reader is given are derived from the
-- same object, so they cannot disagree -- the reason `AccessibleChart` takes
-- one `columns` spec for its chart and its table, after two literals drifted
-- inside a single PR. Stored markup has nothing to describe itself with.
--
-- And a renderer fixed later applies to every question already in the bank. An
-- SVG string bakes today's renderer into rows that outlive it.
--
-- NULLABLE WITH NO DEFAULT, which is the same four-state rule as
-- `sessions.chart_paths`: a spec, SQL NULL for a question that has no figure,
-- and nothing in between. `'{}'::jsonb` as a default would claim every question
-- ever generated was considered for a figure and found to need none, which is
-- absence dressed as data.
--
-- No grant or policy changes: this is a column on `questions`, which already
-- has a `USING (true)` public-read policy and is the one table besides
-- `math_topics` that `anon` may still SELECT. A figure is bank content and
-- identifies no student, exactly like the question text beside it.

ALTER TABLE "public"."questions"
    ADD COLUMN IF NOT EXISTS "figure" "jsonb";

COMMENT ON COLUMN "public"."questions"."figure" IS
    'Figure specification the client renders, derived by question_figures.py '
    'from the same variables the solver reads. NULL means no figure.';
