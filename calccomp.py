# -----------------------------------------------------------------------------
# calccomp.py
#
# COS 301, HW03: a compiler for the extended calculator language of HW02.
#
# Input  (stdin):  a program in the extended calculator language, one statement
#                  per line; the language of calc.py (O'Reilly "Lex and Yacc",
#                  p. 63) extended, as in HW02, with real numbers, scientific
#                  notation, div (//), mod (%), real() and floor().
# Output (stdout): a JCoCo assembly language program that, when executed with
#                  the coco command, writes on standard output what the HW02
#                  interpreter writes for the same input.  This holds exactly
#                  for the required integer language, and for the extended
#                  language except where the JCoCo machine cannot represent or
#                  print a real number as Python does: its integers are 32-bit,
#                  its reals print through DecimalFormat, and it provides no
#                  division for reals.  The README lists every such case, and
#                  tests/vmlimits.calc collects them.
# Diagnostics (stderr): messages for undefined names, type mismatches and
#                  syntax errors, worded as in HW02, plus warnings about values
#                  this machine cannot hold.  They are emitted at compile time
#                  rather than at run time -- JCoCo has no standard error
#                  stream -- so standard output, which is what must agree with
#                  HW02, is unaffected.
#
# Several source operations have no correct single-instruction translation on
# this machine (integer div/mod truncate, reals cannot divide, a zero divisor
# ends the run, real literals are narrowed to 32 bits).  Section 5 explains
# each and generates the run-time support it needs.
#
# Organization (the usual compiler phases, one section each):
#     1. lexical analysis     -- taken unchanged from HW02's calc.py
#     2. abstract syntax
#     3. syntax analysis      -- HW02's grammar, with tree-building actions in
#                                place of the evaluation actions
#     4. static semantics     -- symbol table, type inference, diagnostics
#     5. code generation      -- constant/local/global pools and instructions
#     6. assembly output      -- formatting of the .casm program
#     7. driver
# -----------------------------------------------------------------------------

import sys
from decimal import Decimal

# -----------------------------------------------------------------------------
# 1. Lexical analysis.  Unchanged from HW02 (calc.py); the token set of the
#    source language does not depend on what the back end does with it.
# -----------------------------------------------------------------------------

tokens = ("NAME", "NUMBER", "FLOORDIV", "REAL", "FLOOR")

literals = ["=", "+", "-", "*", "/", "(", ")", "%"]


def t_NAME(t):
    r"[a-zA-Z_][a-zA-Z0-9_]*"
    if t.value == "real":
        t.type = "REAL"
    elif t.value == "floor":
        t.type = "FLOOR"
    return t


t_FLOORDIV = r"//"


def t_NUMBER(t):
    r"\d*\.\d+(?:[eE][-+]?\d+)?|\d+\.\d*(?:[eE][-+]?\d+)?|\d+[eE][-+]?\d+|\d+"
    if "." in t.value or "e" in t.value or "E" in t.value:
        t.value = float(t.value)
    else:
        t.value = int(t.value)
    return t


t_ignore = " \t"


def t_newline(t):
    r"\n+"
    t.lexer.lineno += t.value.count("\n")


def t_error(t):
    print("Illegal character '%s'" % t.value[0], file=sys.stderr)
    t.lexer.skip(1)
    raise SyntaxError  # abort this line, exactly as in HW02


import ply.lex as lex

lexer = lex.lex()

# -----------------------------------------------------------------------------
# 2. Abstract syntax.
#
#    Each expression node acquires a .type attribute (the Python type object
#    int or float) during static semantic analysis; nothing else is stored on
#    the tree, so the tree remains a faithful record of the source program.
# -----------------------------------------------------------------------------


class Node:
    """Base class; `type` is filled in by the analyzer."""

    type = None


class Num(Node):
    """A numeric literal, or a zero standing in for an erroneous expression."""

    def __init__(self, value):
        self.value = value


class Var(Node):
    """A use of a variable."""

    def __init__(self, name):
        self.name = name


class Neg(Node):
    """Unary minus."""

    def __init__(self, operand):
        self.operand = operand


class BinOp(Node):
    """A binary application: op is one of + - * / // %."""

    def __init__(self, op, left, right):
        self.op = op
        self.left = left
        self.right = right


class Cast(Node):
    """real(e) or floor(e); kind is "real" or "floor"."""

    def __init__(self, kind, operand):
        self.kind = kind
        self.operand = operand


class Assign:
    """The statement  name = expression."""

    def __init__(self, name, expr):
        self.name = name
        self.expr = expr


class Show:
    """An expression statement: HW02 prints the value of the expression."""

    def __init__(self, expr):
        self.expr = expr


# -----------------------------------------------------------------------------
# 3. Syntax analysis.  HW02's grammar exactly; the semantic actions build a
#    tree instead of computing a value.
# -----------------------------------------------------------------------------

precedence = (
    ("left", "+", "-"),
    ("left", "*", "/", "FLOORDIV", "%"),
    ("right", "UMINUS"),
)


def p_statement_assign(p):
    """statement : NAME "=" expression
                 | REAL "=" expression
                 | FLOOR "=" expression"""
    p[0] = Assign(p[1], p[3])


def p_statement_expr(p):
    "statement : expression"
    p[0] = Show(p[1])


