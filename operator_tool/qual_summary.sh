#!/bin/sh
# =============================================================================
# Stage C2 qualification completeness summary (run-3 correction)
# =============================================================================
# usage: sh qual_summary.sh <events.log> <expected-gate>...
#
# Enforces receipt integrity. A gate is VALID only when its event stream in
# the log contains EXACTLY ONE `started` event followed by EXACTLY ONE
# `passed` event, with no `failed` event and no conflicting duplicates.
# ANY anomaly - a failed event, a started-without-terminal event, a missing
# gate, duplicate/conflicting terminal events, malformed lines, unknown
# gate names, or a missing/empty log - makes the summary exit NONZERO
# (printing QUALIFICATION_INCOMPLETE). Only a fully valid stream for every
# expected gate exits 0 and prints ALL_GATES_SATISFIED_AND_EXECUTED.
#
# Output is sanitized: gate names and counts only; no paths, URLs or data.
# POSIX sh + awk; invoked via `sh` (no executable-mode dependency).
# =============================================================================

LOG="${1:?usage: qual_summary.sh <events.log> <expected-gate>...}"
[ "$#" -ge 2 ] || { echo "QUAL_SUMMARY_USAGE_ERROR"; exit 2; }
shift

if [ ! -f "$LOG" ]; then
    echo "expected_gates=$#"
    echo "gates_passed=0 gates_failed=0 gates_incomplete=0 gates_missing=$# corrupt_lines=0"
    echo "QUALIFICATION_INCOMPLETE (no events log)"
    exit 1
fi

awk -v expected="$*" '
BEGIN {
    n = split(expected, exp_name, " ")
    for (i = 1; i <= n; i++) {
        known[exp_name[i]] = 1
        started[exp_name[i]] = 0
        passed[exp_name[i]] = 0
        failed[exp_name[i]] = 0
    }
    corrupt = 0
}
{
    if ($1 == "started" && $2 in known && NF == 2) { started[$2]++ }
    else if ($1 == "passed" && $2 in known && NF == 2) { passed[$2]++ }
    else if ($1 == "failed" && $2 in known && NF == 3 && $3 ~ /^rc=[0-9]+$/) { failed[$2]++ }
    else { corrupt++ }
}
END {
    p = 0; f = 0; inc = 0; miss = 0
    for (i = 1; i <= n; i++) {
        g = exp_name[i]
        if (started[g] == 1 && passed[g] == 1 && failed[g] == 0) { p++ }
        else if (started[g] == 0 && passed[g] == 0 && failed[g] == 0) { miss++ }
        else if (failed[g] > 0 || passed[g] > 1 || started[g] > 1) { f++ }
        else { inc++ }   # e.g. started once, no terminal event (truncated)
    }
    printf "expected_gates=%d\n", n
    printf "gates_passed=%d gates_failed=%d gates_incomplete=%d gates_missing=%d corrupt_lines=%d\n", p, f, inc, miss, corrupt
    if (p == n && f == 0 && inc == 0 && miss == 0 && corrupt == 0) {
        print "ALL_GATES_SATISFIED_AND_EXECUTED"
        ok = 1
    } else {
        print "QUALIFICATION_INCOMPLETE"
    }
    exit (ok ? 0 : 1)
}' "$LOG"
exit $?
