# COS 301 — HW03: A compiler for the extended calculator language

Example solution.  `calccomp.py` reads a program in the extended calculator
language of HW02 on standard input and writes on standard output a JCoCo
assembly language program that, when executed with `coco`, prints what the HW02
interpreter (`calc.py`) prints for the same input.

That equality has been checked by running the real virtual machine, not by
inspection, and it holds exactly for every program in the required (integer)
language.  For the extended language it holds except where the JCoCo virtual
machine cannot represent or print a real number as HW02's Python does; those
cases are enumerated under "Limits of the target machine" below, and the file
`tests/vmlimits.calc` collects them.

## Contents

| File | Description |
|---|---|
| `calccomp.py` | The compiler.  This is the deliverable. |
| `calc.py` | The HW02 interpreter, unchanged; the reference semantics for the tests. |
| `runtests.sh` | Regenerates and checks every sample: interpreter output vs. compiled-program output. |
| `tests/tNN.calc` | Sample inputs. |
| `tests/tNN.casm` | Sample outputs: the compiler's JCoCo assembly. |
| `tests/tNN.out` | Standard output of the interpreter — and, as the tests confirm, of the compiled program under `coco`. |
| `tests/tNN.err`, `tests/tNN.cerr` | Diagnostics from the interpreter and the compiler, where any occur. |
| `tests/vmlimits.*` | Inputs whose output cannot agree, with both outputs recorded side by side. |
| `tools/jcocosim.py` | Testing aid, not part of the assignment: a simulator for the JCoCo subset used here, modelling the real VM's arithmetic and printing. |
| `tools/fuzz.py` | Testing aid: random differential testing against the interpreter. |

## Usage

```
python3 calccomp.py < tests/t01.calc > t01.casm
coco t01.casm
sh runtests.sh                 # uses coco if present, else tools/jcocosim.py
python3 tools/fuzz.py 1 300    # COCO=coco to fuzz against the real VM
```

The compiler needs PLY (`pip install ply`), as HW02 did.  It writes only the
assembly program on standard output; diagnostics and notes go to standard
error.

## Source language

The full HW02 language: the calculator of `calc.py` (O'Reilly, *Lex and Yacc*,
p. 63) extended with real numbers and scientific notation, the `div` `//` and
`mod` `%` operators this assignment requires, the casts `real(e)` and
`floor(e)`, and `real`/`floor` usable as ordinary variable names.  The lexer and
grammar are HW02's, unchanged, so any program HW02 accepts this compiler
accepts.

## Design

The standard compiler phases, one numbered section each in `calccomp.py`.

1. **Lexical analysis** — HW02's lexer verbatim.
2. **Abstract syntax** — `Num`, `Var`, `Neg`, `BinOp`, `Cast`, `Assign`, `Show`.
3. **Syntax analysis** — HW02's grammar with tree-building actions.  As in
   HW02, a lexical or syntax error aborts the current line only.
4. **Static semantics** — one left-to-right walk with a symbol table from
   variable to type.
5. **Code generation** — post-order walk of each expression, a run-time support
   library (5b), real-constant materialization (5c), and the generator (5d).
6. **Assembly output.**
7. **Driver.**

### Why the compiler infers types

Types are not needed to select the arithmetic instructions — JCoCo dispatches
on the run-time type — but they are needed for five things:

* **Reproducing HW02's type errors.**  HW02 requires an operator's operands to
  have the same type; otherwise it writes a diagnostic and the expression
  yields the integer `0`.  Since the language has no control flow, one static
  walk decides this exactly, so the compiler issues the diagnostic at compile
  time and generates the integer `0` in place of the erroneous subexpression.
* **Reproducing HW02's undefined names**, decided the same way; such a name
  never enters `Locals`.
* **Eliding casts that are identities** — `real(e)` for a real `e` and
  `floor(e)` for an integer `e` generate no code beyond `e`.