def p_expression_binop(p):
    """expression : expression '+' expression
                  | expression '-' expression
                  | expression '*' expression
                  | expression '/' expression
                  | expression FLOORDIV expression
                  | expression '%' expression"""
    p[0] = BinOp(p[2], p[1], p[3])


def p_expression_uminus(p):
    "expression : '-' expression %prec UMINUS"
    p[0] = Neg(p[2])


def p_expression_group(p):
    "expression : '(' expression ')'"
    p[0] = p[2]


def p_expression_real(p):
    "expression : REAL '(' expression ')'"
    p[0] = Cast("real", p[3])


def p_expression_floor(p):
    "expression : FLOOR '(' expression ')'"
    p[0] = Cast("floor", p[3])


def p_expression_number(p):
    "expression : NUMBER"
    p[0] = Num(p[1])


def p_expression_name(p):
    """expression : NAME
                  | REAL
                  | FLOOR"""
    p[0] = Var(p[1])


def p_error(p):
    if p:
        print("Syntax error at '%s'" % p.value, file=sys.stderr)
    else:
        print("Syntax error at EOF", file=sys.stderr)
    raise SyntaxError  # tell the parser to abort immediately


import ply.yacc as yacc

parser = yacc.yacc(debug=False, write_tables=False)

# -----------------------------------------------------------------------------
# 4. Static semantics.
#
#    The calculator language has no control flow, so a single left-to-right
#    walk determines, exactly, the type of every expression and the set of
#    variables that are defined at each point.  That lets the compiler decide
#    at compile time the two questions HW02 answers at run time:
#
#      * an undefined name evaluates to the integer 0 after a diagnostic;
#      * an operator applied to operands of different types evaluates to the
#        integer 0 after a diagnostic.
#
#    In both cases the analyzer replaces the offending subtree with Num(0), so
#    the generated code loads that zero directly.  The compiled program then
#    writes on stdout precisely what HW02 writes.  Knowing the types also lets
#    the code generator (a) drop the casts that are identities, and (b) choose
#    a zero of the right type for unary minus, so that no instruction in the
#    generated program is ever applied to operands of two different types.
# -----------------------------------------------------------------------------


def type_name(t):
    return t.__name__


def analyze_expr(e, env):
    """Annotate e with its type, reporting and repairing errors; return the
    (possibly replaced) node."""
    if isinstance(e, Num):
        e.type = type(e.value)

    elif isinstance(e, Var):
        if e.name not in env:
            print("Undefined name '%s'" % e.name, file=sys.stderr)
            return analyze_expr(Num(0), env)  # HW02 uses the integer 0
        e.type = env[e.name]

    elif isinstance(e, Neg):
        e.operand = analyze_expr(e.operand, env)
        e.type = e.operand.type  # HW02: -x keeps the type of x

    elif isinstance(e, BinOp):
        e.left = analyze_expr(e.left, env)
        e.right = analyze_expr(e.right, env)
        if e.left.type is not e.right.type:
            print(
                "type error: mismatched types %s and %s for '%s'"
                % (type_name(e.left.type), type_name(e.right.type), e.op),
                file=sys.stderr,
            )
            return analyze_expr(Num(0), env)  # HW02 yields the integer 0
        # '/' is true division: in HW02 (Python 3) it yields a real number
        # even for two integer operands.  Every other operator preserves the
        # common operand type.
        e.type = float if e.op == "/" else e.left.type

    elif isinstance(e, Cast):
        e.operand = analyze_expr(e.operand, env)
        e.type = float if e.kind == "real" else int

    else:  # pragma: no cover -- cannot happen
        raise AssertionError("unknown expression node")

    return e


def analyze(program):
    """Analyze a list of statements in order, threading the symbol table."""
    env = {}  # variable name -> type (int or float)
    for stmt in program:
        stmt.expr = analyze_expr(stmt.expr, env)
        if isinstance(stmt, Assign):
            env[stmt.name] = stmt.expr.type  # a variable may change type
    return program


# -----------------------------------------------------------------------------
# 5. Code generation.
#
#    JCoCo is a stack machine, so an expression is compiled by the usual
#    post-order walk: code for the left operand, code for the right operand,
#    then the instruction, leaving the value on the operand stack.
#
#    Three of the source language's operations cannot be compiled to a single
#    instruction, because the JCoCo virtual machine does not implement the
#    Python semantics HW02 has (see the README, "Adapting to the target VM"):
#
#      * `BINARY_FLOOR_DIVIDE` and `BINARY_MODULO` on integers truncate toward
#        zero, so they disagree with HW02 whenever exactly one operand is
#        negative;
#      * the machine's real numbers implement no division at all, neither
#        `__truediv__` nor `__floordiv__`, so `/` and `//` on reals raise a
#        TypeError at run time;
#      * a zero divisor raises an exception and ends the run, whereas HW02
#        substitutes a zero for the offending expression and carries on, so
#        every later statement's output would be lost.
#
#    The compiler therefore emits a small run-time support library (section 5b)
#    and calls into it for `/`, `//` and `%`.  Each support function is
#    generated only if the program uses it.  They are written to be correct on
#    a machine whose `//` and `%` are floored as well as on one whose are
#    truncated, so the same compiler serves either build of JCoCo.
#
#    Real constants are likewise not emitted as literals: the JCoCo assembler
#    reads them with 32-bit `Float.parseFloat`, which would silently perturb
#    every real in the program (3.14159 becomes 3.141590118408203).  Instead a
#    real constant is materialized as the quotient of two exact integers, which
#    the machine's integer division computes as a correctly rounded double --
#    the same value HW02's lexer produces.
# -----------------------------------------------------------------------------

