-- Lesson-plan content for `spread`, the third topic added for grades 9-12.
--
-- Run this in the Supabase dashboard SQL editor. Like its siblings it is
-- deliberately NOT a migration: `lesson_plans` is reference content the backend
-- never mutates, and a migration would re-apply this text over any later
-- dashboard edit every time the database is rebuilt.
--
-- ONE ROW. `TOPIC_MIN_GRADE` puts this topic at grade 9, so only `advanced` is
-- reachable; an unseeded cell fails open to the heuristics, which is the right
-- outcome for a cell no student can land in.
--
-- BOTH COLUMNS ARE PROMPT TEXT -- `lesson_plan_context._lookup` appends `notes`
-- to `objectives` and sends the pair. Keep `notes` to constraints on the
-- question; anything addressed to a human belongs in these `--` comments.
--
-- AND THE MODEL DOES NOT CHOOSE THE DATA. `_choose_dataset` builds it from
-- deviation patterns whose variance is a perfect square, the generator renders
-- it into the prompt under DATA, and `shown_matches_scored` requires it back
-- verbatim. So this text must not invite the model to pick values, invent a
-- reading, or change one -- the same mistake the quadratics row had to have
-- rewritten out of it after review.
--
-- The population/sample distinction is the one thing here that is a scoring
-- hazard rather than a preference: sample standard deviation over n-1 is what
-- many high-school courses teach by default, so a question that says only
-- "standard deviation" has two defensible answers and scores one of them. The
-- generator requires the word "population" in the text and refuses without it;
-- this text reinforces it rather than being the only thing that asks.

INSERT INTO "public"."lesson_plans" ("topic_name", "grade_band", "objectives", "notes")
VALUES
  ('spread', 'advanced',
   'Quantify how spread out a data set is, and compare the spread of two sets (S-ID.2). Students work from the definition rather than a calculator button: find the mean, take each value''s deviation from it, square those, average the squares to get the variance, and take the square root for the standard deviation. The square root is the step most often skipped -- a variance is in squared units and is not the spread itself. Comparing two sets is the point of the measure: two data sets can share a mean and differ entirely in how tightly they cluster around it, and the standard deviation is what says so. Students distinguish the population formula, which divides by n, from the sample formula, which divides by n - 1.',
   'Every question is about the population standard deviation, and must say "population standard deviation" in full. Ask either for the value for one data set, or for how much larger one set''s is than another''s. Do not ask for the variance, the interquartile range, the mean absolute deviation, the range, the mean, or the median on its own. Do not ask which set is more consistent without asking by how much. Do not describe the data as a sample, a survey of a larger group, or an estimate of anything. Give the data a short plausible context and use every value exactly as provided, in the order provided.')

ON CONFLICT ("topic_name", "grade_band") DO UPDATE
SET "objectives" = EXCLUDED."objectives",
    "notes"      = EXCLUDED."notes",
    "updated_at" = now();
