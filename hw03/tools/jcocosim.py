# -----------------------------------------------------------------------------
# jcocosim.py -- a small simulator for the subset of JCoCo that calccomp.py
# generates.  It is a *testing aid*, not part of the homework: it makes it
# possible to check the compiler's output where the coco command is not
# installed.  Prefer the real VM whenever it is available.
#
# It deliberately models the target's behavior rather than Python's, since the
# differences are exactly what the compiler has to work around (all verified
# against kentdlee/JCoCo, and against the real VM's output):
#
#   * integers are 32-bit and +, - and * raise on overflow (Math.addExact);
#   * BINARY_FLOOR_DIVIDE and BINARY_MODULO on integers truncate toward zero
#     (Java's / and %), so they are not HW02's floored operations;
#   * integer division by an integer or a real yields a correctly rounded
#     double, and a zero divisor raises;
#   * reals implement no __truediv__ and no __floordiv__ at all, and their
#     __mod__ truncates through a 32-bit cast;
#   * int() of a real truncates toward zero and saturates at the 32-bit bounds;
#   * the assembler reads real literals with 32-bit Float.parseFloat;
#   * print renders a real with DecimalFormat("0.0###############").
#
# Besides executing, it checks the conventions the generated code claims to
# obey and reports violations on stderr: pool indices in range, arities
# matching at every call, no binary instruction applied to two operands of
# different types except the integer-by-real division the support library uses
# deliberately, and an empty operand stack at every RETURN_VALUE.
#
# Usage:  python3 jcocosim.py program.casm
# -----------------------------------------------------------------------------

import re
import struct
import sys
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

INT_MIN, INT_MAX = -2 ** 31, 2 ** 31 - 1

NO_ARG = {
    "BINARY_ADD", "BINARY_SUBTRACT", "BINARY_MULTIPLY", "BINARY_TRUE_DIVIDE",
    "BINARY_FLOOR_DIVIDE", "BINARY_MODULO", "BINARY_POWER",
    "POP_TOP", "ROT_TWO", "DUP_TOP", "RETURN_VALUE",
}


class VMError(Exception):
    """What the VM would report as an uncaught exception."""


def narrow_float(text):
    """The assembler's Float.parseFloat: a real literal is read at 32 bits."""
    return struct.unpack("f", struct.pack("f", float(text)))[0]


def check_int(value):
    if not INT_MIN <= value <= INT_MAX:
        raise VMError("integer overflow")
    return value


def java_int_cast(value):
    """Java's (int) cast of a double: truncate toward zero, saturating."""
    if value != value:  # NaN
        return 0
    truncated = int(value) if abs(value) < 2 ** 63 else (
        INT_MAX if value > 0 else INT_MIN)
    return max(INT_MIN, min(INT_MAX, truncated))


def format_real(value):
    """PyFloat.str(): DecimalFormat("0.0###############").

    One mandatory and up to sixteen fractional digits, half-even rounding, and
    never exponent notation -- unlike Python's repr, which is what HW02 uses.
    """
    if value != value:
        return "NaN"
    if value in (float("inf"), float("-inf")):
        return "Infinity" if value > 0 else "-Infinity"
    with localcontext() as context:
        context.prec = 400
        quantized = Decimal(repr(value)).quantize(
            Decimal(1).scaleb(-16), rounding=ROUND_HALF_EVEN)
    text = format(quantized, "f")
    whole, _, fraction = text.partition(".")
    fraction = fraction.rstrip("0") or "0"
    sign = "-" if (text.startswith("-") or
                   (value == 0 and str(value).startswith("-"))) else ""
    return "%s%s.%s" % (sign, whole.lstrip("-"), fraction)


def to_str(value):
    if isinstance(value, float):
        return format_real(value)
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


# -- the machine's binary operations ------------------------------------------


def op_add(a, b):
    return check_int(a + b) if isinstance(a, int) else a + b


def op_sub(a, b):
    return check_int(a - b) if isinstance(a, int) else a - b