INT_MAX = 2 ** 31 - 1  # JCoCo integers are 32-bit
MAX_EXACT_TEN = 22  # 10**22 is the largest power of ten exact as a double

DIRECT_INSTR = {  # operators that are one instruction for either operand type
    "+": "BINARY_ADD",
    "-": "BINARY_SUBTRACT",
    "*": "BINARY_MULTIPLY",
}

HELPER_FOR = {  # (operator, operand type) -> run-time support function
    ("/", int): "_ddiv",
    ("/", float): "_rdiv",
    ("//", int): "_idiv",
    ("//", float): "_rdivf",
    ("%", int): "_imod",
    ("%", float): "_rmod",
}

FLOOR_HELPER = "_floor"  # floor() of a real


class Pool:
    """An ordered, duplicate-free pool; index() returns a zero-based index."""

    def __init__(self):
        self.items = []
        self.index_of = {}

    def index(self, item, key=None):
        key = item if key is None else key
        if key not in self.index_of:
            self.index_of[key] = len(self.items)
            self.items.append(item)
        return self.index_of[key]

    def __len__(self):
        return len(self.items)


class Function:
    """One JCoCo function: its pools and its instruction list."""

    def __init__(self, name, arity):
        self.name = name
        self.arity = arity
        self.constants = Pool()
        self.locals = Pool()
        self.globals = Pool()
        self.code = []  # list of (label, mnemonic, argument)
        # Textbook convention: None is constant 0, so that the standard
        # epilogue LOAD_CONST 0 / RETURN_VALUE returns None.
        self.constants.index(None, key=("NoneType", "None"))

    # -- pools ---------------------------------------------------------------

    def const(self, value):
        # int 1 and float 1.0 are equal and hash alike in Python but are
        # different JCoCo constants, so the pool is keyed by type and by the
        # printed form.
        return self.constants.index(value, key=(type(value).__name__, repr(value)))

    def local(self, name):
        return self.locals.index(name)

    def glob(self, name):
        return self.globals.index(name)

    # -- instructions --------------------------------------------------------

    def emit(self, mnemonic, arg=None):
        self.code.append((None, mnemonic, arg))

    def label(self, name):
        """Attach a label to the next instruction emitted."""
        self.code.append((name, None, None))

    def blank(self):
        self.code.append((None, None, None))

    # -- small idioms used by both the code generator and the library --------

    def push_int(self, value):
        """Push an integer.  Constants are unsigned in the grammar, so a
        negative one is a subtraction from zero."""
        if value < 0:
            self.emit("LOAD_CONST", self.const(0))
            self.emit("LOAD_CONST", self.const(-value))
            self.emit("BINARY_SUBTRACT")
        else:
            self.emit("LOAD_CONST", self.const(value))

    def ratio(self, numerator, denominator=1):
        """Push the real number numerator/denominator.

        Integer division is the only division this machine performs correctly,
        and for operands it can represent exactly it is the IEEE quotient --
        which, for a decimal numeral's mantissa and power of ten, is exactly
        the double HW02's lexer builds.  It is also the only way to obtain a
        real constant at all without going through the assembler's 32-bit
        Float.parseFloat.
        """
        self.push_int(numerator)
        self.emit("LOAD_CONST", self.const(denominator))
        self.emit("BINARY_TRUE_DIVIDE")

    def power_of_ten(self, k):
        """Push 10**k as a real, exactly (k <= MAX_EXACT_TEN).

        Powers of ten up to 10**22 are exactly representable as doubles, and a
        product of exact doubles whose true product is exact is itself exact,
        so the factors below introduce no rounding.
        """
        parts = []
        while k > 9:  # each factor must fit in a 32-bit constant
            parts.append(9)
            k -= 9
        parts.append(k)
        self.ratio(10 ** parts[0])
        for part in parts[1:]:
            self.ratio(10 ** part)
            self.emit("BINARY_MULTIPLY")

    def compare_zero(self, slot, opname, is_real):
        """Compare the local in `slot` with a zero of its own type."""
        self.emit("LOAD_FAST", slot)
        if is_real:
            self.ratio(0)
        else:
            self.emit("LOAD_CONST", self.const(0))
        self.emit("COMPARE_OP", opname)


# -----------------------------------------------------------------------------
# 5b. The run-time support library.
#
#     Each builder returns a complete JCoCo function; `NEEDS` lists the other
#     support functions it calls.  Only the reachable ones are emitted.
#
#     Comparison opnames used below: 0 is <, 2 is ==, 4 is >.
# -----------------------------------------------------------------------------


