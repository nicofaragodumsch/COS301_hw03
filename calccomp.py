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
#                  the coco command, writes exactly what the HW02 interpreter
#                  writes on standard output for the same input.
# Diagnostics (stderr): messages for undefined names, type mismatches and
#                  syntax errors, worded as in HW02.  They are emitted at
#                  compile time rather than at run time; standard output, which
#                  is what must agree with HW02, is unaffected.
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
# -----------------------------------------------------------------------------

BINOP_INSTR = {
    "+": "BINARY_ADD",
    "-": "BINARY_SUBTRACT",
    "*": "BINARY_MULTIPLY",
    "/": "BINARY_TRUE_DIVIDE",
    "//": "BINARY_FLOOR_DIVIDE",
    "%": "BINARY_MODULO",
}

FLOOR_HELPER = "_floor"  # name of the generated run-time support function


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
        # printed form (which also keeps 0.0 and -0.0 apart).
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


class Compiler:
    def __init__(self):
        self.main = Function("main", 0)
        self.needs_floor_helper = False

    # -- expressions ---------------------------------------------------------

    def gen_expr(self, e, f):
        if isinstance(e, Num):
            f.emit("LOAD_CONST", f.const(e.value))

        elif isinstance(e, Var):
            f.emit("LOAD_FAST", f.local(e.name))

        elif isinstance(e, Neg):
            self.gen_neg(e, f)

        elif isinstance(e, BinOp):
            self.gen_expr(e.left, f)  # left operand first: TOS1 op TOS
            self.gen_expr(e.right, f)
            f.emit(BINOP_INSTR[e.op])

        elif isinstance(e, Cast):
            self.gen_cast(e, f)

        else:  # pragma: no cover
            raise AssertionError("unknown expression node")

    def gen_neg(self, e, f):
        """JCoCo has no unary-negation instruction, and constants are unsigned,
        so a negation must be built from a binary operator and zero or one."""
        if e.type is int:
            f.emit("LOAD_CONST", f.const(0))  # -i is 0 - i
            self.gen_expr(e.operand, f)
            f.emit("BINARY_SUBTRACT")
        else:
            # 0.0 - r would turn -0.0 into +0.0, so real negation goes through
            # a multiplication by -1.0, which is exact for both signed zeros.
            f.emit("LOAD_CONST", f.const(0.0))
            f.emit("LOAD_CONST", f.const(1.0))
            f.emit("BINARY_SUBTRACT")  # -1.0
            self.gen_expr(e.operand, f)
            f.emit("BINARY_MULTIPLY")
        # In both cases the constant has the type of the operand, so that the
        # instruction never sees operands of two different types.

    def gen_cast(self, e, f):
        operand_type = e.operand.type
        if e.kind == "real":
            if operand_type is float:
                self.gen_expr(e.operand, f)  # real(r) is the identity
            else:
                f.emit("LOAD_GLOBAL", f.glob("float"))
                self.gen_expr(e.operand, f)
                f.emit("CALL_FUNCTION", 1)
        else:  # floor
            if operand_type is int:
                self.gen_expr(e.operand, f)  # floor(i) is the identity
            else:
                # JCoCo has no floor built-in and cannot import math, so the
                # compiler supplies a run-time support function.
                self.needs_floor_helper = True
                f.emit("LOAD_GLOBAL", f.glob(FLOOR_HELPER))
                self.gen_expr(e.operand, f)
                f.emit("CALL_FUNCTION", 1)

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
                f.code.append((None, None, None))  # blank line between statements
            self.gen_stmt(s, f)
        if program:
            f.code.append((None, None, None))
        f.emit("LOAD_CONST", 0)  # None
        f.emit("RETURN_VALUE")

        functions = [f]
        if self.needs_floor_helper:
            functions.append(make_floor_helper())
        return functions


def make_floor_helper():
    """Build  _floor/1: the largest integer not greater than a real number.

    JCoCo's int() truncates toward zero, as Python's does, so it already is
    the floor for non-negative arguments; for a negative argument with a
    fractional part the truncated value is one too large.  The comparison is
    made in floating point so that its two operands have the same type.
    """
    f = Function(FLOOR_HELPER, 1)
    x, t = f.local("x"), f.local("t")
    to_int, to_float = f.glob("int"), f.glob("float")
    one = f.const(1)

    f.emit("LOAD_GLOBAL", to_int)
    f.emit("LOAD_FAST", x)
    f.emit("CALL_FUNCTION", 1)
    f.emit("STORE_FAST", t)  # t = int(x), truncated toward zero
    f.emit("LOAD_GLOBAL", to_float)
    f.emit("LOAD_FAST", t)
    f.emit("CALL_FUNCTION", 1)
    f.emit("LOAD_FAST", x)
    f.emit("COMPARE_OP", 4)  # float(t) > x ?
    f.emit("POP_JUMP_IF_FALSE", "floor00")
    f.emit("LOAD_FAST", t)
    f.emit("LOAD_CONST", one)
    f.emit("BINARY_SUBTRACT")
    f.emit("STORE_FAST", t)  # t = t - 1
    f.label("floor00")
    f.emit("LOAD_FAST", t)
    f.emit("RETURN_VALUE")
    return f


# -----------------------------------------------------------------------------
# 6. Assembly output.
# -----------------------------------------------------------------------------

INDENT = "    "
OPCODE_WIDTH = 18  # mnemonic field width, so that operands line up


def format_value(v):
    """A constant in JCoCo source form.

    Only None, non-negative integers and non-negative reals can reach the
    constant pool: literals are unsigned (unary minus is a separate token)
    and the zeros the compiler introduces are unsigned too.
    """
    if v is None:
        return "None"
    if isinstance(v, int):
        return str(v)
    if v != v or v in (float("inf"), float("-inf")):
        # JCoCo has no literal for these; report and substitute zero.
        print(
            "warning: real constant %r is not representable in JCoCo; using 0.0" % v,
            file=sys.stderr,
        )
        return "0.0"
    text = repr(v)
    if "e" in text or "E" in text:
        # The JCoCo grammar's Float is a plain decimal numeral, so expand the
        # exponent.  Decimal(float) is exact, so no value is lost.
        text = format(Decimal(v), "f")
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
        line = INDENT + (mnemonic if arg is None else mnemonic.ljust(OPCODE_WIDTH) + str(arg))
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
    program = analyze(parse_program(sys.stdin))
    functions = Compiler().compile(program)
    sys.stdout.write(format_program(functions))


if __name__ == "__main__":
    main()