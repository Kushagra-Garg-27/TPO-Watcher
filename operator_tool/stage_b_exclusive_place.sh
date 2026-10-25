#!/usr/bin/env bash
# =============================================================================
# STAGE B - EXCLUSIVE OVERRIDE PLACEMENT (corrected mechanism, 2026-10-26)
# =============================================================================
# Replaces the earlier `mv -n`-based procedure. Offline evidence (Git Bash,
# GNU coreutils): `mv -n src existing_dest` exits 0 while placing NOTHING -
# a script gating on exit status would report success on a lost race.
# The corrected mechanism uses `ln` (link(2)): creating a hard link fails
# with a nonzero exit status when the destination already exists, giving a
# genuinely exclusive, fail-on-collision placement with reliable reporting.
#
# Target platform: Amazon Linux 2023 (GNU coreutils). Offline rehearsal on
# Windows Git Bash exercises the same logic with stubbed git/docker.
#
# Overridable inputs (for offline rehearsal ONLY; production uses defaults):
#   STAGE_B_TARGET_DIR     default: /home/ec2-user/TPO-Watcher
#   STAGE_B_EXPECTED_HEAD  default: 053b152b37d8aea8d5b4c74606765cdc73565136
#   STAGE_B_OVERRIDE_SHA256  default below (sha256 of the exact override bytes)
#   STAGE_B_CONTAINER      default: tpo-watcher
#
# Exit codes:
#   0  placed and verified           10 HEAD precondition failed
#   11 destination already exists   12 staged-content hash mismatch
#   13 compose config parse failed  14 compose allowlist validation failed
#   15 exclusive placement lost race 16 post-placement hash mismatch (rolled back)
# =============================================================================
set -euo pipefail

TARGET_DIR="${STAGE_B_TARGET_DIR:-/home/ec2-user/TPO-Watcher}"
EXPECTED_HEAD="${STAGE_B_EXPECTED_HEAD:-053b152b37d8aea8d5b4c74606765cdc73565136}"
APPROVED_SHA256="${STAGE_B_OVERRIDE_SHA256:-09f2b3a16e8c308ce8aaf9f001c64af23615f6b1c1971e7f46326053c5c885bb}"
CONTAINER="${STAGE_B_CONTAINER:-tpo-watcher}"
OVERRIDE_NAME="docker-compose.override.yml"

cd "$TARGET_DIR"

TMP_OVERRIDE=""
cleanup() {
    if [ -n "$TMP_OVERRIDE" ] && [ -e "$TMP_OVERRIDE" ]; then
        rm -f "$TMP_OVERRIDE" || true
    fi
}
trap cleanup EXIT

# --- 1. Precondition: trusted git identity of the host checkout ----------
CURRENT_HEAD="$(git rev-parse HEAD)"
if [ "$CURRENT_HEAD" != "$EXPECTED_HEAD" ]; then
    echo "ABORT: git HEAD mismatch (got ${CURRENT_HEAD:0:12}, expected ${EXPECTED_HEAD:0:12})" >&2
    exit 10
fi

# --- 2. Precondition: destination must not exist ---------------------------
if [ -e "$OVERRIDE_NAME" ]; then
    echo "ABORT: $OVERRIDE_NAME already exists in $TARGET_DIR" >&2
    exit 11
fi

# --- 3. Stage the approved content into a private temp file ----------------
TMP_OVERRIDE="$(mktemp "$TARGET_DIR/.docker-compose.override.XXXXXX.tmp")"
cat > "$TMP_OVERRIDE" <<'EOF'
services:
  watcher:
    environment:
      - STATE_FILE=/app/data/session/playwright_state.json
EOF
chmod 0644 "$TMP_OVERRIDE"

# --- 4. Exact approved hash of the staged bytes ----------------------------
ACTUAL_SHA="$(sha256sum "$TMP_OVERRIDE" | awk '{print $1}')"
if [ "$ACTUAL_SHA" != "$APPROVED_SHA256" ]; then
    echo "ABORT: staged content hash mismatch (review approved digest)" >&2
    exit 12
fi

# --- 5. Offline effective-configuration validation (allowlisted) -----------
if ! docker compose -f docker-compose.yml -f "$TMP_OVERRIDE" config >/dev/null 2>&1; then
    echo "ABORT: effective compose configuration failed to parse" >&2
    exit 13
fi
docker compose -f docker-compose.yml -f "$TMP_OVERRIDE" config --format json | python3 -c '
import sys, json
cfg = json.load(sys.stdin)
services = cfg.get("services", {})
assert set(services) == {"watcher", "caddy"}, "service set changed"
env = services.get("watcher", {}).get("environment", {})
assert env.get("STATE_FILE") == "/app/data/session/playwright_state.json", "STATE_FILE"
assert env.get("DB_PATH") == "/app/data/watcher.sqlite", "DB_PATH"
' || { echo "ABORT: allowlisted compose validation failed" >&2; exit 14; }

# --- 6. EXCLUSIVE placement: ln fails reliably when destination exists -----
if ln "$TMP_OVERRIDE" "$OVERRIDE_NAME" 2>/dev/null; then
    rm -f "$TMP_OVERRIDE"
    TMP_OVERRIDE=""
else
    echo "ABORT: exclusive placement failed - destination appeared concurrently" >&2
    exit 15
fi

# --- 7. Post-placement hash guard (rollback on mismatch) -------------------
PLACED_SHA="$(sha256sum "$OVERRIDE_NAME" | awk '{print $1}')"
if [ "$PLACED_SHA" != "$APPROVED_SHA256" ]; then
    rm -f "$OVERRIDE_NAME"
    echo "ABORT+ROLLED BACK: placed file hash mismatch" >&2
    exit 16
fi

echo "STAGE_B_OVERRIDE_PLACED_EXCLUSIVELY"
stat -c "File: %n | Mode: %a | Size: %s bytes" "$OVERRIDE_NAME" 2>/dev/null \
    || ls -l "$OVERRIDE_NAME"

# --- 8. Container invariance evidence (informational, non-mutating) --------
docker inspect "$CONTAINER" --format 'Container: {{.State.Status}} (placement is filesystem-only; no restart performed)' || true

exit 0