* **Choosing the right zero for unary minus.**
* **Selecting the right support routine** for `/`, `//` and `%`, which differ
  by operand type (below).

No *source-level* operator is ever applied to operands of two different types,
so the generated code does not depend on JCoCo's coercion rules, which the
specification does not settle.  (The support library does use one mixed
operation on purpose: integer-by-real division, which the machine implements
correctly and which is the only exact division it offers.)

The typing rules are HW02's: `-e` has the type of `e`; `real()` yields a real
and `floor()` an integer; `/` is true division and always yields a real, even
for two integers; every other operator yields its operands' common type.

## Adapting to the target machine

The interesting part of this assignment is that JCoCo is *not* the Python 3
virtual machine it resembles, so several source operations have no correct
single-instruction translation.  Each item below was found by building the VM
(`kentdlee/JCoCo`) and running the compiled programs, and each is covered by a
regression test.

| What HW02 does | What JCoCo does | Fix |
|---|---|---|
| `-7 // 2` is `-4`, `-7 % 2` is `1` (floored) | `PyInt` uses Java's `/` and `%`: `-3` and `-1` (truncated) | `_imod` corrects the remainder's sign; `_idiv` divides the exact multiple `a - (a % b)` | 
| `1 // 0` warns and yields `0`, and the run continues | raises, ending the run, so every later statement's output is lost | each division routine tests its divisor and returns HW02's typed zero |
| `7.5 / 2.0`, `7.5 // 2.0` | `PyFloat` implements no `__truediv__` and no `__floordiv__` at all — a run-time `TypeError` | `_rdiv` converts a whole-number dividend and divides exactly, else multiplies by a reciprocal; `_rdivf` floors that quotient |
| `-7.5 % 2.0` is `0.5` | `PyFloat.__mod__` truncates, through a 32-bit cast | `_rmod` computes `a - b * (a // b)` |
| `floor(-2.5)` is `-3` | no `floor` built-in, no `math`, and `int()` truncates | `_rfloor`/`_floor` adjust the truncated value downward when needed |
| the real `3.14159` | the assembler reads real literals with 32-bit `Float.parseFloat`: `3.141590118408203` | no real literal is ever emitted; see below |

### The run-time support library (section 5b)

`/`, `//` and `%` compile to a call on one of six generated functions, chosen
by operand type; each is emitted only if the program uses it.

| | integers | reals |
|---|---|---|
| `/` | `_ddiv` | `_rdiv` |
| `//` | `_idiv` | `_rdivf` |
| `%` | `_imod` | `_rmod` |

with `_rfloor` (floor of a real, as a real) and `_floor` (as an integer, for
the `floor()` cast) beneath them.  Two properties are worth noting.

* They are written so as to be correct **on either build of JCoCo**.  The
  sign correction in `_imod` cannot fire on a machine whose `%` is already
  floored, and `_idiv` divides an exact multiple, where truncation and flooring
  agree.  So if this course distributes a JCoCo with Python-style `div`/`mod`,
  the same output remains correct.
* The zero-divisor test lives inside the routines, so HW02's recovery costs no
  exception handling: `SETUP_EXCEPT` around every division would have taken
  roughly a dozen instructions apiece.

### Real constants (section 5c)

Because the assembler narrows every real literal to 32 bits, real constants are
not emitted as literals.  A constant is instead materialized from integers:
`repr` gives the digits that denote it exactly, and after normalization it is
`m * 10^e` for some integer mantissa `m`.  The compiler emits

* `m / 10^k` when `e = -k < 0`, or `m / 1` when `e = 0`, or `m / 1` times an
  exactly built power of ten when `e > 0`;
* powers of ten above the 32-bit limit as products of smaller ones, which is
  exact because every power of ten through `10^22` is exact as a double.

