#Three options
#Using this instead of creating w/ LLM for increased consistency and speed. 
#1. Algebra, angles, geometry, mean, median, probability - take solution, add/subtract random number. 
#2. Expressions - random term w/ 'x' as variable. 
#3. Rationals - need random numerator and denominator.
#Ordering already has solution generation, may move here. 

import random
import sympy as sp 
from sympy import symbols, Add, Mul
from sympy.parsing.sympy_parser import (
    parse_expr,
    standard_transformations,
    implicit_multiplication_application
) #treat 2x as 2*x for sympy parsing
transformations = (standard_transformations + (implicit_multiplication_application,))


#POSSIBLY - for method 1, find how many decimal places answer has and round to match. 

#method 1
def generate_general_incorrect_answers(answer):
    generated_answers = []

    # Always normalize to float for math
    answer = float(sp.sympify(answer))    
    while len(generated_answers) < 3:
        operation = random.choice(["+", "-", "*"])
        if operation == "+":
            offset = random.randint(1, 25)
            incorrect_answer = answer + offset
        elif operation == "-":
            offset = random.randint(1, 25)
            # prevent negative results
            incorrect_answer = answer - offset
            if incorrect_answer < 0:
                incorrect_answer = random.randint(1, 5)
        elif operation == "*":
            factor = random.randint(2, 5)
            incorrect_answer = answer * factor
        # round safely
        incorrect_answer = round(float(incorrect_answer), 2)

        # normalize formatting (important for frontend equality checks)
        formatted = f"{incorrect_answer:.2f}".rstrip('0').rstrip('.')

        # compare numerically, store as string
        if incorrect_answer != answer and formatted not in generated_answers:
            generated_answers.append(formatted)

    return generated_answers


#maybe take numerator/denomiator as input. 
def generate_incorrect_rational(answer): 
    generated_answers = []

    answer = sp.sympify(answer)

    while len(generated_answers) < 3:
        num = random.randint(1, 20)
        denom = random.randint(1, 20)
        
        #avoid "1" answers, some more randomness. 
        if num == denom:
            if num <= 8:
                num += random.randint(1, 5)
            elif num >= 15:
                num -= random.randint(1, 5)
            else:
                num += random.randint(-3, 3)

        incorrect_answer = sp.Rational(num, denom)
        sp.sympify(incorrect_answer) #ensure answer is in simplest form

        if incorrect_answer != answer and incorrect_answer not in generated_answers: #ensure we dont add the correct answer or duplicates.
            generated_answers.append(str(incorrect_answer))
        else:
            continue 

    return generated_answers


#expressions
def extract_terms(expr):
    """
    Break expression into additive terms.
    Example: 2*x + 3*x → [2*x, 3*x]
    """
    return list(expr.as_ordered_terms())

def wrong_coefficient(expr):
    x = symbols('x')

    if expr.is_Add:
        coeffs = [t.as_coeff_Mul()[0] for t in expr.as_ordered_terms()]
        base = expr.as_ordered_terms()[0].as_coeff_Mul()[1]

        wrong_coeff = sum(coeffs) + random.choice([-1, 1, 2])

        return wrong_coeff * base

    return expr

def sign_error(expr):
    return expr * -1

def generate_symbolic_incorrect_answers(solution_expr, count=3):
    results = set()

    attempts = 0
    while len(results) < count:
        
        
        wrong = wrong_coefficient(solution_expr)

        rand = random.random()
        if rand < 0.5:
            wrong = sign_error(wrong)

        if wrong != solution_expr:
            results.add(str(wrong))

        attempts += 1

    return list(results)