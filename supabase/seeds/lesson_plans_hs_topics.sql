-- Lesson plans for quadratics and functions (grade 9+, so `advanced` only).
-- Run in the dashboard SQL editor, not as a migration; idempotent.
-- Both columns are prompt text: keep `notes` to constraints on the question;
-- notes for humans go in these comments. Only invite what the solver can score.
-- See docs/question-generation.md.

INSERT INTO "public"."lesson_plans" ("topic_name", "grade_band", "objectives", "notes")
VALUES
  -- quadratics -----------------------------------------------------------
  -- The generator supplies the equation and the root; this text is appended
  -- last, so it must never ask the model to write an equation or pick a root.
  ('quadratics', 'advanced',
   'Students solve a quadratic that factors over the integers and identify a particular solution rather than the solution set. A quadratic normally has two solutions, so the question says which one it wants, and "the larger solution" is a different answer from "the smaller" -- reading the question is part of the work. Factoring is the expected route (A-REI.4b), and the quadratic formula reaches the same pair as the general case; for these equations it always gives whole numbers, because the discriminant is a perfect square. Students check a solution by substituting it back into the equation.',
   'Word the question so it is unambiguous which of the two solutions is wanted. Do not reveal or hint at either solution. Do not tell the student which method to use. Do not ask for both solutions, the solution set, the number of solutions, the vertex, the axis of symmetry, the discriminant or a graph.'),

  -- functions -------------------------------------------------------------
  -- One row serves both scenarios (evaluate, compose): never name `g` or offer
  -- a choice. About notation and composition, since substitution is 8.F.2.
  ('functions', 'advanced',
   'Use function notation fluently: read f(x) as the output of the function f for the input x, evaluate a function at a given value including a negative one, and evaluate a composition f(g(x)) by working from the inside out (F-IF.2, F-BF.1c). Composition is the step with no earlier equivalent -- students who substitute confidently still commonly read f(g(4)) from left to right, so the order in which the two functions apply is the thing to make explicit. Students distinguish f(g(x)) from g(f(x)) and recognise that the two are usually different functions.',
   'Define every function the question uses with explicit function notation, written "f(x) = ...", using polynomials of degree 1 or 2 with whole-number coefficients, and do not introduce a function the scenario above did not ask for. Write a squared term as "x^2". Ask for a single numeric value, and never for a formula, a simplified expression, an inverse, a domain or range, a graph, a table, or where the function is increasing. Do not use piecewise, rational, exponential, trigonometric or absolute-value functions.')

ON CONFLICT ("topic_name", "grade_band") DO UPDATE
SET "objectives" = EXCLUDED."objectives",
    "notes"      = EXCLUDED."notes",
    "updated_at" = now();
