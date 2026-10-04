# -----------------------------------------------------------------------------
# calc.py
#
# A simple calculator with variables.   This is from O'Reilly's
# "Lex and Yacc", p. 63.
# Extended to support real numbers and scientific notation. # [G]
# Extended to support div (//) and mod (%). # [G]
# Extended to support real() casting. # [G]
# Extended to support floor() casting. # [G]
# Modified to strictly separate stdout results and stderr diagnostics. # [G]
# Modified to strictly abort evaluation on syntax and lexer errors. # [G]
# Modified to enforce strict type-matching on arithmetic operators. # [G]
# Modified to force stdout flushing to guarantee immediate output. # [G]
# Modified to allow reserved keywords to act as variable names. # [G]
# Modified to recover from arithmetic errors (division/mod by zero, overflow in
# floor()/real()) instead of crashing, so that later statements are still
# evaluated. # [C]
# -----------------------------------------------------------------------------

import math  # [G]
import sys  # [G]

tokens = ("NAME", "NUMBER", "FLOORDIV", "REAL", "FLOOR")  # [G]

literals = ["=", "+", "-", "*", "/", "(", ")", "%"]  # [G]

# Tokens


def t_NAME(t):  # [G]
    r"[a-zA-Z_][a-zA-Z0-9_]*"  # [G]
    if t.value == "real":  # [G]
        t.type = "REAL"  # [G]
    elif t.value == "floor":  # [G]
        t.type = "FLOOR"  # [G]
    return t  # [G]


t_FLOORDIV = r"//"  # [G]


def t_NUMBER(t):
    r"\d*\.\d+(?:[eE][-+]?\d+)?|\d+\.\d*(?:[eE][-+]?\d+)?|\d+[eE][-+]?\d+|\d+"  # [G]
    if "." in t.value or "e" in t.value or "E" in t.value:  # [G]
        t.value = float(t.value)  # [G]
    else:  # [G]
        t.value = int(t.value)  # [G]
    return t


t_ignore = " \t"


def t_newline(t):
    r"\n+"
    t.lexer.lineno += t.value.count("\n")


def t_error(t):
    print("Illegal character '%s'" % t.value[0], file=sys.stderr)  # [G]
    t.lexer.skip(1)
    raise SyntaxError  # [G] Abort lexical analysis immediately to prevent cascading parser errors


# Build the lexer
import ply.lex as lex

lexer = lex.lex()

# Parsing rules

precedence = (
    ("left", "+", "-"),
    ("left", "*", "/", "FLOORDIV", "%"),  # [G]
    ("right", "UMINUS"),
)

# dictionary of names
names = {}


def error_value(op, operand):  # [C]
    """Return the zero that `op` applied to `operand`s would have produced.

    An erroneous subexpression evaluates to zero.  Giving that zero the type
    the operator normally yields (real for '/', otherwise the operand type)
    keeps the strict same-type rule meaningful, so one bad subexpression does
    not cascade into spurious type errors in the enclosing expression.
    """  # [C]
    return 0.0 if op == "/" or isinstance(operand, float) else 0  # [C]


def p_statement_assign(p):
    """statement : NAME "=" expression
                 | REAL "=" expression
                 | FLOOR "=" expression"""  # [G]
    names[p[1]] = p[3]


def p_statement_expr(p):
    "statement : expression"
    print(p[1], flush=True)  # [G] Force immediate unbuffered output to stdout


def p_expression_binop(p):
    """expression : expression '+' expression
                  | expression '-' expression
                  | expression '*' expression
                  | expression '/' expression
                  | expression FLOORDIV expression
                  | expression '%' expression"""
    # Note: FLOORDIV and '%' grammar rules were added to the docstring above # [G]

    if type(p[1]) != type(p[3]):  # [G]
        print(
            f"type error: mismatched types {type(p[1]).__name__} and {type(p[3]).__name__} for '{p[2]}'",
            file=sys.stderr,
        )  # [G]
        p[0] = 0  # [G] Return 0 for the erroneous expression
    else:  # [G]
        try:  # [C] Contain arithmetic faults so that evaluation can continue
            if p[2] == "+":  # [G]
                p[0] = p[1] + p[3]  # [G]
            elif p[2] == "-":  # [G]
                p[0] = p[1] - p[3]  # [G]
            elif p[2] == "*":  # [G]
                p[0] = p[1] * p[3]  # [G]
            elif p[2] == "/":  # [G]
                p[0] = p[1] / p[3]  # [G]
            elif p[2] == "//":  # [G]
                p[0] = p[1] // p[3]  # [G]
            elif p[2] == "%":  # [G]
                p[0] = p[1] % p[3]  # [G]
        except ZeroDivisionError:  # [C] '/', '//' and '%' with a zero divisor
            print(
                f"arithmetic error: division by zero for '{p[2]}'",
                file=sys.stderr,
            )  # [C]
            p[0] = error_value(p[2], p[1])  # [C] Zero in place of the bad expression
        except (ArithmeticError, ValueError) as err:  # [C] e.g. overflow, inf % inf
            print(
                f"arithmetic error: {err} for '{p[2]}'",
                file=sys.stderr,
            )  # [C]
            p[0] = error_value(p[2], p[1])  # [C]


def p_expression_uminus(p):
    "expression : '-' expression %prec UMINUS"
    p[0] = -p[2]


def p_expression_group(p):
    "expression : '(' expression ')'"
    p[0] = p[2]


def p_expression_real(p):  # [G]
    "expression : REAL '(' expression ')'"  # [G]
    try:  # [C] An integer may be too large to be represented as a float
        p[0] = float(p[3])  # [G]
    except (ArithmeticError, ValueError) as err:  # [C]
        print(
            f"arithmetic error: cannot convert to real ({err})", file=sys.stderr
        )  # [C]
        p[0] = 0.0  # [C] Zero of the result type of real()


def p_expression_floor(p):  # [G]
    "expression : FLOOR '(' expression ')'"  # [G]
    try:  # [C] Infinities and NaNs have no integer value
        p[0] = math.floor(p[3])  # [G]
    except (ArithmeticError, ValueError) as err:  # [C]
        print(f"arithmetic error: cannot apply floor ({err})", file=sys.stderr)  # [C]
        p[0] = 0  # [C] Zero of the result type of floor()


def p_expression_number(p):
    "expression : NUMBER"
    p[0] = p[1]


def p_expression_name(p):
    """expression : NAME
                  | REAL
                  | FLOOR"""  # [G]
    try:
        p[0] = names[p[1]]
    except LookupError:
        print("Undefined name '%s'" % p[1], file=sys.stderr)  # [G]
        p[0] = 0


def p_error(p):
    if p:
        print("Syntax error at '%s'" % p.value, file=sys.stderr)  # [G]
    else:
        print("Syntax error at EOF", file=sys.stderr)  # [G]
    raise SyntaxError  # [G] Tell the parser to abort immediately


import ply.yacc as yacc

parser = yacc.yacc(
    debug=False, write_tables=False
)  # [C] Silence PLY's "Generating LALR tables" notice and avoid writing
# parsetab.py / parser.out into the current directory # [C]

while True:
    try:
        s = input()  # [G] Removed the 'calc > ' prompt string
    except EOFError:
        break
    if not s:
        continue

    try:  # [G] Catch the abort signal from the parser and lexer
        yacc.parse(s)
    except SyntaxError:  # [G]
        pass  # [G] Ignore the rest of the line and wait for the next input
    except (ArithmeticError, ValueError) as err:  # [C] Last-resort guard: one bad
        print("error: %s" % err, file=sys.stderr)  # [C] statement must not end the run