-- Lesson plans for ordering, geometry and expressions across all four bands.
-- Run in the dashboard SQL editor, not as a migration; idempotent.
-- `middle` spans grades 4-6: write it safe for a 4th grader and state any
-- 6th-grade-edge skill in `notes`. Common Core-based; keep under the
-- 2000-char _MAX_CONTEXT_CHARS, which truncates silently.

INSERT INTO "public"."lesson_plans" ("topic_name", "grade_band", "objectives", "notes")
VALUES
  -- ordering -------------------------------------------------------------
  ('ordering', 'early',
   'Compare and order whole numbers within 1000 using place value: hundreds before tens before ones. Students reason about which digit position decides the comparison, and use the language "greater than", "less than", and "equal to". Simple unit fractions (halves, thirds, fourths) may be placed on a number line, but only as positions between whole numbers, not as values to compute with.',
   'No decimals and no negative numbers -- both are later concepts. Fractions appear as points on a number line only, and only for the 3rd-grade edge of this band.'),

  ('ordering', 'middle',
   'Compare and order multi-digit whole numbers and decimals to the thousandths, using place value and the number line. Students compare decimals by aligning place values rather than by digit count, a common misconception ("0.45 is bigger than 0.5"). Ordering may mix whole numbers, decimals, and simple fractions in one set.',
   'Negative numbers belong to the 6th-grade edge of this band only -- a 4th grader has not met them. Prefer positive values unless the question is clearly aimed at the top of the band.'),

  ('ordering', 'upper',
   'Order rational numbers including negatives, decimals, and fractions on a number line, and reason about absolute value versus signed magnitude (-8 is less than -3, though 8 is greater than 3). Students distinguish rational from irrational numbers and place square roots approximately between consecutive integers.',
   'Absolute-value-versus-magnitude is the reliable source of error here and is worth targeting directly.'),

  ('ordering', 'advanced',
   'Order any real numbers, including irrationals, radicals, and values in exponential or scientific notation, reasoning about relative magnitude without full evaluation. Students compare quantities across representations -- a fraction against a decimal against a root -- by converting only as far as the comparison requires.',
   NULL),

  -- geometry -------------------------------------------------------------
  ('geometry', 'early',
   'Identify and describe two-dimensional shapes by their attributes: number of sides, number of corners, and whether sides are equal. Partition rectangles and circles into halves, thirds, and fourths, and describe the parts using those words. Find the area and perimeter of a rectangle by counting unit squares or adding side lengths, not by applying a formula.',
   'Deliberately excludes circles as a measurement subject, volume, and the Pythagorean theorem -- all assume formulas this band has not reached. LLM_geometry_generation.EARLY_BAND_SCENARIOS enforces the same exclusion structurally.'),

  ('geometry', 'middle',
   'Apply area and perimeter formulas for rectangles and triangles, and find the volume of a rectangular prism by counting unit cubes and by multiplying length, width, and height. Students classify shapes by properties of their sides and angles, and plot points in the first quadrant of the coordinate plane.',
   'Coordinate geometry beyond the first quadrant, and the area of a circle, sit at the 6th-grade edge of this band. A 4th grader works with rectangles and triangles only.'),

  ('geometry', 'upper',
   'Find the area and circumference of circles, and apply the Pythagorean theorem to find an unknown side of a right triangle and distances in the coordinate plane. Students reason about angle relationships formed by parallel lines cut by a transversal, and about the angles of a triangle summing to 180 degrees.',
   NULL),

  ('geometry', 'advanced',
   'Find the surface area and volume of composite and curved solids -- cylinders, cones, spheres, and pyramids -- and apply triangle and circle theorems to solve for unknown measures. Students work with similarity and congruence criteria rather than direct measurement.',
   NULL),

  -- expressions ----------------------------------------------------------
  ('expressions', 'early',
   'Evaluate numerical expressions using addition and subtraction within 100, working left to right. Students reason about missing numbers as fact families ("7 plus what makes 15?") rather than as equations with a variable. Multiplication appears only as repeated addition of small facts at the top of this band.',
   'No algebraic notation of any kind -- no x, no letters standing for numbers, no parentheses. Missing-number problems are stated in words, not as symbolic equations. LLM_expressions_generation._pick_scenario withholds the "simplify" scenario from this band structurally.'),

  ('expressions', 'middle',
   'Evaluate multi-step numerical expressions using the order of operations, including parentheses, with whole numbers. Students interpret an expression as a sequence of operations without evaluating it ("three times as much as the sum of 8 and 5"), and reason about how grouping symbols change the result.',
   'Variables and combining like terms (2x + 3x) are a 6th-grade skill and are NOT appropriate for the 4th-5th majority of this band. The scenario gate already withholds "simplify" from middle band entirely -- do not reintroduce variable notation through this text.'),

  ('expressions', 'upper',
   'Combine like terms and apply the distributive property to generate equivalent linear expressions. Students expand a(b + c), factor out a common term, and recognise when two differently-written expressions are equivalent by substitution or by transformation.',
   NULL),

  ('expressions', 'advanced',
   'Manipulate polynomial and rational expressions: expand products of binomials, factor quadratics, and simplify expressions with exponents including negative and fractional powers. Students choose a form of an expression that reveals a property of interest.',
   NULL)

ON CONFLICT ("topic_name", "grade_band") DO UPDATE
SET "objectives" = EXCLUDED."objectives",
    "notes"      = EXCLUDED."notes",
    "updated_at" = now();