def build_imod():
    """_imod/2: a % b with the sign of b (HW02's, i.e. Python's, modulo).

    The machine's BINARY_MODULO leaves the sign of the dividend, so when the
    remainder is not zero and its sign differs from the divisor's, the divisor
    is added.  On a machine whose BINARY_MODULO is already floored the two
    tests below can never both succeed, so the same code is correct there.
    """
    f = Function("_imod", 2)
    a, b = f.local("a"), f.local("b")
    r = f.local("r")

    f.compare_zero(b, 2, False)
    f.emit("POP_JUMP_IF_TRUE", "mod04")  # b == 0: HW02 yields zero
    f.emit("LOAD_FAST", a)
    f.emit("LOAD_FAST", b)
    f.emit("BINARY_MODULO")
    f.emit("STORE_FAST", r)
    f.compare_zero(r, 2, False)
    f.emit("POP_JUMP_IF_TRUE", "mod03")  # exact: no correction, and no sign
    f.compare_zero(r, 0, False)
    f.emit("POP_JUMP_IF_FALSE", "mod01")
    f.compare_zero(b, 4, False)  # r < 0: correct if b > 0
    f.emit("POP_JUMP_IF_TRUE", "mod02")
    f.emit("JUMP_ABSOLUTE", "mod03")
    f.label("mod01")
    f.compare_zero(b, 0, False)  # r > 0: correct if b < 0
    f.emit("POP_JUMP_IF_FALSE", "mod03")
    f.label("mod02")
    f.emit("LOAD_FAST", r)
    f.emit("LOAD_FAST", b)
    f.emit("BINARY_ADD")
    f.emit("STORE_FAST", r)
    f.label("mod03")
    f.emit("LOAD_FAST", r)
    f.emit("RETURN_VALUE")
    f.label("mod04")
    f.emit("LOAD_CONST", f.const(0))
    f.emit("RETURN_VALUE")
    return f


def build_idiv():
    """_idiv/2: a // b rounded toward minus infinity, as HW02 rounds it.

    Once the floored remainder is known, a - (a % b) is an exact multiple of b,
    so dividing it by b gives the floored quotient however the machine's
    integer division rounds.
    """
    f = Function("_idiv", 2)
    a, b = f.local("a"), f.local("b")

    f.compare_zero(b, 2, False)
    f.emit("POP_JUMP_IF_TRUE", "div01")  # b == 0: HW02 yields zero
    f.emit("LOAD_FAST", a)
    f.emit("LOAD_GLOBAL", f.glob("_imod"))
    f.emit("LOAD_FAST", a)
    f.emit("LOAD_FAST", b)
    f.emit("CALL_FUNCTION", 2)
    f.emit("BINARY_SUBTRACT")  # a - (a % b)
    f.emit("LOAD_FAST", b)
    f.emit("BINARY_FLOOR_DIVIDE")  # exact division: rounding is irrelevant
    f.emit("RETURN_VALUE")
    f.label("div01")
    f.emit("LOAD_CONST", f.const(0))
    f.emit("RETURN_VALUE")
    return f


def build_ddiv():
    """_ddiv/2: a / b for integers.  The instruction is right (it yields a
    real, as HW02 does); only HW02's recovery from a zero divisor is missing."""
    f = Function("_ddiv", 2)
    a, b = f.local("a"), f.local("b")

    f.compare_zero(b, 2, False)
    f.emit("POP_JUMP_IF_TRUE", "ddv01")
    f.emit("LOAD_FAST", a)
    f.emit("LOAD_FAST", b)
    f.emit("BINARY_TRUE_DIVIDE")
    f.emit("RETURN_VALUE")
    f.label("ddv01")
    f.ratio(0)  # HW02 yields 0.0 for a failed '/'
    f.emit("RETURN_VALUE")
    return f


def build_rdiv():
    """_rdiv/2: a / b for reals.

    The machine's reals have no division at all, but its *integers* divide
    correctly, and integer-by-real division is a correctly rounded double.  So
    when the dividend is a whole number the machine's integer range can hold,
    the quotient is obtained exactly by converting the dividend.  (The
    conversion saturates rather than wrapping, so comparing it back with the
    dividend also rejects anything out of range.)

    Otherwise the quotient must be formed as a * (1/b), and the extra rounding
    of the reciprocal can move the last bit of the result: about one real
    division in six differs from HW02's in that bit.  No sequence of this
    machine's instructions can do better, since correctly rounded division of
    two arbitrary doubles is exactly the operation it lacks.  See the README.
    """
    f = Function("_rdiv", 2)
    a, b = f.local("a"), f.local("b")
    t = f.local("t")

    f.compare_zero(b, 2, True)
    f.emit("POP_JUMP_IF_TRUE", "rdv02")  # b == 0: HW02 yields 0.0
    f.emit("LOAD_GLOBAL", f.glob("int"))
    f.emit("LOAD_FAST", a)
    f.emit("CALL_FUNCTION", 1)
    f.emit("STORE_FAST", t)  # t = the truncated dividend
    f.emit("LOAD_FAST", t)
    f.emit("LOAD_CONST", f.const(1))
    f.emit("BINARY_TRUE_DIVIDE")  # back to a real, exactly
    f.emit("LOAD_FAST", a)
    f.emit("COMPARE_OP", 2)  # was the dividend a whole number in range?
    f.emit("POP_JUMP_IF_FALSE", "rdv01")
    f.emit("LOAD_FAST", t)  # yes: an exact integer-by-real division
    f.emit("LOAD_FAST", b)
    f.emit("BINARY_TRUE_DIVIDE")
    f.emit("RETURN_VALUE")
    f.label("rdv01")
    f.emit("LOAD_FAST", a)  # no: a * (1/b), the best available
    f.emit("LOAD_CONST", f.const(1))
    f.emit("LOAD_FAST", b)
    f.emit("BINARY_TRUE_DIVIDE")
    f.emit("BINARY_MULTIPLY")
    f.emit("RETURN_VALUE")
    f.label("rdv02")
    f.ratio(0)
    f.emit("RETURN_VALUE")
    return f


