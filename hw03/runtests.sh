#!/bin/sh
# ---------------------------------------------------------------------------
# runtests.sh -- regenerate and check the sample outputs.
#
# For each tests/tNN.calc this script
#   1. runs the HW02 interpreter    calc.py       -> tests/*.out  (expected)
#   2. runs the HW03 compiler       calccomp.py   -> tests/*.casm (deliverable)
#   3. executes the compiled program and compares its standard output with
#      the interpreter's, which is the criterion HW03 states.
#
# Step 3 uses the coco command when it is available, and otherwise
# tools/jcocosim.py, the simulator included with this package, which models the
# same behavior (it is cross-checked against the real VM).  Set COCO to choose
# explicitly, e.g.  COCO=coco sh runtests.sh
#
# tests/vmlimits.calc is *not* an assertion: it collects inputs whose output
# cannot agree, because of limits of the JCoCo VM itself (see the README).  Its
# two outputs are regenerated side by side for inspection.
#
# Diagnostics (standard error) are recorded in tests/*.err and tests/*.cerr but
# are not compared: HW03 pins down standard output.
# ---------------------------------------------------------------------------

set -u
cd "$(dirname "$0")"

if [ -z "${COCO:-}" ]; then
    if command -v coco > /dev/null 2>&1; then COCO=coco
    else COCO="python3 tools/jcocosim.py"; fi
fi
echo "using JCoCo implementation: $COCO"

status=0
for input in tests/t*.calc; do
    base=$(basename "$input" .calc)
    python3 calc.py     < "$input" > "tests/$base.out"  2> "tests/$base.err"
    python3 calccomp.py < "$input" > "tests/$base.casm" 2> "tests/$base.cerr"
    $COCO "tests/$base.casm" > "tests/$base.run" 2> "tests/$base.rerr"
    if diff -u "tests/$base.out" "tests/$base.run" > "tests/$base.diff"; then
        rm -f "tests/$base.diff" "tests/$base.run"
        echo "PASS $base"
    else
        echo "FAIL $base  (see tests/$base.diff)"
        status=1
    fi
    if [ -s "tests/$base.rerr" ]; then
        echo "     note: the JCoCo run wrote to stderr; see tests/$base.rerr"
        status=1
    else
        rm -f "tests/$base.rerr"
    fi
    [ -s "tests/$base.cerr" ] || rm -f "tests/$base.cerr"
    [ -s "tests/$base.err" ]  || rm -f "tests/$base.err"
done

# The known, documented divergences: recorded, not asserted.
python3 calc.py     < tests/vmlimits.calc > tests/vmlimits.out  2> /dev/null
python3 calccomp.py < tests/vmlimits.calc > tests/vmlimits.casm 2> tests/vmlimits.cerr
$COCO tests/vmlimits.casm > tests/vmlimits.run 2> tests/vmlimits.rerr
echo "recorded tests/vmlimits.{out,run}: known VM limits, see the README"

# A constant JCoCo cannot hold: the compiler must say so and exit 2, since the
# program it writes would not assemble.  This one *is* asserted.
python3 calccomp.py < tests/badconst.calc > tests/badconst.casm 2> tests/badconst.cerr
if [ $? -eq 2 ] && [ -s tests/badconst.cerr ]; then
    echo "PASS badconst (compiler reported an unassemblable constant, exit 2)"
else
    echo "FAIL badconst (expected exit 2 and a diagnostic)"
    status=1
fi

exit $status
