-- Lesson-plan content for the seven topics that are NOT reachable before
-- grade 6, seeded at `upper` and `advanced` only.
--
-- Run this in the Supabase dashboard SQL editor. Deliberately NOT a
-- migration, for the same reason as lesson_plans_priority_topics.sql: this
-- is reference content the backend never mutates, and a migration would
-- re-apply it over any later dashboard edit on every rebuild.
--
-- Idempotent: re-running updates existing rows rather than erroring, via
-- the (topic_name, grade_band) unique constraint from 20260827010000.
--
-- Scope, and why `early`/`middle` are absent. `LLM_topic_decider._allowed_topics()`
-- keeps all seven of these out of grade 1-5 sessions entirely, and algebra
-- and probability out of everything below grade 6. Their `early`/`middle`
-- rows would be defense-in-depth padding for a gate that already holds, and
-- an unseeded cell fails open to the difficulty/grade heuristics -- which is
-- the better default for content nobody is meant to see. Leaving them
-- unseeded is the decision, not an omission.
--
-- IMPORTANT -- these objectives are bounded by what each generator can
-- actually emit, not by what the grade band could cover in a classroom. A
-- lesson plan describing a question shape the JSON contract cannot produce
-- would push the model toward output the solver then mis-scores, which is
-- worse than no grounding at all. The binding limits, read off the code:
--
--   * algebra -- LLM_algebra_generation solves with sympy and takes
--     `solution[0]`, and splits `question_text` on a single "=". So: ONE
--     linear equation, ONE unknown, exactly one solution. A quadratic would
--     present one root as the answer and mark the other correct choice
--     wrong. No systems, no inequalities, no quadratics at any band.
--   * probability -- only three scenarios exist (probability_of,
--     not_probability_of, dice). Single-event probability over a stated
--     sample space, plus the complement. No compound or conditional
--     probability, no permutations or combinations.
--   * rationals -- values are proper fractions in "a/b" form; the prompt
--     explicitly forbids mixed numbers. Arithmetic on fractions only, not
--     algebraic rational expressions.
--   * mean / median / mode -- the contract is a listed dataset and the one
--     statistic. No box plots, no mean absolute deviation, no comparing two
--     distributions -- none of those can be expressed in the JSON.
--   * angle_relationships -- five scenarios: complementary, supplementary,
--     linear pair, triangle sum, and solve-for-x complementary. No circle
--     theorems, no trigonometry, no transversal diagrams (a question about
--     a diagram has no diagram to show).
--
-- So `advanced` here means harder numbers and an extra reasoning step
-- inside the same question shape -- not a different kind of mathematics.
-- That is a real limitation of the generators, and the honest place to
-- record it is here, next to the text it constrains.
--
-- Objectives are Common Core-based (thecorestandards.org progressions).
-- Kept well under lesson_plan_context._MAX_CONTEXT_CHARS (2000), which
-- truncates silently.

