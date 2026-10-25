#!/bin/sh
# =============================================================================
# Stage C2 qualification step recorder (run-2 receipts correction)
# =============================================================================
# Wraps one qualification command so the final summary step can report
# EXPECTED vs STARTED vs PASSED vs FAILED honestly (the first run's summary
# claimed seven suites despite none executing).
#
# Usage: run_qual_suite.sh <check-name> <command...>
# Appends human-readable, secret-free state lines to
# ${RUNNER_TEMP:-/tmp}/qual_state/events.log:
#   started <name> / passed <name> / failed <name> rc=<rc>
# and propagates the command's exit code.
# =============================================================================
NAME="$1"; shift
STATE_DIR="${RUNNER_TEMP:-/tmp}/qual_state"
mkdir -p "$STATE_DIR"
echo "started $NAME" >> "$STATE_DIR/events.log"
if "$@"; then
    echo "passed $NAME" >> "$STATE_DIR/events.log"
    exit 0
fi
rc=$?
echo "failed $NAME rc=$rc" >> "$STATE_DIR/events.log"
exit $rc