def build_rfloor():
    """_rfloor/1: the floor of a real, as a real."""
    f = Function("_rfloor", 1)
    x = f.local("x")
    t = f.local("t")

    f.emit("LOAD_GLOBAL", f.glob("int"))
    f.emit("LOAD_FAST", x)
    f.emit("CALL_FUNCTION", 1)  # truncates toward zero
    f.emit("LOAD_CONST", f.const(1))
    f.emit("BINARY_TRUE_DIVIDE")  # back to a real, exactly
    f.emit("STORE_FAST", t)
    f.emit("LOAD_FAST", t)
    f.emit("LOAD_FAST", x)
    f.emit("COMPARE_OP", 4)  # t > x: x was negative and not whole
    f.emit("POP_JUMP_IF_FALSE", "rfl01")
    f.emit("LOAD_FAST", t)
    f.ratio(1)
    f.emit("BINARY_SUBTRACT")
    f.emit("STORE_FAST", t)
    f.label("rfl01")
    f.emit("LOAD_FAST", t)
    f.emit("RETURN_VALUE")
    return f


def build_floor():
    """_floor/1: floor() of a real, as an integer -- HW02's math.floor."""
    f = Function("_floor", 1)
    f.emit("LOAD_GLOBAL", f.glob("int"))
    f.emit("LOAD_GLOBAL", f.glob("_rfloor"))
    f.emit("LOAD_FAST", f.local("x"))
    f.emit("CALL_FUNCTION", 1)
    f.emit("CALL_FUNCTION", 1)
    f.emit("RETURN_VALUE")
    return f


def build_rabs():
    """_rabs/1: the magnitude of a real."""
    f = Function("_rabs", 1)
    x = f.local("x")
    f.compare_zero(x, 0, True)
    f.emit("POP_JUMP_IF_FALSE", "abs01")
    f.ratio(-1)
    f.emit("LOAD_FAST", x)
    f.emit("BINARY_MULTIPLY")
    f.emit("RETURN_VALUE")
    f.label("abs01")
    f.emit("LOAD_FAST", x)
    f.emit("RETURN_VALUE")
    return f


def build_rdm():
    """_rdm/3: shift-and-subtract division of reals.  A >= 0 and B > 0; the
    third argument selects the truncated quotient (0) or the remainder (1).

    This is long division in binary.  The divisor is doubled until it reaches
    the dividend, then halved back down, subtracted whenever it fits, with the
    corresponding power of two accumulated into the quotient.  Every step is
    exact: scaling by two neither rounds nor loses bits, and each subtraction
    happens only when d <= r < 2d, where the difference is exactly
    representable.  So the remainder is HW02's remainder bit for bit -- which
    the obvious a - b * (a // b) is not, because both of those operations
    round.

    The loop runs once per power of two between the operands, at most about
    2100 times for the machine's extreme exponents and typically fewer than 60.
    """
    f = Function("_rdm", 3)
    a, b, want = f.local("A"), f.local("B"), f.local("want")
    d, s, r, q = f.local("d"), f.local("s"), f.local("r"), f.local("q")

    f.emit("LOAD_FAST", b)
    f.emit("STORE_FAST", d)
    f.ratio(1)
    f.emit("STORE_FAST", s)

    f.label("dm00")  # scale the divisor up to the dividend's magnitude
    f.emit("LOAD_FAST", d)
    f.ratio(2)
    f.emit("BINARY_MULTIPLY")
    f.emit("LOAD_FAST", a)
    f.emit("COMPARE_OP", 1)  # d * 2 <= A ?
    f.emit("POP_JUMP_IF_FALSE", "dm01")
    f.emit("LOAD_FAST", d)
    f.ratio(2)
    f.emit("BINARY_MULTIPLY")
    f.emit("STORE_FAST", d)
    f.emit("LOAD_FAST", s)
    f.ratio(2)
    f.emit("BINARY_MULTIPLY")
    f.emit("STORE_FAST", s)
    f.emit("JUMP_ABSOLUTE", "dm00")

    f.label("dm01")
    f.emit("LOAD_FAST", a)
    f.emit("STORE_FAST", r)
    f.ratio(0)
    f.emit("STORE_FAST", q)

    f.label("dm02")  # subtract where it fits, halving all the way down
    f.emit("LOAD_FAST", r)
    f.emit("LOAD_FAST", d)
    f.emit("COMPARE_OP", 5)  # r >= d ?
    f.emit("POP_JUMP_IF_FALSE", "dm03")
    f.emit("LOAD_FAST", r)
    f.emit("LOAD_FAST", d)
    f.emit("BINARY_SUBTRACT")  # exact: d <= r < 2d
    f.emit("STORE_FAST", r)
    f.emit("LOAD_FAST", q)
    f.emit("LOAD_FAST", s)
    f.emit("BINARY_ADD")
    f.emit("STORE_FAST", q)
    f.label("dm03")
    f.emit("LOAD_FAST", s)
    f.ratio(1)
    f.emit("COMPARE_OP", 2)  # s == 1: the units place is done
    f.emit("POP_JUMP_IF_TRUE", "dm04")
    f.emit("LOAD_FAST", d)
    f.ratio(1, 2)
    f.emit("BINARY_MULTIPLY")
    f.emit("STORE_FAST", d)
    f.emit("LOAD_FAST", s)
    f.ratio(1, 2)
    f.emit("BINARY_MULTIPLY")
    f.emit("STORE_FAST", s)
    f.emit("JUMP_ABSOLUTE", "dm02")

    f.label("dm04")
    f.compare_zero(want, 2, False)
    f.emit("POP_JUMP_IF_TRUE", "dm05")
    f.emit("LOAD_FAST", r)
    f.emit("RETURN_VALUE")
    f.label("dm05")
    f.emit("LOAD_FAST", q)
    f.emit("RETURN_VALUE")
    return f


