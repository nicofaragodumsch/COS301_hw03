"""Random differential test: interpreter stdout vs compiled-program stdout.

A testing aid, not part of the homework.  For each randomly generated
calculator program it compares the standard output of calc.py (HW02) with the
standard output of the program calccomp.py produces for it, executed by JCoCo.

    python3 tools/fuzz.py [seed] [count]

Set COCO to the real virtual machine (e.g. COCO=coco) to test against it;
otherwise tools/jcocosim.py is used, which models the same behavior.

Zero divisors are generated on purpose: HW02 recovers from them, and so must
the compiled program.  Mismatches are classified, because two classes of
divergence are inherent to the target and documented in the README: JCoCo
prints reals with DecimalFormat rather than Python's repr, and it has no real
division, so a quotient can differ in its last bit.
"""
import os
import random
import subprocess
import sys

HW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COCO = os.environ.get("COCO", "python3 tools/jcocosim.py")


def gen_expr(depth, names, rng):
    kind = "num" if depth <= 0 else rng.choice(
        ["num", "num", "name", "bin", "bin", "neg", "cast", "paren"])
    if kind == "num":
        return rng.choice([str(rng.randint(0, 999)),
                           "%.3f" % rng.uniform(0, 100),
                           "%de%d" % (rng.randint(1, 9), rng.randint(1, 3)),
                           "0", "0.0"])
    if kind == "name":
        return rng.choice(names) if names else str(rng.randint(0, 9))
    if kind == "neg":
        return "-" + gen_expr(depth - 1, names, rng)
    if kind == "paren":
        return "(" + gen_expr(depth - 1, names, rng) + ")"
    if kind == "cast":
        return rng.choice(["real", "floor"]) + "(" \
            + gen_expr(depth - 1, names, rng) + ")"
    op = rng.choice(["+", "-", "*", "/", "//", "%"])
    return "%s %s %s" % (gen_expr(depth - 1, names, rng), op,
                         gen_expr(depth - 1, names, rng))


def gen_program(rng):
    names, lines = [], []
    for _ in range(rng.randint(1, 6)):
        if rng.random() < 0.5 or not names:
            name = rng.choice(["a", "b", "c", "real", "floor", "x_1"])
            lines.append("%s = %s"
                         % (name, gen_expr(rng.randint(0, 3), names, rng)))
            if name not in names:
                names.append(name)
        else:
            lines.append(gen_expr(rng.randint(0, 3), names, rng))
    return "\n".join(lines) + "\n"


def run(command, stdin=None):
    return subprocess.run(command, shell=True, input=stdin,
                          capture_output=True, text=True, cwd=HW)


def classify(want, got):
    """Is this mismatch one of the documented target-VM limitations?"""
    if all("." not in line and "e" not in line for line in want.splitlines()):
        return "INTEGER MISMATCH (unexpected)"
    for w, g in zip(want.splitlines(), got.splitlines()):
        if w == g:
            continue
        try:
            w, g = float(w), float(g)
        except ValueError:
            return "REAL MISMATCH (unexpected)"
        if abs(w - g) > 1e-9 * max(1.0, abs(w)):
            return "REAL MISMATCH (unexpected)"
    return "real printing or last-bit division (documented)"


def main():
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    rng = random.Random(seed)
    print("JCoCo implementation: %s" % COCO)
    tally = {}
    shown = 0
    for trial in range(count):
        source = gen_program(rng)
        want = run("python3 calc.py", source).stdout
        with open("/tmp/fuzz.casm", "w") as out:
            out.write(run("python3 calccomp.py", source).stdout)
        vm = run("%s /tmp/fuzz.casm" % COCO)
        if want == vm.stdout and not vm.stderr:
            kind = "agree"
        elif vm.stderr:
            kind = "VM error: " + vm.stderr.strip().splitlines()[0][:60]
        else:
            kind = classify(want, vm.stdout)
        tally[kind] = tally.get(kind, 0) + 1
        if kind != "agree" and "documented" not in kind and shown < 5:
            shown += 1
            print("--- trial %d ---\n%sHW02:  %r\nJCoCo: %r"
                  % (trial, source, want, vm.stdout))
    for kind, n in sorted(tally.items(), key=lambda item: -item[1]):
        print("%5d  %s" % (n, kind))


main()
