-- Lesson-plan content for the four topics added for grades 1-3.
--
-- Run this in the Supabase dashboard SQL editor. Like its two siblings it is
-- deliberately NOT a migration: `lesson_plans` is reference content the
-- backend never mutates, and a migration would re-apply this text over any
-- later dashboard edit every time the database is rebuilt.
--
-- Idempotent: re-running updates existing rows via the (topic_name,
-- grade_band) unique constraint from 20260827010000.
--
-- FIVE ROWS, NOT SIXTEEN. All four topics carry a `TOPIC_MAX_GRADE`, so most
-- of the band grid is unreachable: `missing_number`, `graphs` and
-- `shape_fractions` stop at grade 3 and exist only in `early`; `patterns`
-- stops at grade 5, so it has `early` and part of `middle`. An unseeded cell
-- fails open to the difficulty/grade heuristics, which is the right outcome
-- for a cell no student can land in -- writing text for it would be padding
-- that reads as coverage.
--
-- `patterns`/`middle` is the one to read carefully. `_grade_band()` buckets
-- 4, 5 AND 6 together, but this topic is capped at 5, so that row is written
-- for grades 4-5 only. Do not raise it to the band ceiling the way the other
-- seed files' `middle` rows are written.
--
-- BOTH COLUMNS ARE PROMPT TEXT. `lesson_plan_context._lookup` appends `notes`
-- to `objectives` and sends the pair, so `notes` is an instruction to the
-- model, not a margin note to the next editor. Keep it to constraints on the
-- question. Anything addressed to a human -- why a limit exists, what would
-- have to change to lift it, which module enforces what -- belongs in these
-- `--` comments, which are not sent anywhere.
--
-- THE CONSTRAINT THAT MATTERS MORE THAN THE STANDARDS. Whatever this text
-- invites, the model attempts, and the solver then scores it -- correctly or
-- not. Three wrong answers in production came from seed text alone (see
-- CLAUDE.md). All four of these generators refuse rather than guess, so an
-- out-of-scope objective costs retries and a narrower bank rather than a
-- wrong answer -- but retries are not free either: see the note on
-- `shape_fractions` below, where a question shape the model kept reaching for
-- exhausted all three attempts and failed the request outright.
--
-- Objectives are Common Core-based (thecorestandards.org progressions) and
-- written here rather than sourced from worksheet vendors, whose content is
-- copyrighted and whose grade taxonomy does not line up with these topics.
--
-- Kept well under lesson_plan_context._MAX_CONTEXT_CHARS (2000), which
-- truncates silently -- and that budget covers objectives AND notes together.