INSERT INTO "public"."lesson_plans" ("topic_name", "grade_band", "objectives", "notes")
VALUES
  -- algebra --------------------------------------------------------------
  ('algebra', 'upper',
   'Solve multi-step linear equations in one variable: apply the distributive property, combine like terms on each side, and collect the variable onto one side when it appears on both (3x + 5 = x - 7). Students reason about inverse operations as a sequence that preserves equality, and check a solution by substitution. Negative coefficients and negative solutions are expected at this band.',
   'Exactly one linear equation with one unknown and one solution -- the solver takes solution[0] and splits on a single "=". No quadratics, no systems, no inequalities.'),

  ('algebra', 'advanced',
   'Solve linear equations requiring the distributive property and the collection of like terms from both sides, such as 5(x - 3) + 2x = 4(x + 1). Students reason fluently about equivalent equations and recognise when a rearrangement leaves the solution unchanged. Coefficients and constants are integers; the solution itself may be a fraction.',
   'Still one linear equation with a single unique solution -- the constraint is the solver, not the grade. Integer coefficients deliberately: an earlier version invited "fractional and decimal constants" and generation failed outright with "Cannot convert expression to float" (2026-08-18). Raise difficulty through structure, not through decimal literals.'),

  -- angle_relationships --------------------------------------------------
  ('angle_relationships', 'upper',
   'Use complementary, supplementary, vertical, and linear-pair relationships to find an unknown angle, and apply the triangle angle sum to find a third angle from two known ones. Students set up and solve a one-variable equation for an unknown angle expressed algebraically, and reason about which relationship a described configuration implies.',
   'Angle arithmetic stated in words only -- there is no diagram to accompany a question, so the configuration must be fully described in the text.'),

  ('angle_relationships', 'advanced',
   'Solve for an unknown in a pair of complementary angles where BOTH angles are given as algebraic expressions, such as (2x + 10) degrees and (3x - 5) degrees, using the fact that the pair sums to 90 degrees. Students set up the equation from the stated relationship rather than from a diagram, and choose coefficients so the solution is a whole number.',
   'Exactly two angles in one stated relationship -- complementary, supplementary, linear pair or triangle sum. Measured 2026-08-18: an earlier version invited "the unknown in more than one expression" and produced a quadrilateral with two pairs of complementary opposite angles, which is not a coherent configuration. Do not describe figures beyond the two-angle relationships the five scenarios cover. KNOWN AND NOT FIXED BY THIS TEXT: the whole-number-solution request above is not reliably followed -- (5x+15)+(3x-20)=90 gives 11.875, reported as 11.88. Nothing in scenario 5 constrains the coefficients, so this is a generator limitation, not a seeding one; a real fix belongs in LLM_angle_relationship_generation, not here.'),

  -- probability ----------------------------------------------------------
  ('probability', 'upper',
   'Find the probability of drawing ONE named category from a clearly stated collection, expressing the result as a fraction in lowest terms. Students find the probability of the complement of that single category ("not red") and reason that an event and its complement sum to 1. For a die, the event may be a condition on the face value such as "greater than 4".',
   'The favourable outcome must be exactly ONE named category, or its complement, or a condition on a single die. Never ask for two categories combined ("blue or yellow") -- that is a compound event, the generator has no scenario for it, and the solver scores it wrongly rather than refusing. Measured 2026-08-18: an earlier version of this text produced "either blue or yellow" and the answer came back as 1 against a true 10/21.'),

  ('probability', 'advanced',
   'Find the probability of ONE named category, or of its complement, over a larger collection where every category is listed as a whole-number count of items, so the total must be assembled by adding several counts before the single favourable count is divided by it. Students express the result as a fraction in lowest terms.',
   'Difficulty comes from the size of the sample space and the arithmetic of totalling it -- NOT from combining categories, and NOT from percentages. Every category must be an explicit whole-number count of items: measured 2026-08-18, "80% being Arabica" of 15 brands scored as 1. Percentages, proportions and ratios have no counts for the solver to divide. The one-category rule from the upper band applies here too and is the thing most likely to be eroded by a well-meaning edit.'),

  -- rationals ------------------------------------------------------------
  ('rationals', 'upper',
   'Add, subtract, multiply, and divide rational numbers, including negative fractions, finding common denominators where the operation requires it. Students reason about why dividing by a fraction is multiplying by its reciprocal, and about how sign rules apply to each operation.',
   'Values are proper fractions in "a/b" form; the prompt forbids mixed numbers. Keep denominators to values a student can find a common multiple for mentally.'),

  ('rationals', 'advanced',
   'Perform multi-step arithmetic on rational numbers, applying order of operations across several fraction operations and simplifying the result fully. Students reason about the most efficient order in which to combine terms rather than working strictly left to right.',
   'Fraction arithmetic only -- not algebraic rational expressions, which the "a/b" contract cannot represent.'),

  -- mean -----------------------------------------------------------------
  ('mean', 'upper',
   'Find the mean of a dataset and interpret it as a balance point -- the value each item would take if the total were shared equally. Students reason about how an extreme value pulls the mean toward it, and work with datasets whose mean is not a whole number.',
   'Datasets may include negative values at this band (temperatures, scores relative to zero). The contract is a listed dataset and one statistic -- no comparison of two distributions.'),

  ('mean', 'advanced',
   'Find the mean of larger datasets including negative and non-integer values, and reason about the relationship between the mean and the sum: knowing the mean and the count determines the total. Students recognise when a mean alone describes a dataset poorly.',
   'Mean absolute deviation and weighted means have no representation in the JSON contract -- keep to the mean of a listed dataset.'),

  -- median ---------------------------------------------------------------
  ('median', 'upper',
   'Find the median of a dataset, ordering the values first and averaging the two middle values when the count is even. Students reason about why the median resists extreme values where the mean does not, and about what that implies for describing a skewed dataset.',
   'Ordering the data first is the step students skip; a dataset presented out of order targets it directly. Negative values are appropriate at this band.'),

  ('median', 'advanced',
   'Find the median of larger datasets presented out of order, including negative and decimal values, and reason about the median as the 50th percentile. Students consider how the median and mean together indicate the direction of skew.',
   'Box plots and five-number summaries cannot be expressed in the JSON contract -- the question is the median of a listed dataset.'),

  -- mode -----------------------------------------------------------------
  ('mode', 'upper',
   'Find the mode of a dataset in which at least one value genuinely repeats more often than the others, including datasets where two values tie for most frequent. Students reason about when the mode is the most informative measure of centre -- categorical or repeated-value data -- and when it is misleading.',
   'Every dataset MUST contain a repeated value. A dataset with no mode cannot be scored: the solver returns a garbage answer rather than refusing. An earlier version of this text said students should "recognise that a dataset may have no mode at all", which is true of the subject and wrong as an instruction to a generator. Bimodal datasets are reserved for hard difficulty by COMPLEXITY_BY_GRADE.'),

  ('mode', 'advanced',
   'Find the mode of larger datasets where the most frequent value leads by only one occurrence, so the answer requires careful counting rather than visual inspection. Students reason about modality as a property of a distribution and about what a bimodal result suggests about the underlying data.',
   'The dataset MUST still contain a repeated value -- see the upper-band note. Measured 2026-08-18: an earlier version produced nine distinct rainfall readings with no repeat at all, and the answer came back as 0.')

ON CONFLICT ("topic_name", "grade_band") DO UPDATE
  SET "objectives" = EXCLUDED."objectives",
      "notes"      = EXCLUDED."notes",
      "updated_at" = now();