def call_rdm(f, magnitudes, want):
    """Emit a call on _rdm with the two magnitude locals and a selector."""
    f.emit("LOAD_GLOBAL", f.glob("_rdm"))
    for slot in magnitudes:
        f.emit("LOAD_FAST", slot)
    f.emit("LOAD_CONST", f.const(want))
    f.emit("CALL_FUNCTION", 3)


def store_magnitudes(f, a, b, big_a, big_b):
    """big_a, big_b := |a|, |b|."""
    for source, target in ((a, big_a), (b, big_b)):
        f.emit("LOAD_GLOBAL", f.glob("_rabs"))
        f.emit("LOAD_FAST", source)
        f.emit("CALL_FUNCTION", 1)
        f.emit("STORE_FAST", target)


def build_rmod():
    """_rmod/2: a % b for reals, with the sign of b, as HW02 computes it.

    The exact remainder comes from _rdm; only its sign needs adjusting, in the
    same way as for integers.
    """
    f = Function("_rmod", 2)
    a, b = f.local("a"), f.local("b")
    big_a, big_b, r = f.local("A"), f.local("B"), f.local("r")

    f.compare_zero(b, 2, True)
    f.emit("POP_JUMP_IF_TRUE", "rmd05")  # b == 0: HW02 yields 0.0
    store_magnitudes(f, a, b, big_a, big_b)
    call_rdm(f, (big_a, big_b), 1)
    f.emit("STORE_FAST", r)
    f.compare_zero(r, 2, True)
    f.emit("POP_JUMP_IF_TRUE", "rmd06")  # a zero remainder takes b's sign
    f.compare_zero(a, 0, True)
    f.emit("POP_JUMP_IF_FALSE", "rmd00")
    f.ratio(-1)  # the truncating remainder takes the sign of the dividend
    f.emit("LOAD_FAST", r)
    f.emit("BINARY_MULTIPLY")
    f.emit("STORE_FAST", r)
    f.label("rmd00")
    f.compare_zero(r, 2, True)
    f.emit("POP_JUMP_IF_TRUE", "rmd04")  # exact: no sign to correct
    f.compare_zero(r, 0, True)
    f.emit("POP_JUMP_IF_FALSE", "rmd01")
    f.compare_zero(b, 4, True)  # r < 0: correct if b > 0
    f.emit("POP_JUMP_IF_TRUE", "rmd02")
    f.emit("JUMP_ABSOLUTE", "rmd04")
    f.label("rmd01")
    f.compare_zero(b, 0, True)  # r > 0: correct if b < 0
    f.emit("POP_JUMP_IF_FALSE", "rmd04")
    f.label("rmd02")
    f.emit("LOAD_FAST", r)
    f.emit("LOAD_FAST", b)
    f.emit("BINARY_ADD")
    f.emit("STORE_FAST", r)
    f.label("rmd04")
    f.emit("LOAD_FAST", r)
    f.emit("RETURN_VALUE")
    f.label("rmd05")
    f.ratio(0)
    f.emit("RETURN_VALUE")
    f.label("rmd06")  # 0.0 * b is the zero with b's sign, as HW02 returns
    f.ratio(0)
    f.emit("LOAD_FAST", b)
    f.emit("BINARY_MULTIPLY")
    f.emit("RETURN_VALUE")
    return f


def build_rdivf():
    """_rdivf/2: a // b for reals, rounded toward minus infinity.

    _rdm gives the magnitude of the truncated quotient exactly; when the
    operands' signs differ the quotient is negated, and lowered by one if the
    division left a remainder.
    """
    f = Function("_rdivf", 2)
    a, b = f.local("a"), f.local("b")
    big_a, big_b, q = f.local("A"), f.local("B"), f.local("q")

    f.compare_zero(b, 2, True)
    f.emit("POP_JUMP_IF_TRUE", "rfd05")  # b == 0: HW02 yields 0.0
    store_magnitudes(f, a, b, big_a, big_b)
    call_rdm(f, (big_a, big_b), 0)
    f.emit("STORE_FAST", q)
    f.compare_zero(a, 0, True)
    f.emit("POP_JUMP_IF_FALSE", "rfd01")
    f.compare_zero(b, 0, True)  # a < 0: signs differ unless b < 0 too
    f.emit("POP_JUMP_IF_FALSE", "rfd02")
    f.emit("JUMP_ABSOLUTE", "rfd04")
    f.label("rfd01")
    f.compare_zero(b, 0, True)  # a >= 0: signs differ if b < 0
    f.emit("POP_JUMP_IF_FALSE", "rfd04")
    f.label("rfd02")  # signs differ: negate, then floor
    f.ratio(-1)
    f.emit("LOAD_FAST", q)
    f.emit("BINARY_MULTIPLY")
    f.emit("STORE_FAST", q)
    call_rdm(f, (big_a, big_b), 1)
    f.ratio(0)
    f.emit("COMPARE_OP", 3)  # remainder != 0: the quotient was truncated up
    f.emit("POP_JUMP_IF_FALSE", "rfd04")
    f.emit("LOAD_FAST", q)
    f.ratio(1)
    f.emit("BINARY_SUBTRACT")
    f.emit("STORE_FAST", q)
    f.label("rfd04")
    f.emit("LOAD_FAST", q)
    f.emit("RETURN_VALUE")
    f.label("rfd05")
    f.ratio(0)
    f.emit("RETURN_VALUE")
    return f


