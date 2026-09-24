-- Lesson plan for `spread` (grade 9+, `advanced` only). Run in the dashboard
-- SQL editor, not as a migration. Both columns are prompt text.
-- The generator supplies the data, so never invite the model to pick or change
-- values. "Population" must be explicit: sample SD is the other defensible
-- answer.

INSERT INTO "public"."lesson_plans" ("topic_name", "grade_band", "objectives", "notes")
VALUES
  ('spread', 'advanced',
   'Quantify how spread out a data set is, and compare the spread of two sets (S-ID.2). Students work from the definition rather than a calculator button: find the mean, take each value''s deviation from it, square those, average the squares to get the variance, and take the square root for the standard deviation. The square root is the step most often skipped -- a variance is in squared units and is not the spread itself. Comparing two sets is the point of the measure: two data sets can share a mean and differ entirely in how tightly they cluster around it, and the standard deviation is what says so. Students distinguish the population formula, which divides by n, from the sample formula, which divides by n - 1.',
   'Every question is about the population standard deviation, and must say "population standard deviation" in full. Ask either for the value for one data set, or for how much larger one set''s is than another''s. Do not ask for the variance, the interquartile range, the mean absolute deviation, the range, the mean, or the median on its own. Do not ask which set is more consistent without asking by how much. Do not describe the data as a sample, a survey of a larger group, or an estimate of anything. Give the data a short plausible context and use every value exactly as provided, in the order provided.')

ON CONFLICT ("topic_name", "grade_band") DO UPDATE
SET "objectives" = EXCLUDED."objectives",
    "notes"      = EXCLUDED."notes",
    "updated_at" = now();
