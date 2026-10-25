# TPO-WATCHER — Standalone MFA Operator Tool (Stage C2)

A hardened, human-operated, one-shot utility for provisioning the watcher's
authenticated browser session when the portal requires a multi-factor
challenge. **It is completely separate from the qualified watcher
application**: no `app.*` imports, no SQLite access, no notifications, no
scheduling, no daemon.

## What it does

A single bounded transaction:

```
confirm → lock → stage → authenticate (human enters password + 6-digit OTP,
hidden) → serialize → verify in a FRESH browser context → validate structure
→ publish atomically (0600, mtime forced strictly newer) → cleanup → unlock
```

A failed attempt can never modify, delete, or re-timestamp an existing
session file. A successful attempt publishes only after independent
restoration verification.

## Why the mtime bump matters

The qualified watcher's paused-session recovery
(`app/monitoring/watcher.py`, `has_session_file_been_refreshed()`) requires
the session file's mtime to be **strictly newer** than the mtime recorded
when the MFA pause engaged. A replacement within the same filesystem
timestamp tick would be invisible. This tool therefore forces the published
file's mtime strictly beyond the replaced file's mtime
(`force_mtime_strictly_greater`). Interop proof: `test_watcher_interop.py`
scenarios 06–08.

## Security model

| Property | Mechanism |
|---|---|
| Origin safety | Loopback-only allowlist by default; non-loopback requires `--allow-origin` AND `--acknowledge-account-authorization` |
| OTP handling | Single bounded human entry via hidden prompt; no TOTP generation, no seeds, no QR decoding, no retries |
| Logging | Nothing variable is logged; a `SecretScrubber` filter redacts any registered secret that ever reaches a log record |
| Concurrency | `OperatorLock` is KERNEL-managed: `fcntl.flock` on Linux (msvcrt byte-lock for local Windows development) over a persistent lock file; holder death auto-releases at the kernel; the lock file is never deleted and no payload/PID inspection can weaken ownership (the retired rename-reclaim design had a deterministic steal race — proven and replaced; see `test_lock_deterministic.py` A1) |
| Destination safety | Final-component symlink → `SYMLINK_REJECTED`; symlinked ancestor → `DESTINATION_UNSAFE`; re-checked immediately before publish |
| Publication | Transactional: prior-session hard-link backup → 0600 + strictly-newer mtime enforced AND VERIFIED on the staged file → atomic `os.replace` → post-validation with explicit backup recovery; a failed run never deletes or truncates the previous session |
| Bounded runtime | Wall-clock deadline enforced at every phase (`RUNTIME_BOUND_EXCEEDED`) |
| Cancellation | Ctrl+C at any prompt aborts fail-closed; staging removed; lock released |

## Volume-permission compatibility (production topology)

The production data volume root is root:root mode 0755 (Stage A
observation) while this image runs as uid 1000. A uid-1000 process CANNOT
create `/app/data/session` inside it (proven on a real Linux kernel by
`test_linux_fixtures.py` L1). A separately authorized ONE-TIME setup is
therefore required before first live use:

```
docker exec -u 0 tpo-watcher sh -c \
  'mkdir -p /app/data/session && chown 1000:1000 /app/data/session && chmod 0700 /app/data/session'
```

After that setup the coexistence rules hold (proven L2/L3): the root
watcher can always write inside the operator-owned 0700 directory (root
bypasses DAC), and the operator can atomically replace watcher-created
root-owned files because rename is governed by directory ownership. If the
watcher ever creates the directory FIRST as root:root, the same one-time
`chown`/`chmod` corrects it. Do NOT run the companion container as root to
bypass this.

## Layout

| File | Purpose |
|---|---|
| `operator_primitives.py` | Stdlib-only safety core: kernel-managed lock, destination safety, structure validation, transactional publication |
| `mfa_operator.py` | Browser-flow tool + CLI on top of the primitives (only file importing Playwright) |
| `_lock_race_worker.py` | Subprocess worker for cross-process lock tests |
| `requirements.txt` | Single pinned dependency: `playwright==1.63.0` (matches the qualified app) |
| `Dockerfile` | Isolated tooling image (context = `operator_tool/` only; python:3.12-slim, chromium, non-root uid 1000, no ports) |
| `docker-compose.operator.yml` | `qualification` (network=none, offline) and `operator-mfa` (live, authorization-gated) profiles; external volume `tpo-watcher_watcher_data` |
| `test_adversarial_mfa_operator.py` | Adversarial suite (real Chromium + real multi-process races) |
| `test_watcher_interop.py` | 13 checks against the qualified `AuthManager`/`PlacementWatcher` contracts |
| `test_lock_concurrency.py` | Cross-process lock hammering (subprocess races, death recovery) |
| `test_lock_deterministic.py` | Deterministic interleavings incl. the retired-design steal-race proof |
| `test_publication_faults.py` | Fault injection at every publication boundary |
| `test_linux_fixtures.py` | Linux-only volume/POSIX evidence (root-volume denial, 0600/0700 literals, symlinks, flock, mtime precision) |
| `verify_production_build_exclusion.py` | Production build isolation check + runbook rule emission |
| `stage_b_exclusive_place.sh` | Corrected Stage B runbook mechanism (`ln`, not `mv -n`) |
| `stage_b_hash_guarded_rollback.sh` | Hash-guarded Stage B rollback |
| `test_stage_b_exclusive_placement.py` | Offline rehearsal of the placement/rollback scripts (stubbed git/docker) |
| `QUALIFICATION_ARM64.md` | Prepared (not executed) native ARM64 qualification plan |

## Running the offline suites (fictional loopback portals only)

```
python -I -B operator_tool/test_lock_deterministic.py
python -I -B operator_tool/test_lock_concurrency.py
python -I -B operator_tool/test_publication_faults.py
python -I -B operator_tool/test_adversarial_mfa_operator.py
python -I -B operator_tool/test_watcher_interop.py
python -I -B operator_tool/test_stage_b_exclusive_placement.py
python -I -B operator_tool/verify_production_build_exclusion.py
# Linux-only (WSL2/ARM64):
python -I -B operator_tool/test_linux_fixtures.py
```

**Receipt policy:** every suite reports
`EXECUTED / PASSED / FAILED / SKIPPED / DEFERRED_PLATFORM` separately.
Skipped and platform-deferred cases are never counted as passed. On
Windows, POSIX-only cases (symlinks, literal 0600/0700, advisory-lock
payload rewrites, read-only lock dirs) are DEFERRED and MUST execute and
pass on the native Linux/ARM64 qualification, where any deferral fails
the suite. Windows receipts prove logic; POSIX and ARM64 browser proofs
belong to the native qualification (see `QUALIFICATION_ARM64.md`).

## Authorization prerequisites for ANY live use

1. Explicit account-holder authorization (the credentials belong to an
   individual student placement account).
2. Institutional acceptable-use compliance for operator-assisted login.
3. The Stage B compose override approved and placed (session directory
   `/app/data/session`).
4. A separately authorized companion-container execution on the production
   host (see `docker-compose.operator.yml`, profile `operator-mfa`).

No live use is implemented or approved by this repository state.
