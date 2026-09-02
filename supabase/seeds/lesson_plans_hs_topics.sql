-- Lesson-plan content for the two topics added for grades 9-12.
--
-- Run this in the Supabase dashboard SQL editor. Like its three siblings it is
-- deliberately NOT a migration: `lesson_plans` is reference content the backend
-- never mutates, and a migration would re-apply this text over any later
-- dashboard edit every time the database is rebuilt.
--
-- Idempotent: re-running updates existing rows via the (topic_name,
-- grade_band) unique constraint from 20260827010000.
--
-- TWO ROWS, NOT EIGHT. Both topics have a `TOPIC_MIN_GRADE` of 9, so `early`,
-- `middle` and `upper` are unreachable -- `_allowed_topics` never offers either
-- topic below grade 9, and `_grade_band` puts grade 9 and up in `advanced`. An
-- unseeded cell fails open to the difficulty/grade heuristics, which is the
-- right outcome for a cell no student can land in; writing text for it would be
-- padding that reads as coverage.
--
-- BOTH COLUMNS ARE PROMPT TEXT. `lesson_plan_context._lookup` appends `notes`
-- to `objectives` and sends the pair, so `notes` is an instruction to the model,
-- not a margin note to the next editor. Keep it to constraints on the question.
-- Anything addressed to a human belongs in these `--` comments, which are sent
-- nowhere.
--
-- THE CONSTRAINT THAT MATTERS MORE THAN THE STANDARDS. Whatever this text
-- invites, the model attempts, and the solver then scores it -- correctly or
-- not. Three wrong answers in production came from seed text alone (see
-- CLAUDE.md). Both of these generators refuse rather than guess, so an
-- out-of-scope objective costs retries and a narrower bank rather than a wrong
-- answer -- but retries are not free, and a cell with few legal questions left
-- is where an exhausted retry budget stops being theoretical.

INSERT INTO "public"."lesson_plans" ("topic_name", "grade_band", "objectives", "notes")
VALUES
  -- quadratics -----------------------------------------------------------
  -- Emits one equation ax^2 + bx + c = 0 and asks for ONE named root. The
  -- solver requires two DIFFERENT roots, both whole numbers -- so a repeated
  -- root, an irrational one and a fractional one are each refused rather than
  -- rounded. Asking students to "find the solutions" (plural) is impossible
  -- here: the answer is one option among four, so the question has to name
  -- which root it wants, and the generator writes that instruction itself.
  --
  -- Deliberately out of reach, and not worth inviting: the quadratic formula
  -- applied to a non-factorable equation (the answer would be irrational),
  -- completing the square as a *stated method* (nothing scores the method,
  -- only the root), complex roots, and anything asking for the vertex, axis of
  -- symmetry or discriminant -- those are different questions with different
  -- answers and this solver returns a root.
  ('quadratics', 'advanced',
   'Solve a quadratic equation that factors over the integers, and identify a particular solution rather than the solution set. Students recognise that a quadratic normally has two solutions, so a question must say which one it wants, and that "the larger solution" is a different answer from "the smaller" -- reading the question is part of the work. Factoring is the expected route (A-REI.4b): a student who can write x^2 - 5x + 6 as (x - 2)(x - 3) reads both solutions off it directly. The quadratic formula reaches the same pair and is worth recognising as the general case, and for these equations it always produces whole numbers because the discriminant is a perfect square. Students check a solution by substituting it back into the original equation.',
   'Every equation must have exactly two different whole-number solutions. Do not write an equation whose solutions are fractions, decimals or square roots, and do not write one with a repeated solution or with no real solution. Ask for one named solution -- the larger or the smaller -- and never for both, for the solution set, or for the number of solutions. Do not ask for the vertex, the axis of symmetry, the discriminant or a graph. Do not ask the student to name or use a particular method.'),

  -- functions -------------------------------------------------------------
  -- Two scenarios: evaluate f at a value, and evaluate a composition f(g(x)).
  -- Both are exact integer arithmetic over polynomials of degree at most 2,
  -- and the result is bounded -- a composition of two quadratics reaches the
  -- millions from coefficients that each look reasonable, and a question whose
  -- answer is 48,271,009 tests calculator ownership.
  --
  -- The grade claim is narrower than the topic name suggests, and the notes
  -- below are what hold it: evaluating a rule at a value is 8.F.2, and grade 8
  -- explicitly does not require function notation. What is high school is the
  -- notation (F-IF.2) and composition (F-BF.1c). So the objectives are written
  -- about notation and composition, not about substitution.
  ('functions', 'advanced',
   'Use function notation fluently: read f(x) as the output of the function f for the input x, evaluate a function at a given value including a negative one, and evaluate a composition f(g(x)) by working from the inside out (F-IF.2, F-BF.1c). Composition is the step with no earlier equivalent -- students who substitute confidently still commonly read f(g(4)) from left to right, so the order in which the two functions apply is the thing to make explicit. Students distinguish f(g(x)) from g(f(x)) and recognise that the two are usually different functions.',
   'Define each function with explicit function notation, written "f(x) = ..." and "g(x) = ...", using polynomials of degree 1 or 2 with whole-number coefficients. Write a squared term as "x^2". Ask for a single numeric value: one evaluation, or one composition in the order f(g(x)). Do not ask for g(f(x)). Do not ask for a formula, a simplified expression, an inverse, a domain or range, a graph, a table, or where the function is increasing. Do not use piecewise, rational, exponential, trigonometric or absolute-value functions. Keep the answer small enough to work out by hand.')

ON CONFLICT ("topic_name", "grade_band") DO UPDATE
SET "objectives" = EXCLUDED."objectives",
    "notes"      = EXCLUDED."notes",
    "updated_at" = now();