Integer division on this machine is correctly rounded, so exactly one rounding
occurs — of the true decimal value — and the result is bit-for-bit the double
HW02's lexer builds.  This covers mantissas up to 2 147 483 647 (about nine or
ten significant digits) and exponents to ±22; `3.14159`, `2e-2`, `1e10`,
`12345.6789` and `9.87654321e15` all now print exactly as HW02 prints them.
Anything wider falls back to a literal, with a warning.

### Code generation templates

| Construct | Code |
|---|---|
| `name = e` | *code for* `e`; `STORE_FAST` *name* |
| `e` (expression statement) | `LOAD_GLOBAL print`; *code for* `e`; `CALL_FUNCTION 1`; `POP_TOP` |
| `e1 + e2`, `-`, `*` | *code for* `e1`; *code for* `e2`; `BINARY_ADD`/`SUBTRACT`/`MULTIPLY` |
| `e1 / e2`, `//`, `%` | `LOAD_GLOBAL` *routine*; *code for* `e1`; *code for* `e2`; `CALL_FUNCTION 2` |
| `-e`, `e` an integer | `LOAD_CONST 0`; *code for* `e`; `BINARY_SUBTRACT` |
| `-e`, `e` a real | *code for* `-1.0`; *code for* `e`; `BINARY_MULTIPLY` |
| `real(e)`, `e` an integer | `LOAD_GLOBAL float`; *code for* `e`; `CALL_FUNCTION 1` |
| `floor(e)`, `e` a real | `LOAD_GLOBAL _floor`; *code for* `e`; `CALL_FUNCTION 1` |
| a real constant | *see above* |
| end of program | `LOAD_CONST 0` (`None`); `RETURN_VALUE` |

**Unary minus.**  Appendix A lists no unary-negation instruction and the
grammar's constants are unsigned, so a negation is built from a binary
operator.  An integer negation is a subtraction from zero, as in the
assignment's own sample output.  A real negation is a multiplication by `-1.0`
instead: `0.0 - r` would turn `-0.0` into `+0.0`, and `-0.0` is reachable
(`-real(x)` with `x` zero is one way).  The VM prints `-0.0` just as Python
does, so the distinction is visible.  In both cases the constant carries the
operand's type.

## Conventions and formatting

The textbook's conventions: zero-based pools private to each function;
`Constants`, `Locals`, `Globals` in that order with empty sections omitted;
`None` as constant 0 so the standard epilogue `LOAD_CONST 0` / `RETURN_VALUE`
returns `None`; the callable pushed below its arguments; every call's result
consumed or popped.  Instructions are indented with operands aligned, as in the
disassembler's output, and a blank line separates the code for consecutive
source statements.  (Blank lines and indentation are ignored by the assembler.)
No comments are emitted: the JCoCo grammar specifies no comment syntax.

The assignment invites improvements on its sample outputs' formatting, and this
compiler's output differs from them in three deliberate ways — all verified to
assemble and run.

1. **`None` is constant 0, not the last constant**, which is the textbook's
   convention and makes the epilogue uniform.
2. **An assignment leaves nothing on the operand stack.**  The samples emit a
   `LOAD_FAST` of the assigned variable after each `STORE_FAST`; those values
   are never consumed and merely accumulate.
3. **`print` is pushed before its argument**, rather than after it followed by
   `ROT_TWO`, saving an instruction per printed statement.

Sample 1 compiles to 16 instructions against the sample output's 19, and sample
2 to 88 against 96, with identical output.

## Limits of the target machine

These are properties of JCoCo, not of the compiler; `tests/vmlimits.calc`
records each one, and `tests/vmlimits.out` and `.run` show the two outputs.

* **Integers are 32 bits and overflow raises.**  `2000000000 + 2000000000` is
  `4000000000` in HW02; on the VM `Math.addExact` throws, the run ends, and the
  remaining statements produce nothing.  A calculator over unbounded integers
  cannot be compiled faithfully to this machine without a bignum library in
  assembly.  An integer *constant* out of range is caught at compile time with
  a warning, since the assembler would reject it with a `NumberFormatException`.
