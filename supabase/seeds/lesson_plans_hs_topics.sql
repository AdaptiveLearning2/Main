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
  -- THE MODEL DOES NOT WRITE THE EQUATION FOR THIS TOPIC, and that is the one
  -- thing to know before editing these two fields. `_choose_coefficients`
  -- builds it from two integer roots, `quadratic_prompt` hands it over under
  -- EQUATION and WHICH SOLUTION and forbids changing either, and
  -- `shown_matches_scored` refuses any reply whose text does not carry that
  -- exact equation. The model's whole job is the sentence around it.
  --
  -- So this text must not instruct the model to author an equation or to pick
  -- a root. The first version of this row did both -- "every equation must
  -- have exactly two different whole-number solutions", "ask for one named
  -- solution, the larger or the smaller" -- and carried a worked example
  -- (x^2 - 5x + 6) in its objectives. `append_lesson_context` appends LAST, so
  -- that was the final instruction in the prompt, contradicting the one above
  -- it. A model that obeyed it would write its own equation, fail
  -- `shown_matches_scored` on all three attempts, and the generator would
  -- raise -- turning a working topic into a hard failure the moment this file
  -- was seeded, on a path no test covers because an unseeded cell fails open.
  --
  -- It is written as a description of what the STUDENT does, plus constraints
  -- on the WORDING. Anything that reads as an instruction to produce the
  -- mathematics belongs in COMPLEXITY_BY_GRADE's replacement (`_TIERS` and
  -- `_ROOT_RANGE` in the generator), not here.
  --
  -- Still worth stating in the notes, because they are constraints on the
  -- sentence rather than on the equation: no revealing a solution, no naming
  -- a method, and none of the questions this solver cannot score -- the
  -- solution set, the vertex, the axis of symmetry, the discriminant.
  ('quadratics', 'advanced',
   'Students solve a quadratic that factors over the integers and identify a particular solution rather than the solution set. A quadratic normally has two solutions, so the question says which one it wants, and "the larger solution" is a different answer from "the smaller" -- reading the question is part of the work. Factoring is the expected route (A-REI.4b), and the quadratic formula reaches the same pair as the general case; for these equations it always gives whole numbers, because the discriminant is a perfect square. Students check a solution by substituting it back into the equation.',
   'Word the question so it is unambiguous which of the two solutions is wanted. Do not reveal or hint at either solution. Do not tell the student which method to use. Do not ask for both solutions, the solution set, the number of solutions, the vertex, the axis of symmetry, the discriminant or a graph.'),

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
  --
  -- ONE ROW, TWO SCENARIOS -- which is the constraint that shapes the notes.
  -- `append_lesson_context` is keyed on (topic, grade_band) and knows nothing
  -- about which scenario was selected, while `_EVALUATE_BLOCK` says `Do NOT
  -- include "g"` and `_COMPOSE_BLOCK` requires it. The lesson text is appended
  -- LAST, so anything here naming `g` unconditionally is the final word and
  -- contradicts the block above it. The first version of this row did exactly
  -- that -- 'written "f(x) = ..." and "g(x) = ..."', plus "one evaluation, or
  -- one composition", which also hands the model a choice the scenario had
  -- already made. On the Ollama branch, where no schema is sent, an obeying
  -- reply would show an unused g(x) in a question scored on f alone.
  --
  -- So the notes must be true of BOTH scenarios: name no function the block
  -- did not, and offer no choice between them. `_mentions_second_function` in
  -- the generator is the enforcement, because this is prompt text and prompt
  -- text is asked, not guaranteed.
  ('functions', 'advanced',
   'Use function notation fluently: read f(x) as the output of the function f for the input x, evaluate a function at a given value including a negative one, and evaluate a composition f(g(x)) by working from the inside out (F-IF.2, F-BF.1c). Composition is the step with no earlier equivalent -- students who substitute confidently still commonly read f(g(4)) from left to right, so the order in which the two functions apply is the thing to make explicit. Students distinguish f(g(x)) from g(f(x)) and recognise that the two are usually different functions.',
   'Define every function the question uses with explicit function notation, written "f(x) = ...", using polynomials of degree 1 or 2 with whole-number coefficients, and do not introduce a function the scenario above did not ask for. Write a squared term as "x^2". Ask for a single numeric value, and never for a formula, a simplified expression, an inverse, a domain or range, a graph, a table, or where the function is increasing. Do not use piecewise, rational, exponential, trigonometric or absolute-value functions.')

ON CONFLICT ("topic_name", "grade_band") DO UPDATE
SET "objectives" = EXCLUDED."objectives",
    "notes"      = EXCLUDED."notes",
    "updated_at" = now();
