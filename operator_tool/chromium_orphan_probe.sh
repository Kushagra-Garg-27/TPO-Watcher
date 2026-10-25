#!/bin/sh
# =============================================================================
# Stage C2 container gate: orphaned-Chromium detection (run-2 correction)
# =============================================================================
# Replaces the previous `pgrep -f chromium` probe, which can match its own
# wrapping `sh -c` command line (the string "chromium" appears in the
# checker's own arguments) and produce a false positive.
#
# This probe inspects ONLY each process's comm (kernel process name, derived
# from the executable, max 15 chars) under /proc. The checker itself runs as
# `sh`/`python`, so its own command line can never produce a match regardless
# of the strings it contains.
#
# Exit 0 = no browser process remains (clean).
# Exit 1 = orphaned browser process detected (its comm is printed).
# Exit 2 = /proc inspection unavailable (fail closed).
# =============================================================================
ORPHAN=""
for comm_file in /proc/[0-9]*/comm; do
    [ -r "$comm_file" ] || continue
    IFS= read -r comm < "$comm_file" 2>/dev/null || continue
    case "$comm" in
        chrome|chromium|headless_shell|chromium_headle*|chrome_crashpad|chromium_crashpad)
            ORPHAN="$comm"
            break
            ;;
    esac
done

if [ ! -d /proc/self ]; then
    echo "ORPHAN_PROBE_UNAVAILABLE"
    exit 2
fi

if [ -n "$ORPHAN" ]; then
    echo "ORPHAN_BROWSER_PROCESS: $ORPHAN"
    exit 1
fi
echo "CHROMIUM_CLEAN"
exit 0
