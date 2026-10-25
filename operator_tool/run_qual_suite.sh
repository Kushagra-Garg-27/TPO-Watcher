#!/bin/sh
# =============================================================================
# Stage C2 qualification step recorder (run-3 correction)
# =============================================================================
# Wraps one qualification command and records its outcome to
# ${RUNNER_TEMP:-/tmp}/qual_state/events.log:
#   started <name> / passed <name> / failed <name> rc=<rc>
#
# RUN-3 CORRECTIONS:
#   1. Failure exit code is captured IMMEDIATELY inside an explicit `else`
#      branch. The previous form read `$?` AFTER the `if ... fi` statement,
#      which under POSIX shell rules can yield the if-statement's status
#      (0) instead of the wrapped command's failure code.
#   2. Receipt writes are fail-closed: if an event cannot be recorded, the
#      recorder exits 125 so qualification can never silently lose a gate
#      outcome.
#   3. The original nonzero exit code is propagated unchanged (1/7/126/127/
#      128+signal ...). A failed command can never produce exit 0 or a
#      `passed` event.
#
# The script is POSIX sh and MUST be invoked as `sh run_qual_suite.sh ...`
# (it intentionally does not depend on an executable git mode).
# =============================================================================

NAME="$1"; shift
if [ -z "$NAME" ] || [ $# -eq 0 ]; then
    echo "QUAL_RECORDER_USAGE_ERROR" >&2
    exit 124
fi

STATE_DIR="${RUNNER_TEMP:-/tmp}/qual_state"
LOG="$STATE_DIR/events.log"

log_event() {
    if ! printf '%s\n' "$1" >> "$LOG" 2>/dev/null; then
        echo "QUAL_RECEIPT_WRITE_FAILED: $NAME" >&2
        exit 125
    fi
}

if ! mkdir -p "$STATE_DIR" 2>/dev/null; then
    echo "QUAL_STATE_DIR_UNAVAILABLE" >&2
    exit 125
fi

log_event "started $NAME"

if "$@"; then
    log_event "passed $NAME"
    exit 0
else
    rc=$?
    log_event "failed $NAME rc=$rc"
    exit "$rc"
fi