def op_mul(a, b):
    return check_int(a * b) if isinstance(a, int) else a * b


def op_truediv(a, b):
    if isinstance(a, float):  # PyFloat has no __truediv__ at all
        raise VMError("TypeError: 'float' object has no attribute "
                      "'__truediv__'")
    if b == 0:
        raise VMError("ZeroDivisionError: division by zero")
    return a / b  # correctly rounded, for an integer or a real divisor


def op_floordiv(a, b):
    if isinstance(a, float):  # PyFloat has no __floordiv__ either
        raise VMError("TypeError: 'float' object has no attribute "
                      "'__floordiv__'")
    if b == 0:
        raise VMError("ZeroDivisionError: division by zero")
    quotient = abs(a) // abs(b)  # Java's /: truncation, not flooring
    return check_int(quotient if (a < 0) == (b < 0) else -quotient)


def op_mod(a, b):
    if b == 0:
        raise VMError("ZeroDivisionError: division or modulo by zero")
    if isinstance(a, float):  # truncating, through a 32-bit cast
        return a - java_int_cast(a / b) * b
    return abs(a) % abs(b) * (1 if a >= 0 else -1)  # Java's %


BINARY = {
    "BINARY_ADD": op_add,
    "BINARY_SUBTRACT": op_sub,
    "BINARY_MULTIPLY": op_mul,
    "BINARY_TRUE_DIVIDE": op_truediv,
    "BINARY_FLOOR_DIVIDE": op_floordiv,
    "BINARY_MODULO": op_mod,
    "BINARY_POWER": lambda a, b: a ** b,
}

COMPARE = {
    0: lambda a, b: a < b, 1: lambda a, b: a <= b, 2: lambda a, b: a == b,
    3: lambda a, b: a != b, 4: lambda a, b: a > b, 5: lambda a, b: a >= b,
}


class Function:
    def __init__(self, name, arity):
        self.name = name
        self.arity = arity
        self.constants = []
        self.locals = []
        self.globals = []
        self.code = []  # list of (mnemonic, argument)
        self.labels = {}  # label -> address


def parse_value(text):
    if text == "None":
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d*", text):
        return narrow_float(text)
    raise SyntaxError("unrecognized constant: %s" % text)


def parse(source):
    functions, current = {}, None
    for raw in source.splitlines():
        line = raw.strip()
        if not line:
            continue
        header = re.fullmatch(r"Function:\s*(\w+)/(\d+)", line)
        if header:
            current = Function(header.group(1), int(header.group(2)))
            functions[current.name] = current
            continue
        if current is None:
            raise SyntaxError("text outside a function: %s" % line)
        for section, attribute in (("Constants", "constants"),
                                   ("Locals", "locals"),
                                   ("Globals", "globals")):
            match = re.fullmatch(section + r":\s*(.*)", line)
            if match:
                items = [x.strip() for x in match.group(1).split(",")]
                setattr(current, attribute,
                        [parse_value(x) for x in items]
                        if attribute == "constants" else items)
                break
        else:
            if line in ("BEGIN", "END"):
                continue
            while True:  # any number of labels may precede an instruction
                label = re.match(r"([A-Za-z_@][A-Za-z0-9_@]*):\s*(.*)", line)
                if not label:
                    break
                current.labels[label.group(1)] = len(current.code)
                line = label.group(2).strip()
            if not line:
                continue
            parts = line.split()
            mnemonic = parts[0]
            argument = parts[1] if len(parts) > 1 else None
            if argument is not None and re.fullmatch(r"-?\d+", argument):
                argument = int(argument)
            if (mnemonic in NO_ARG) != (argument is None):
                raise SyntaxError("bad operand count: %s" % line)
            current.code.append((mnemonic, argument))
    return functions