* **Reals print differently.**  `PyFloat.str` uses
  `DecimalFormat("0.0###############")`: at most sixteen fractional digits and
  never exponent notation.  So `0.1 + 0.2` prints `0.3` where HW02 prints
  `0.30000000000000004`, and `1.5e-12` prints as `0.0000000000015`.  Values
  from `1e-4` up to `1e16` — where Python also avoids exponent notation — print
  identically once the constants are exact, which is why the fix above matters.
* **Real division can differ in the last bit.**  When the dividend is not a
  whole number in the machine's integer range, `_rdiv` must form `a * (1/b)`,
  which rounds twice; about one such division in six lands one bit away from
  HW02's.  Correctly rounded division of two arbitrary doubles is precisely the
  operation the machine lacks, so no instruction sequence can do better.  The
  compiler says so once, on standard error, for any program that divides reals.
* **`real()` and `floor()` inherit the 32-bit range**, since they go through
  `int()`, whose Java cast saturates.
* **Diagnostics cannot be reproduced at run time.**  JCoCo offers no standard
  error stream, so the compiled program stays silent where HW02 writes a
  warning; the compiler emits the equivalent diagnostics itself, at compile
  time.  Standard output, which is what the assignment pins down, is unaffected.
* Compile-time diagnostics necessarily appear in a different order from HW02's
  run-time ones (lexical and syntax errors first, then semantic ones).

## Testing

`runtests.sh` compiles each `tests/tNN.calc`, runs it, and compares its standard
output with the interpreter's.  It uses `coco` when present and otherwise
`tools/jcocosim.py`.

The simulator models the target's behavior rather than Python's — 32-bit
integers with checked overflow, truncating integer `//` and `%`, reals with no
division, `DecimalFormat` printing, 32-bit literal parsing — because those
differences are the whole problem here; an idealized simulator passes programs
that the real VM rejects.  It agrees with the real VM on all ten sample files
and on 60 random programs.  It also checks the conventions the generated code
claims to obey: pool indices in range, arities matching, no unintended
mixed-type operation, and an empty operand stack at every `RETURN_VALUE`.

All nine assertions pass on the real VM (JCoCo built from `kentdlee/JCoCo`,
with the JavaFX turtle module stubbed out, which is irrelevant here).
`tools/fuzz.py` extends the comparison to random programs: of 400 programs
mixing both types, all six operators, both casts, nested negations, zero
divisors, undefined names, and type errors, 396 agree exactly and 4 differ only
in the documented real-printing or last-bit cases.

| Sample | Exercises |
|---|---|
| `t01`, `t02` | The assignment's two sample inputs. |
| `t03` | `//` and `%` on integers and reals; `/` on both. |
| `t04` | Reals, scientific notation, `real()`, `floor()` on both signs, elided casts. |
| `t05` | Undefined names, a type mismatch, an illegal character, a syntax error. |
| `t06` | `real`/`floor` as variable names, a variable changing type, nested unary minus, parentheses. |
| `t07` | Every sign combination of `//` and `%`, for integers and reals — the floored-vs-truncated regression. |
| `t08` | Zero divisors of all six kinds, each followed by more statements, checking that no output is lost. |
| `t09` | Real division, including exact whole-number dividends, and exact real constants. |
| `vmlimits` | The divergences above: recorded for inspection, not asserted. |

## Resources

The lexer, grammar, and PLY structure come from `calc.py` (HW02, itself from
O'Reilly's *Lex and Yacc*, p. 63, as extended in class).  The instruction set,
pools, and calling conventions follow Chapter 3 and Appendix A of Lee,
*Foundations of Programming Languages* (JCoCo edition).  The behavior recorded
under "Adapting to the target machine" was read from the JCoCo sources
(`PyInt.java`, `PyFloat.java`, `PyParser.java`) and confirmed by running the
VM.  Everything else — the abstract syntax, static semantics, code generator,
support library, constant materialization, tests, simulator, and fuzz tester —
was written for this assignment.