LIBRARY = {  # name -> (builder, functions it calls)
    "_imod": (build_imod, ()),
    "_idiv": (build_idiv, ("_imod",)),
    "_ddiv": (build_ddiv, ()),
    "_rdiv": (build_rdiv, ()),
    "_rabs": (build_rabs, ()),
    "_rdm": (build_rdm, ()),
    "_rfloor": (build_rfloor, ()),
    "_floor": (build_floor, ("_rfloor",)),
    "_rdivf": (build_rdivf, ("_rabs", "_rdm")),
    "_rmod": (build_rmod, ("_rabs", "_rdm")),
}


# -----------------------------------------------------------------------------
# 5c. Real constants.
# -----------------------------------------------------------------------------


def decimal_parts(value):
    """Return (mantissa, exponent) with mantissa * 10**exponent exactly
    `value`, using as few mantissa digits as possible, or None for an infinity
    or a NaN.

    repr(value) round-trips, so its digits denote `value` exactly; normalizing
    drops trailing zeros, which is what lets 1e10 and 999999999.0 be expressed
    with a mantissa the machine's 32-bit integers can hold.
    """
    sign, digits, exponent = Decimal(repr(value)).normalize().as_tuple()
    if not isinstance(exponent, int):  # 'F' for infinity, 'n' for NaN
        return None
    mantissa = int("".join(str(d) for d in digits))
    return (-mantissa if sign else mantissa), exponent


# -----------------------------------------------------------------------------
# 5d. The code generator proper.
# -----------------------------------------------------------------------------


class Compiler:
    def __init__(self):
        self.main = Function("main", 0)
        self.needed = []  # support functions to emit, in order of first need
        self.unassemblable = False  # emitted something JCoCo cannot assemble

    def need(self, name):
        """Record that `name` -- and whatever it calls -- must be emitted."""
        if name not in self.needed:
            self.needed.append(name)
            if name == "_rdiv":  # say so once, not once per occurrence
                print("note: JCoCo provides no division for real numbers; the "
                      "substitute emitted here is exact when the dividend is a "
                      "whole number and may differ in the last bit otherwise",
                      file=sys.stderr)
            for dependency in LIBRARY[name][1]:
                self.need(dependency)
        return name

    def call_helper(self, name, f, operands):
        f.emit("LOAD_GLOBAL", f.glob(self.need(name)))
        for operand in operands:
            self.gen_expr(operand, f)
        f.emit("CALL_FUNCTION", len(operands))

    # -- expressions ---------------------------------------------------------

    def gen_expr(self, e, f):
        if isinstance(e, Num):
            self.gen_const(e.value, f)

        elif isinstance(e, Var):
            f.emit("LOAD_FAST", f.local(e.name))

        elif isinstance(e, Neg):
            self.gen_neg(e, f)

        elif isinstance(e, BinOp):
            if e.op in DIRECT_INSTR:
                self.gen_expr(e.left, f)  # left operand first: TOS1 op TOS
                self.gen_expr(e.right, f)
                f.emit(DIRECT_INSTR[e.op])
            else:  # '/', '//' and '%' go through the support library
                self.call_helper(HELPER_FOR[(e.op, e.left.type)], f,
                                 (e.left, e.right))

        elif isinstance(e, Cast):
            self.gen_cast(e, f)

        else:  # pragma: no cover
            raise AssertionError("unknown expression node")

    def gen_const(self, value, f):
        if isinstance(value, int):
            if abs(value) > INT_MAX:
                print("warning: integer constant %d is outside JCoCo's 32-bit "
                      "integer range; the assembler will reject it" % value,
                      file=sys.stderr)
                self.unassemblable = True
            f.emit("LOAD_CONST", f.const(value))
            return

        parts = decimal_parts(value)
        if parts is None:  # an infinity or a NaN: no JCoCo literal exists
            print("warning: real constant %r cannot be represented by JCoCo; "
                  "using 0.0" % value, file=sys.stderr)
            f.ratio(0)
            return

        mantissa, exponent = parts
        if abs(mantissa) <= INT_MAX and abs(exponent) <= MAX_EXACT_TEN:
            # Exactly one rounding occurs, of the true decimal value, so the
            # result is the same double HW02's lexer produces.
            if exponent == 0:
                f.ratio(mantissa)
            elif exponent < 0:
                scale = 10 ** -exponent
                if scale <= INT_MAX:
                    f.ratio(mantissa, scale)  # integer by integer
                else:
                    f.push_int(mantissa)  # integer by exact real
                    f.power_of_ten(-exponent)
                    f.emit("BINARY_TRUE_DIVIDE")
            else:
                f.ratio(mantissa)  # exact real times exact power of ten
                f.power_of_ten(exponent)
                f.emit("BINARY_MULTIPLY")
            return

        # More significant digits, or a wider exponent, than the machine's
        # integers can carry.  Fall back on a literal, which the assembler
        # narrows to 32 bits, and say so.
        print("warning: real constant %r needs more precision than JCoCo's "
              "32-bit assembler provides; output may differ" % value,
              file=sys.stderr)
        f.emit("LOAD_CONST", f.const(value))

    def gen_neg(self, e, f):
        """JCoCo has no unary-negation instruction and its constants are
        unsigned, so a negation is built from a binary operator."""
        if e.type is int:
            f.emit("LOAD_CONST", f.const(0))  # -i is 0 - i
            self.gen_expr(e.operand, f)
            f.emit("BINARY_SUBTRACT")
        else:
            # 0.0 - r would turn -0.0 into +0.0, so real negation is a
            # multiplication by -1.0, which is exact for both signed zeros.
            f.ratio(-1)
            self.gen_expr(e.operand, f)
            f.emit("BINARY_MULTIPLY")
        # In both cases the constant has the type of the operand, so that no
        # instruction is ever applied to operands of two different types.

    def gen_cast(self, e, f):
        if e.kind == "real":
            if e.operand.type is float:
                self.gen_expr(e.operand, f)  # real(r) is the identity
            else:
                f.emit("LOAD_GLOBAL", f.glob("float"))
                self.gen_expr(e.operand, f)
                f.emit("CALL_FUNCTION", 1)
        else:  # floor
            if e.operand.type is int:
                self.gen_expr(e.operand, f)  # floor(i) is the identity
            else:
                self.call_helper(FLOOR_HELPER, f, (e.operand,))

    # -- statements ----------------------------------------------------------

    def gen_stmt(self, s, f):
        if isinstance(s, Assign):
            self.gen_expr(s.expr, f)
            f.emit("STORE_FAST", f.local(s.name))
        else:  # Show: HW02 prints the value of an expression statement
            f.emit("LOAD_GLOBAL", f.glob("print"))
            self.gen_expr(s.expr, f)
            f.emit("CALL_FUNCTION", 1)
            f.emit("POP_TOP")  # print returns None; keep the stack clean

    # -- whole program -------------------------------------------------------

    def compile(self, program):
        f = self.main
        for i, s in enumerate(program):
            if i:
                f.blank()  # a blank line between statements, for the reader
            self.gen_stmt(s, f)
        if program:
            f.blank()
        f.emit("LOAD_CONST", 0)  # None
        f.emit("RETURN_VALUE")
        return [f] + [LIBRARY[name][0]() for name in self.needed]


