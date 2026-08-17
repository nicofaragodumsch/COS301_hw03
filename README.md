# COS 301 — HW03: A compiler for the extended calculator language

Example solution.  `calccomp.py` reads a program in the extended calculator
language of HW02 on standard input and writes on standard output a JCoCo
assembly language program that, when executed with `coco`, prints exactly what
the HW02 interpreter (`calc.py`) prints for the same input.

## Contents

| File | Description |
|---|---|
| `calccomp.py` | The compiler.  This is the deliverable. |
| `calc.py` | The HW02 interpreter, unchanged; included as the reference semantics used by the tests. |
| `runtests.sh` | Regenerates and checks every sample: interpreter output vs. compiled-program output. |
| `tests/tNN.calc` | Sample inputs. |
| `tests/tNN.casm` | Sample outputs: the compiler's JCoCo assembly for `tNN.calc`. |
| `tests/tNN.out` | The standard output of the interpreter — and, as the tests confirm, of the compiled program. |
| `tests/tNN.err`, `tests/tNN.cerr` | Diagnostics from the interpreter and from the compiler, where any occur. |
| `tools/jcocosim.py` | A testing aid, not part of the assignment: a small simulator for the JCoCo subset used here, so the samples can be checked where `coco` is unavailable. |
| `tools/fuzz.py` | A testing aid: generates random calculator programs and compares interpreter output with compiled-program output. |

## Usage

```
python3 calccomp.py < tests/t01.calc > t01.casm
coco t01.casm
sh runtests.sh                 # uses coco if present, else tools/jcocosim.py
```

The compiler requires PLY (`pip install ply`), as HW02 did.  It writes only
the assembly program on standard output; all diagnostics go to standard error.

## Source language

