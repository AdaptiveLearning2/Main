-- Lesson plans for the four young topics: five rows, since TOPIC_MAX_GRADE
-- leaves the rest unreachable. Run in the dashboard SQL editor; idempotent.
-- Both columns are prompt text; only invite what the solver can score.
-- Original Common Core text, never copyrighted vendor worksheets. The 2000-char
-- _MAX_CONTEXT_CHARS covers objectives and notes together.

INSERT INTO "public"."lesson_plans" ("topic_name", "grade_band", "objectives", "notes")
VALUES
  -- missing_number -------------------------------------------------------
  -- One equation, one "?"; any digit outside it is refused, so no stories.
  -- "*" is allowed at grade 3 (3.OA.4) and blocked at 1-2 by _forbidden_operator.
  ('missing_number', 'early',
   'Find the unknown number that makes an equation true, where the equation has three numbers and one operation. The unknown may sit in any of the three positions, including the first, so that students read an equation as a statement of balance rather than as a left-to-right instruction to compute. Grade 1 works with addition and subtraction within 20 (1.OA.8), grade 2 within 100 (2.OA.1, 2.NBT.5), and grade 3 extends to an unknown factor in a multiplication (3.OA.4). Students find the unknown by using the inverse operation, and check the answer by putting it back into the equation.',
   'One operation per question and exactly one unknown, written "?" and never a letter. No division. No negative results, fractions or decimals. Do not set the question in a story or mention any number that is not part of the equation.'),

  -- patterns -------------------------------------------------------------
  -- One ascending sequence, 4-8 whole numbers, constant step, one "?";
  -- solve_pattern refuses doubling and there is no figure for shape patterns.
  ('patterns', 'early',
   'Complete a counting sequence by working out the constant amount it grows by. Grade 1 counts on by ones and twos within 20 (1.NBT.1); grade 2 skip counts by twos, fives and tens (2.NBT.2); grade 3 extends to larger steps and longer sequences. The blank may fall anywhere in the sequence, not only at the end, so a student may have to work backwards from a later term as well as forwards. Students describe the rule in words ("it goes up by five each time") before using it, and check the rule against every term rather than only the first pair.',
   'The sequence must go up by the same amount every time, and that amount must be a whole number of 1 or more. Do not count backwards. Do not use doubling or any pattern that multiplies. Do not use shapes, colours or a repeating pattern. No fractions or decimals. Exactly one blank, written "?", and no other number anywhere in the wording.'),

  -- Grades 4-5 only (capped at 5): not pitched at the band's 6th-grade edge.
  -- 5.OA.3 is unreachable: one sequence per question.
  ('patterns', 'middle',
   'Identify the constant step of a number sequence and use it to recover a missing term, including when the blank falls between two known terms so the step has to be found from either side of it. Students generate and describe a rule for a sequence (4.OA.5), and work with steps and values large enough that counting on one term at a time stops being practical, so the rule has to be applied as arithmetic rather than as repeated counting.',
   'One sequence per question, going up by the same whole-number amount every time. Do not compare two sequences and do not use ordered pairs. Do not count backwards, and do not use doubling or any pattern that multiplies. Exactly one blank, written "?".'),

  -- graphs ---------------------------------------------------------------
  -- 2-5 categories, counts 1-20; the total or "how many more". "How many
  -- fewer" is refused.
  ('graphs', 'early',
   'Read a bar graph and answer a question by counting the bars. Two readings: how many there are altogether across the categories, and how many more one category has than another. The comparison is the harder of the two, because it is a reading and a subtraction rather than a reading and an addition. Grade 1 works with up to three categories (1.MD.4), grade 2 with four (2.MD.10), and grade 3 continues with scaled reading questions (3.MD.3). The graph is ruled at every unit so that a young student can count a bar rather than estimate its height.',
   'Ask either for the total across all the categories or for how many more the larger of two named categories has. Do not ask how many fewer. Do not ask about a category that is not in the graph. The counts belong in the graph only -- do not write any number into the question. Do not use a picture graph where one symbol stands for more than one, and do not use a line plot or a tally chart.'),

  -- shape_fractions ------------------------------------------------------
  -- Non-lowest-terms shading is refused, so grade 1 has only 1/2, 1/4, 3/4;
  -- naming them in the objectives is load-bearing (the model reaches for 2/4).
  ('shape_fractions', 'early',
   'Name the fraction of a shape that is shaded, where the shape has been divided into equal parts. Grade 1 covers halves and fourths (1.G.3), so the shaded amount there is one half, one fourth, or three fourths and nothing else. Grade 2 adds thirds (2.G.3), and grade 3 reads a/b as a copies of the unit fraction 1/b (3.NF.1). The whole is always drawn the same size and the parts divide it, so more parts means smaller parts -- which is the misconception these standards exist to address. Students count the shaded parts and the total parts, and say the fraction in that order.',
   'The shaded parts and the total parts must share no common factor, so two parts of four, two of six and three of six are all disallowed; use one of two, one of four, three of four, one of three, two of three, three of eight and so on. Ask only what fraction is shaded. Do not ask students to compare, add, order or convert fractions, and do not ask for an equivalent fraction. No fractions of one or more, no mixed numbers, and no fractions of a set such as three of the eight marbles. Do not write any number into the question, and do not call the shape a circle, a pie or a pizza.')

ON CONFLICT ("topic_name", "grade_band") DO UPDATE
SET "objectives" = EXCLUDED."objectives",
    "notes"      = EXCLUDED."notes",
    "updated_at" = now();