# -----------------------------------------------------------------------------
# 6. Assembly output.
# -----------------------------------------------------------------------------

INDENT = "    "
OPCODE_WIDTH = 18  # mnemonic field width, so that operands line up


def format_value(v):
    """A constant in JCoCo source form.  Only None, integers within the
    machine's range, and (rarely, see gen_const) reals reach the pool."""
    if v is None:
        return "None"
    if isinstance(v, int):
        return str(v)
    text = repr(v)
    if "e" in text or "E" in text:
        text = format(Decimal(v), "f")  # the grammar's Float is plain decimal
    if "." not in text:
        text += ".0"
    return text


def format_function(f):
    out = ["Function: %s/%d" % (f.name, f.arity)]
    out.append("Constants: " + ", ".join(format_value(v) for v in f.constants.items))
    if len(f.locals):  # an empty section is omitted entirely
        out.append("Locals: " + ", ".join(f.locals.items))
    if len(f.globals):
        out.append("Globals: " + ", ".join(f.globals.items))
    out.append("BEGIN")
    for label, mnemonic, arg in f.code:
        if mnemonic is None:
            out.append(label + ":" if label else "")  # label line, or spacing
            continue
        line = INDENT + (mnemonic if arg is None
                         else mnemonic.ljust(OPCODE_WIDTH) + str(arg))
        out.append(line.rstrip())
    out.append("END")
    return "\n".join(out)


def format_program(functions):
    return "\n\n".join(format_function(f) for f in functions) + "\n"


# -----------------------------------------------------------------------------
# 7. Driver.  Statements are read one per line, as in HW02: a line with a
#    lexical or syntax error is reported and discarded, and compilation
#    continues with the next line.
# -----------------------------------------------------------------------------


def parse_program(source_lines):
    program = []
    for s in source_lines:
        if not s.strip():
            continue
        try:
            statement = parser.parse(s, lexer=lexer.clone())
        except SyntaxError:
            continue  # ignore the rest of the line, as HW02 does
        if statement is not None:
            program.append(statement)
    return program


def main():
    """Exit 0 normally, or 2 when the program written out is known not to
    assemble, so that a script driving the compiler can tell the difference.
    Warnings about values this machine holds only approximately do not change
    the status: those programs do assemble and run."""
    program = analyze(parse_program(sys.stdin))
    compiler = Compiler()
    functions = compiler.compile(program)
    sys.stdout.write(format_program(functions))
    return 2 if compiler.unassemblable else 0


if __name__ == "__main__":
    sys.exit(main())