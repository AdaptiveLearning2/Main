# Generates wrong answer options directly instead of via LLM, for speed and consistency.
# Algebra/angles/geometry/mean/median/probability: offset the solution.
# Expressions: perturb the 'x' term. Rationals: random numerator/denominator.

import random
import sympy as sp 
from sympy import symbols, Add, Mul
from sympy.parsing.sympy_parser import (
    parse_expr,
    standard_transformations,
    implicit_multiplication_application
) # treat 2x as 2*x for sympy parsing
transformations = (standard_transformations + (implicit_multiplication_application,))


def generate_general_incorrect_answers(answer):
    generated_answers = []

    answer = float(sp.sympify(answer))
    attempts = 0
    # Bounded for the reason `generate_symbolic_incorrect_answers` documents,
    # though this one has not been seen to hang: its offsets are drawn from a
    # wide enough range that three distinct values are almost always reachable.
    # "Almost always" is the problem -- the failure mode is an infinite loop on
    # the hot path, so it does not get to depend on the arithmetic working out.
    while len(generated_answers) < 3 and attempts < MAX_ATTEMPTS:
        attempts += 1
        operation = random.choice(["+", "-", "*"])
        if operation == "+":
            offset = random.randint(1, 25)
            incorrect_answer = answer + offset
        elif operation == "-":
            offset = random.randint(1, 25)
            incorrect_answer = answer - offset
            if incorrect_answer < 0: # prevent negative results
                incorrect_answer = random.randint(1, 5)
        elif operation == "*":
            factor = random.randint(2, 5)
            incorrect_answer = answer * factor
        incorrect_answer = round(float(incorrect_answer), 2)

        # normalize formatting -- important for frontend equality checks
        formatted = f"{incorrect_answer:.2f}".rstrip('0').rstrip('.')

        if incorrect_answer != answer and formatted not in generated_answers:
            generated_answers.append(formatted)

    offset = 1
    while len(generated_answers) < 3:
        formatted = f"{answer + offset:.2f}".rstrip('0').rstrip('.')
        if formatted not in generated_answers:
            generated_answers.append(formatted)
        offset += 1

    return generated_answers


def generate_incorrect_rational(answer):
    generated_answers = []

    answer = sp.sympify(answer)

    attempts = 0
    # Bounded like the other two. This one draws from 400 pairs, so exhausting
    # the bound means something is wrong rather than unlucky -- but an
    # unbounded loop on a request thread is not a risk worth carrying for a
    # branch that should never be taken.
    while len(generated_answers) < 3 and attempts < MAX_ATTEMPTS:
        attempts += 1
        num = random.randint(1, 20)
        denom = random.randint(1, 20)

        # avoid landing on "1" too often
        if num == denom:
            if num <= 8:
                num += random.randint(1, 5)
            elif num >= 15:
                num -= random.randint(1, 5)
            else:
                num += random.randint(-3, 3)

        incorrect_answer = sp.Rational(num, denom)
        sp.sympify(incorrect_answer) # already in simplest form

        # Compared as a string, because that is what the list holds. The check
        # was `incorrect_answer not in generated_answers` -- a Rational against
        # a list of strings, which is never a match, so duplicates passed it
        # and a student was shown the same wrong option twice: four choices
        # offering three answers.
        formatted = str(incorrect_answer)
        if incorrect_answer != answer and formatted not in generated_answers:
            generated_answers.append(formatted)
        else:
            continue

    offset = 1
    while len(generated_answers) < 3:
        candidate = str(sp.sympify(answer) + offset)
        if candidate not in generated_answers:
            generated_answers.append(candidate)
        offset += 1

    return generated_answers


def extract_terms(expr):
    """
    Break expression into additive terms.
    Example: 2*x + 3*x → [2*x, 3*x]
    """
    return list(expr.as_ordered_terms())

# How many random attempts each generator makes before falling back to
# deterministic variants. The randomised search exists to make distractors look
# natural; it must never be the only thing standing between the caller and an
# answer, because whether it can succeed at all is a property of the solution's
# shape rather than of how long you try.
MAX_ATTEMPTS = 50


def wrong_coefficient(expr):
    if expr.is_Add:
        coeffs = [t.as_coeff_Mul()[0] for t in expr.as_ordered_terms()]
        base = expr.as_ordered_terms()[0].as_coeff_Mul()[1]

        wrong_coeff = sum(coeffs) + random.choice([-1, 1, 2])

        return wrong_coeff * base

    # A single term -- `5*x`, which is what simplifying `2x+3x` produces, and
    # so the commonest answer this is ever asked about. Unhandled, this
    # returned the expression unchanged, and the only other value reachable was
    # `sign_error`'s negation: two distinct results where the caller needs
    # three, so `generate_symbolic_incorrect_answers` looped for ever.
    #
    # That is the 28-minute CPU spin, and it was never intermittent: every
    # `simplify` question with a one-term answer hit it. It looked intermittent
    # because `_pick_scenario` reaches `simplify` about one time in three, and
    # only above the "middle" grade band.
    if expr.is_Mul:
        coeff, base = expr.as_coeff_Mul()
        return (coeff + random.choice([-2, -1, 1, 2])) * base

    return expr

def sign_error(expr):
    return expr * -1

def generate_symbolic_incorrect_answers(solution_expr, count=3):
    """`count` distinct wrong answers.

    The loop was `while len(results) < count` with an `attempts` counter that
    was incremented and never read -- a bound someone meant to add. Whether the
    randomised mutations can reach `count` distinct values depends on the
    solution, not on how long you try: for `5*x` exactly two were reachable, so
    the loop could not terminate. See `wrong_coefficient`.
    """
    results = set()

    attempts = 0
    while len(results) < count and attempts < MAX_ATTEMPTS:
        
        
        wrong = wrong_coefficient(solution_expr)

        rand = random.random()
        if rand < 0.5:
            wrong = sign_error(wrong)

        if wrong != solution_expr:
            results.add(str(wrong))

        attempts += 1

    # Deterministic filler, so a caller always gets a full set of options
    # whatever the solution's shape. Returning short would put a question in
    # front of a student with two choices where the design says four, which is
    # a worse outcome than a slightly duller distractor.
    offset = 1
    while len(results) < count and offset <= count + 2:
        candidate = solution_expr + offset
        if candidate != solution_expr:
            results.add(str(candidate))
        offset += 1

    return list(results)