class Machine:
    def __init__(self, functions):
        self.functions = functions
        self.problems = []

    def complain(self, message):
        self.problems.append(message)
        print("jcocosim: " + message, file=sys.stderr)

    def check_operands(self, mnemonic, left, right):
        if type(left) is type(right):
            return
        # An integer dividend over a real divisor is the one mixed-type
        # operation the support library uses on purpose; anything else is a bug
        # in the generated code, since the source language forbids mixing.
        if mnemonic == "BINARY_TRUE_DIVIDE" and isinstance(left, int) \
                and isinstance(right, float):
            return
        self.complain("%s applied to %s and %s" % (
            mnemonic, type(left).__name__, type(right).__name__))

    def resolve(self, name):
        if name in self.functions:
            return name
        builtins = {"print": self.builtin_print, "int": self.builtin_int,
                    "float": float, "str": to_str}
        if name not in builtins:
            raise VMError("no such global: %s" % name)
        return builtins[name]

    @staticmethod
    def builtin_print(*arguments):
        print(" ".join(to_str(a) for a in arguments), flush=True)
        return None

    @staticmethod
    def builtin_int(value):
        return java_int_cast(value) if isinstance(value, float) else value

    def call(self, callee, arguments):
        if callable(callee):
            return callee(*arguments)
        function = self.functions[callee]
        if len(arguments) != function.arity:
            self.complain("%s/%d called with %d argument(s)" % (
                function.name, function.arity, len(arguments)))
        return self.run(function, arguments)

    def run(self, function, arguments=()):
        variables = [None] * max(len(function.locals), len(arguments))
        variables[:len(arguments)] = arguments
        stack, pc = [], 0
        while True:
            mnemonic, argument = function.code[pc]
            pc += 1
            if mnemonic in BINARY:
                right, left = stack.pop(), stack.pop()
                self.check_operands(mnemonic, left, right)
                stack.append(BINARY[mnemonic](left, right))
            elif mnemonic == "LOAD_CONST":
                stack.append(function.constants[argument])
            elif mnemonic == "LOAD_FAST":
                stack.append(variables[argument])
            elif mnemonic == "STORE_FAST":
                variables[argument] = stack.pop()
            elif mnemonic == "LOAD_GLOBAL":
                stack.append(self.resolve(function.globals[argument]))
            elif mnemonic == "CALL_FUNCTION":
                actuals = [stack.pop() for _ in range(argument)][::-1]
                stack.append(self.call(stack.pop(), actuals))
            elif mnemonic == "POP_TOP":
                stack.pop()
            elif mnemonic == "ROT_TWO":
                stack[-1], stack[-2] = stack[-2], stack[-1]
            elif mnemonic == "DUP_TOP":
                stack.append(stack[-1])
            elif mnemonic == "COMPARE_OP":
                right, left = stack.pop(), stack.pop()
                self.check_operands("COMPARE_OP", left, right)
                stack.append(COMPARE[argument](left, right))
            elif mnemonic == "POP_JUMP_IF_FALSE":
                pc = function.labels[argument] if not stack.pop() else pc
            elif mnemonic == "POP_JUMP_IF_TRUE":
                pc = function.labels[argument] if stack.pop() else pc
            elif mnemonic in ("JUMP_ABSOLUTE", "JUMP_FORWARD"):
                pc = function.labels[argument]
            elif mnemonic == "RETURN_VALUE":
                result = stack.pop()
                if stack:
                    self.complain(
                        "%s returns with %d value(s) left on the operand stack"
                        % (function.name, len(stack)))
                return result
            else:
                raise NotImplementedError(mnemonic)


def main():
    if len(sys.argv) != 2:
        print("usage: jcocosim.py program.casm", file=sys.stderr)
        return 2
    with open(sys.argv[1]) as source:
        functions = parse(source.read())
    if "main" not in functions:
        print("jcocosim: no main/0 to run", file=sys.stderr)
        return 2
    machine = Machine(functions)
    try:
        machine.run(functions["main"])
    except VMError as error:
        # The real VM prints a traceback and stops; the program's remaining
        # output is lost, which is the behavior worth reproducing here.
        print("An Uncaught Exception Occurred: %s" % error, file=sys.stderr)
        return 1
    return 1 if machine.problems else 0


if __name__ == "__main__":
    sys.exit(main())
