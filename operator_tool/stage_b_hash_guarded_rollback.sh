#!/usr/bin/env bash
# =============================================================================
# STAGE B - HASH-GUARDED OVERRIDE ROLLBACK (corrected mechanism, 2026-10-26)
# =============================================================================
# Removes the Stage B override ONLY if the on-disk file still hashes to the
# exact approved Stage B digest. Any other content (another operator's file,
# an edited file, a replaced file) is left untouched and reported.
#
# Exit codes:
#   0  rollback completed      20 file absent (nothing to do)
#   21 hash mismatch (kept)    22 wrong current HEAD (refusing)
# =============================================================================
set -euo pipefail

TARGET_DIR="${STAGE_B_TARGET_DIR:-/home/ec2-user/TPO-Watcher}"
EXPECTED_HEAD="${STAGE_B_EXPECTED_HEAD:-053b152b37d8aea8d5b4c74606765cdc73565136}"
APPROVED_SHA256="${STAGE_B_OVERRIDE_SHA256:-09f2b3a16e8c308ce8aaf9f001c64af23615f6b1c1971e7f46326053c5c885bb}"
OVERRIDE_NAME="docker-compose.override.yml"

cd "$TARGET_DIR"

CURRENT_HEAD="$(git rev-parse HEAD)"
if [ "$CURRENT_HEAD" != "$EXPECTED_HEAD" ]; then
    echo "ABORT: git HEAD mismatch (got ${CURRENT_HEAD:0:12}) - refusing rollback" >&2
    exit 22
fi

if [ ! -e "$OVERRIDE_NAME" ]; then
    echo "NOTHING_TO_ROLL_BACK"
    exit 20
fi

CURRENT_SHA="$(sha256sum "$OVERRIDE_NAME" | awk '{print $1}')"
if [ "$CURRENT_SHA" != "$APPROVED_SHA256" ]; then
    echo "KEPT: $OVERRIDE_NAME does not match the approved Stage B digest - not removing" >&2
    exit 21
fi

rm -f "$OVERRIDE_NAME"
echo "STAGE_B_OVERRIDE_ROLLED_BACK"
docker compose -f docker-compose.yml config >/dev/null 2>&1 \
    && echo "Effective configuration reverted to base (STATE_FILE=/app/data/playwright_state.json)" || true
exit 0