The full HW02 language, which is the calculator of `calc.py`
(O'Reilly, *Lex and Yacc*, p. 63) extended with

* real numbers and scientific notation,
* the `div` and `//` and `mod` `%` operators required by this assignment,
* the casts `real(e)` and `floor(e)`,
* `real` and `floor` usable as ordinary variable names.

One statement per line; a statement is either `name = expression` or an
expression, which is printed.  The lexer and the grammar are HW02's, unchanged;
only the semantic actions differ, so any program HW02 accepts this compiler
also accepts.

## Design

The program is organized as the standard compiler phases, one numbered section
each in `calccomp.py`.

1. **Lexical analysis** — HW02's lexer verbatim.
2. **Abstract syntax** — `Num`, `Var`, `Neg`, `BinOp`, `Cast`, `Assign`, `Show`.
3. **Syntax analysis** — HW02's grammar with tree-building actions in place of
   the evaluating ones.  As in HW02, a lexical or syntax error aborts the
   current line only; compilation continues with the next one.
4. **Static semantics** — one left-to-right walk over the statements carrying a
   symbol table from variable to type.  The calculator language has no control
   flow, so this walk decides *exactly* the two questions HW02 answers at run
   time: whether a name is defined, and whether the operands of an operator have
   the same type.
5. **Code generation** — the usual post-order walk of each expression: code for
   the left operand, code for the right operand, then the instruction.  Pools
   are filled on demand and printed once the code is complete.
6. **Assembly output.**
7. **Driver.**

### Why the compiler infers types

Types are not needed to select the arithmetic instructions — JCoCo's
`BINARY_ADD` and the rest dispatch on the run-time type, exactly as Python
does — but they are needed for four things:

* **Reproducing HW02's type errors.**  HW02 requires both operands of an
  operator to have the same type; otherwise it writes a diagnostic and the
  expression yields the integer `0`.  Because types are known statically, the
  compiler issues that diagnostic at compile time and generates `LOAD_CONST` of
  the integer `0` for the erroneous subexpression, so the compiled program's
  standard output still agrees with the interpreter's.
* **Reproducing HW02's undefined names.**  A name used before it is ever
  assigned gets a diagnostic and the integer `0`, again decided statically.
  Such a name therefore never enters `Locals`.
* **Eliding casts that are identities.**  `real(e)` for a real `e`, and
  `floor(e)` for an integer `e`, generate no code at all beyond `e`.
* **Choosing the right zero for unary minus** (below).

A consequence worth stating: **no instruction in the generated program is ever
applied to operands of two different types.**  The generated code therefore
does not depend on whether JCoCo coerces mixed operands, a question the
specification does not settle.

The typing rules are HW02's: a literal has the type the lexer gives it; `-e`
has the type of `e`; `real()` yields a real and `floor()` an integer; `/` is
true division and always yields a real, even for two integers; every other
operator yields the common type of its operands.

### Code generation templates

| Construct | Code |
|---|---|
| `name = e` | *code for* `e`; `STORE_FAST` *name* |
| `e` (expression statement) | `LOAD_GLOBAL print`; *code for* `e`; `CALL_FUNCTION 1`; `POP_TOP` |
| `e1 op e2` | *code for* `e1`; *code for* `e2`; `BINARY_ADD`/`SUBTRACT`/`MULTIPLY`/`TRUE_DIVIDE`/`FLOOR_DIVIDE`/`MODULO` |
| `-e`, `e` an integer | `LOAD_CONST 0`; *code for* `e`; `BINARY_SUBTRACT` |
| `-e`, `e` a real | `LOAD_CONST 0.0`; `LOAD_CONST 1.0`; `BINARY_SUBTRACT`; *code for* `e`; `BINARY_MULTIPLY` |
| `real(e)`, `e` an integer | `LOAD_GLOBAL float`; *code for* `e`; `CALL_FUNCTION 1` |
| `floor(e)`, `e` a real | `LOAD_GLOBAL _floor`; *code for* `e`; `CALL_FUNCTION 1` |
| end of program | `LOAD_CONST 0` (`None`); `RETURN_VALUE` |

**Unary minus.**  Appendix A lists no unary-negation instruction and the
grammar's constants are unsigned, so a negation must be built from a binary
operator.  An integer negation is a subtraction from zero, as in the
assignment's own sample output.  A real negation is instead a multiplication by
`-1.0` (itself `0.0 - 1.0`), two instructions more: `0.0 - r` would quietly
turn `-0.0` into `+0.0`, and `-0.0` is reachable — `-real(x)` where `x` is zero
is one way — so the shorter form would not always print what HW02 prints.  In
both cases the constant carries the type of the operand, so the instruction
never sees mixed types.

**`floor` of a real.**  JCoCo has no `floor` built-in and cannot import
`math`, so the compiler emits a run-time support function, `_floor/1`, and
calls it.  It is generated only for programs that need it.  `int()` truncates
toward zero, which is already the floor for a non-negative argument; for a
negative argument with a fractional part the truncated value is one too large:

```
t = int(x)
if float(t) > x: t = t - 1
return t
```

The comparison is made in floating point so that its operands, too, have the
same type.

## Conventions and formatting

The textbook's conventions are followed: zero-based pools private to each
function; `Constants`, `Locals`, `Globals` in that order with empty sections
omitted entirely; `None` as constant 0 so that the standard epilogue
`LOAD_CONST 0` / `RETURN_VALUE` returns `None`; the callable pushed below its
arguments; every call's result consumed or popped.  Instructions are indented
and operands aligned in a column, as in the disassembler's output, with a blank
line between the code for consecutive source statements.  (Blank lines and
indentation are ignored by the assembler; the language is not line-oriented.)
No comments are emitted, because the JCoCo grammar specifies no comment syntax.

The assignment invites improvements on the formatting of its sample outputs,
and this compiler's output differs from them in three deliberate ways.

1. **`None` is constant 0, not the last constant.**  The samples place `None`
   last.  Index 0 is the textbook's convention and makes the epilogue uniform.
2. **An assignment leaves nothing on the operand stack.**  The samples emit a
   `LOAD_FAST` of the assigned variable after each `STORE_FAST`; those values
   are never consumed and merely accumulate on the operand stack.
3. **`print` is pushed before its argument**, rather than after it followed by
   `ROT_TWO`, saving an instruction per printed statement and matching the
   textbook's calling discipline.

Sample 1 compiles to 16 instructions here against the sample output's 19, and
sample 2 to 88 against 96 — one `ROT_TWO` saved per printed statement and per
unary minus, and one stray `LOAD_FAST` per assignment — with identical output.

## Assumptions about JCoCo

The specification is silent on a few points, and the generated code assumes
JCoCo behaves as the Python 3 VM it mimics:

* `BINARY_TRUE_DIVIDE` on two integers yields a real (this is why Appendix A
  distinguishes it from `BINARY_FLOOR_DIVIDE`);
* `int()` on a real truncates toward zero, and `float()` on an integer is exact
  within the range of a real;
* `BINARY_FLOOR_DIVIDE` and `BINARY_MODULO` round toward negative infinity, as
  Python's do, so that `-17 // 5` is `-4` and `-17 % 5` is `3`;
* `print` converts a number as Python's `str` does, and JCoCo's integers, like
  Python's, are not limited to a fixed width.

Where the last two fail, the divergence would be in JCoCo's arithmetic and
printing, not in the generated code.

## Known limitations

* **Run-time arithmetic errors are not intercepted.**  HW02 recovers from
  division or `mod` by zero (a diagnostic, the expression yields zero, and
  execution continues); the compiled program would instead raise a JCoCo
  exception and stop.  Since the assignment's input is a valid program, this
  does not affect the required equality of outputs.  It could be repaired
  without changing the rest of the design by wrapping each division in the
  `SETUP_EXCEPT` idiom, at the cost of roughly a dozen extra instructions per
  division.
* **Real constants outside JCoCo's range.**  An input such as `1e400` denotes
  an infinity in HW02; there is no JCoCo literal for it, so the compiler warns
  and substitutes `0.0`.
* Real constants are written in plain decimal, expanded exactly with `Decimal`,
  because the grammar's `Float` is a plain decimal numeral.  For inputs such as
  `1.0e-9` this is exact but long.
* Compile-time diagnostics necessarily appear in a different order from HW02's
  run-time ones (all lexical and syntax errors first, then all semantic ones).
  Standard output, which is what the assignment requires to agree, is
  unaffected.

## Testing

`runtests.sh` compiles each `tests/*.calc`, runs the result, and compares its
standard output with the interpreter's, which is the correctness criterion the
assignment states.  It uses `coco` when present and otherwise
`tools/jcocosim.py`, a simulator of the dozen instructions used here.  Besides
executing, the simulator checks the conventions the generated code claims to
obey and reports a failure if any is violated: pool indices in range, arities
matching at every call, no binary instruction applied to two different types,
and an empty operand stack at every `RETURN_VALUE`.

All six samples pass, with no leftover stack values and no mixed-type
operations.  `tools/fuzz.py` extends the same comparison to randomly generated
programs; 1500 of them (five seeds of 300, mixing both types, all six
operators, both casts, nested negations, undefined names, and type errors, but
no zero divisors) agree with the interpreter exactly.  The one disagreement
this uncovered — the sign of zero under negation — is what motivated the real
negation template above.

The samples exercise:

| Sample | Exercises |
|---|---|
| `t01` | The assignment's sample input 1. |
| `t02` | The assignment's sample input 2. |
| `t03` | `//` and `%` on integers and on reals, including negative operands. |
| `t04` | Reals, scientific notation, `real()`, `floor()` on both signs, elided casts. |
| `t05` | Undefined names, a type mismatch, an illegal character, a syntax error. |
| `t06` | `real` and `floor` as variable names, a variable changing type, nested unary minus, parentheses. |

## Resources

The lexer, the grammar, and the general PLY structure come from `calc.py`
(HW02, itself from O'Reilly's *Lex and Yacc*, p. 63, as extended in class).
The JCoCo instruction set, pools, and calling conventions follow Chapter 3 and
Appendix A of Lee, *Foundations of Programming Languages* (JCoCo edition).
Everything else — the abstract syntax, the static semantics, the code
generator, the `_floor` support function, the tests, and the simulator — was
written for this assignment.