INSERT INTO "public"."lesson_plans" ("topic_name", "grade_band", "objectives", "notes")
VALUES
  -- missing_number -------------------------------------------------------
  -- Emits exactly one equation: number, operator, number, "=", number, with
  -- one of the three numbers replaced by "?". Word problems are impossible
  -- rather than discouraged -- any digit outside the equation is refused --
  -- so there is no point asking for a story context.
  --
  -- Worth knowing before editing: the early band's tiers ASK for addition and
  -- subtraction only, so 3.OA.4 unknown-factor questions are not offered at
  -- this band. They are not prevented either -- `OPERATORS` accepts "*" at
  -- every grade, and solve_missing scores a multiplication happily -- so a
  -- grade-3 reply that reaches for one is served rather than retried. That is
  -- the right outcome by the standard, since 3.OA.4 is grade-3 content, and
  -- it is why this is a note rather than a defect. Offering it deliberately
  -- is a change to COMPLEXITY_BY_GRADE in LLM_missing_number_generation.py,
  -- not to the text below.
  --
  -- The sharper form of the same gap is at grades 1-2, where GRADE_OVERRIDES
  -- says "Do NOT use multiplication" and nothing enforces it: a "*" there
  -- would be scored and served, and would be genuinely above grade. Unlike
  -- the parenthesis leak in `expressions`, which was found by reading real
  -- output and is now checked in code, this one has not been observed --
  -- so it is recorded here rather than acted on.
  ('missing_number', 'early',
   'Find the unknown number that makes an equation true, where the equation has three numbers and one operation. The unknown may sit in any of the three positions, including the first, so that students read an equation as a statement of balance rather than as a left-to-right instruction to compute. Grade 1 works with addition and subtraction within 20 (1.OA.8), grade 2 within 100 (2.OA.1, 2.NBT.5), and grade 3 extends to an unknown factor in a multiplication (3.OA.4). Students find the unknown by using the inverse operation, and check the answer by putting it back into the equation.',
   'One operation per question and exactly one unknown, written "?" and never a letter. No division. No negative results, fractions or decimals. Do not set the question in a story or mention any number that is not part of the equation.'),

  -- patterns -------------------------------------------------------------
  -- Emits one ascending sequence of 4 to 8 whole numbers with a constant
  -- whole-number step, one term replaced by "?". Repeating shape or colour
  -- patterns are out because there is no figure for them and no number to
  -- score; doubling patterns are out because solve_pattern derives a single
  -- constant step and refuses anything else.
  ('patterns', 'early',
   'Complete a counting sequence by working out the constant amount it grows by. Grade 1 counts on by ones and twos within 20 (1.NBT.1); grade 2 skip counts by twos, fives and tens (2.NBT.2); grade 3 extends to larger steps and longer sequences. The blank may fall anywhere in the sequence, not only at the end, so a student may have to work backwards from a later term as well as forwards. Students describe the rule in words ("it goes up by five each time") before using it, and check the rule against every term rather than only the first pair.',
   'The sequence must go up by the same amount every time, and that amount must be a whole number of 1 or more. Do not count backwards. Do not use doubling or any pattern that multiplies. Do not use shapes, colours or a repeating pattern. No fractions or decimals. Exactly one blank, written "?", and no other number anywhere in the wording.'),

  -- Grades 4-5 only. This topic is capped at grade 5, so unlike other
  -- middle-band rows it must NOT be pitched at the 6th-grade edge of the
  -- band. 5.OA.3 -- two sequences compared as ordered pairs -- is not
  -- reachable: the format holds one sequence per question.
  ('patterns', 'middle',
   'Identify the constant step of a number sequence and use it to recover a missing term, including when the blank falls between two known terms so the step has to be found from either side of it. Students generate and describe a rule for a sequence (4.OA.5), and work with steps and values large enough that counting on one term at a time stops being practical, so the rule has to be applied as arithmetic rather than as repeated counting.',
   'One sequence per question, going up by the same whole-number amount every time. Do not compare two sequences and do not use ordered pairs. Do not count backwards, and do not use doubling or any pattern that multiplies. Exactly one blank, written "?".'),

  -- graphs ---------------------------------------------------------------
  -- Emits 2 to 5 categories with counts 1-20 and exactly two question
  -- shapes: the total, and the difference between two named bars. Asking how
  -- many FEWER of the smaller bar is refused rather than answered with an
  -- absolute value, because the question as posed has no answer.
  ('graphs', 'early',
   'Read a bar graph and answer a question by counting the bars. Two readings: how many there are altogether across the categories, and how many more one category has than another. The comparison is the harder of the two, because it is a reading and a subtraction rather than a reading and an addition. Grade 1 works with up to three categories (1.MD.4), grade 2 with four (2.MD.10), and grade 3 continues with scaled reading questions (3.MD.3). The graph is ruled at every unit so that a young student can count a bar rather than estimate its height.',
   'Ask either for the total across all the categories or for how many more the larger of two named categories has. Do not ask how many fewer. Do not ask about a category that is not in the graph. The counts belong in the graph only -- do not write any number into the question. Do not use a picture graph where one symbol stands for more than one, and do not use a line plot or a tally chart.'),

  -- shape_fractions ------------------------------------------------------
  -- Emits a rectangle in 2-8 equal parts with some shaded, and REFUSES any
  -- shading that is not already in lowest terms: two parts of four is a fine
  -- picture and an ambiguous question, since 2/4 and 1/2 are both correct
  -- readings and a student giving the other would be marked wrong for a
  -- right answer.
  --
  -- That refusal is what makes the grade-1 enumeration in the objectives
  -- load-bearing rather than decorative. Grade 1 is held to 2 or 4 parts
  -- (1.G.3), which leaves exactly three legal pictures -- 1/2, 1/4, 3/4 --
  -- and half-of-four is the one a model reaches for first. Measured on
  -- llama3.1:8b before the fractions were named: 2/4 on all three attempts,
  -- so the request failed outright rather than degrading. Naming the three
  -- is cheaper than any of the alternatives and stays true to 1.G.3.
  ('shape_fractions', 'early',
   'Name the fraction of a shape that is shaded, where the shape has been divided into equal parts. Grade 1 covers halves and fourths (1.G.3), so the shaded amount there is one half, one fourth, or three fourths and nothing else. Grade 2 adds thirds (2.G.3), and grade 3 reads a/b as a copies of the unit fraction 1/b (3.NF.1). The whole is always drawn the same size and the parts divide it, so more parts means smaller parts -- which is the misconception these standards exist to address. Students count the shaded parts and the total parts, and say the fraction in that order.',
   'The shaded parts and the total parts must share no common factor, so two parts of four, two of six and three of six are all disallowed; use one of two, one of four, three of four, one of three, two of three, three of eight and so on. Ask only what fraction is shaded. Do not ask students to compare, add, order or convert fractions, and do not ask for an equivalent fraction. No fractions of one or more, no mixed numbers, and no fractions of a set such as three of the eight marbles. Do not write any number into the question, and do not call the shape a circle, a pie or a pizza.')

ON CONFLICT ("topic_name", "grade_band") DO UPDATE
SET "objectives" = EXCLUDED."objectives",
    "notes"      = EXCLUDED."notes",
    "updated_at" = now();
