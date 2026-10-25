"""
Stage C2 operator tooling - stdlib-only safety primitives
=========================================================
Kernel-managed exclusive locking, destination safety, storage-state
structure validation, timestamp forcing, and atomic session-publication
mechanics. Deliberately free of third-party imports (no Playwright) so the
security-critical primitives can be qualified on a bare Linux fixture.

Locking design (2026-10-26 revision)
------------------------------------
Earlier revisions used an O_EXCL-created lock file with payload-based
staleness and rename-based stale reclaim. That design has a narrow but real
race: between one process reading a stale payload and its rename, a second
process can legitimately reclaim and re-acquire, after which the first
process's rename steals the NEW holder's lock - breaking exclusivity.

This revision replaces the whole reclaim path with kernel-managed locking:

- POSIX:   ``fcntl.flock(fd, LOCK_EX | LOCK_NB)`` on a persistent lock file
- Windows: ``msvcrt.locking(fd, LK_NBLCK, 1)`` byte-range lock (local
           development only; production targets Linux)

Consequences:
- A held lock can only belong to a LIVE holder (the kernel drops the lock
  when the owning process exits) - stale recovery is automatic and there is
  no stale-check/rename race surface at all.
- The lock file is NEVER deleted by acquire/release, so no process can ever
  unlink or rename another holder's lock. Lock files are small persistent
  artifacts next to the target (documented).
- Payload written after acquisition (pid/host/token/time) is diagnostic
  only; ownership is the kernel lock itself, so PID reuse cannot fool it.
- Scope: correct across processes AND across containers sharing one host
  kernel (Docker named volume on a single EC2 host). Cross-kernel mounts
  (e.g. NFS) are out of scope and must not be used.

Publications are fail-closed: the prior session file is hard-link backed up
inside the staging directory before replacement, and any post-replacement
validation failure restores that backup (explicit recovery), never deleting
or truncating state ad hoc.
"""

import errno
import json
import logging
import os
import socket
import stat as stat_module
import time
import uuid
from typing import Optional, Tuple

logger = logging.getLogger("mfa_operator_tool")


class SecretScrubber(logging.Filter):
    """Defensive logging filter: replaces registered secret values with [REDACTED]."""

    def __init__(self):
        super().__init__()
        self._secrets = set()

    def register(self, value: Optional[str]) -> None:
        if value and len(str(value)) >= 4:
            self._secrets.add(str(value))

    def unregister(self, value: Optional[str]) -> None:
        try:
            self._secrets.discard(str(value))
        except Exception:
            pass

    def clear(self) -> None:
        self._secrets.clear()

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            redacted = message
            for secret in self._secrets:
                if secret in redacted:
                    redacted = redacted.replace(secret, "[REDACTED]")
            if redacted != message:
                record.msg = redacted
                record.args = ()
        except Exception:
            pass
        return True

# The published mtime must exceed the replaced file's mtime by a visible
# margin so float comparisons and coarse filesystem timestamps can never
# observe equality (the qualified watcher's pause recovery requires a
# STRICTLY newer mtime; see has_session_file_been_refreshed()).
MTIME_EPSILON_SECONDS = 0.001


def is_process_alive(pid: int) -> bool:
    """
    Platform-aware liveness probe (diagnostics only - locking no longer
    depends on it).

    Never uses os.kill on Windows: signal 0 maps to CTRL_C_EVENT there and
    can disrupt console processes. On Windows we open a query-only handle;
    on POSIX we use the standard signal-0 probe, treating EPERM as alive.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    if not hasattr(os, "kill"):
        return True
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


class OperatorLock:
    """
    Kernel-managed exclusive interprocess lock over a persistent lock file.

    ``timeout_seconds`` is accepted for interface compatibility with earlier
    revisions and is deliberately unused: holder death releases the lock at
    the kernel, so age-based staleness no longer exists. Exclusive ownership
    is never weakened - a lock held by a live process can never be taken
    over by another process, whatever the age or payload of the lock file.
    """

    def __init__(self, target_path: str, timeout_seconds: float = 600.0):
        self.target_path = os.path.abspath(target_path)
        self.lock_path = f"{self.target_path}.lock"
        self.timeout_seconds = timeout_seconds
        self.token = uuid.uuid4().hex
        self.is_acquired = False
        self._fd: Optional[int] = None

    # -- platform lock primitives --------------------------------------

    @staticmethod
    def _lock_fd(fd: int) -> bool:
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError as exc:
            # Contention surfaces as EWOULDBLOCK/EAGAIN/EACCES/EDEADLK;
            # ANY failure fails closed (never a false "acquired").
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES, errno.EDEADLK):
                return False
            logger.error("Kernel lock acquisition failed (%s).", type(exc).__name__)
            return False
        except Exception:
            logger.error("Kernel lock acquisition failed unexpectedly.")
            return False

    @staticmethod
    def _unlock_fd(fd: int) -> None:
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass

    # -- public API ------------------------------------------------------

    def acquire(self) -> bool:
        if self.is_acquired:
            return True
        lock_dir = os.path.dirname(self.lock_path)
        try:
            if lock_dir:
                os.makedirs(lock_dir, mode=0o700, exist_ok=True)
            fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError:
            logger.error("Could not open operator lock file (permission or path failure).")
            return False

        if not self._lock_fd(fd):
            try:
                os.close(fd)
            except OSError:
                pass
            logger.error("Operator lock is held by another live process.")
            return False

        self._fd = fd
        self.is_acquired = True
        # Diagnostic payload only; the kernel lock is the ownership proof.
        try:
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            payload = json.dumps({
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "time": time.time(),
                "token": self.token,
            }).encode("utf-8")
            os.write(fd, payload)
            os.fsync(fd)
        except OSError:
            pass  # diagnostics never gate correctness
        return True

    def release(self) -> None:
        """Unlocks and closes our own descriptor. Never touches the file."""
        if not self.is_acquired or self._fd is None:
            return
        try:
            self._unlock_fd(self._fd)
        finally:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
            self.is_acquired = False

    def __enter__(self):
        if not self.acquire():
            raise BlockingIOError("Could not acquire exclusive operator lock")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    def __del__(self):  # best-effort descriptor hygiene
        try:
            self.release()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Destination safety and storage-state validation
# ---------------------------------------------------------------------------

def validate_destination_safety(target_path: str) -> Tuple[bool, str, str]:
    """
    Rejects destinations whose final component or any existing ancestor
    directory is a symlink. Returns (is_safe, reason_code, detail).
    """
    target = os.path.abspath(target_path)
    if not os.path.basename(target):
        return False, "DESTINATION_UNSAFE", "empty-basename"
    if os.path.islink(target):
        return False, "SYMLINK_REJECTED", "final-component-is-symlink"
    current = os.path.dirname(target)
    depth = 0
    while current and depth < 64:
        if os.path.islink(current):
            return False, "DESTINATION_UNSAFE", f"ancestor-is-symlink:{os.path.basename(current)}"
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
        depth += 1
    return True, "OK", "safe"


def validate_storage_state_structure(path: str) -> Tuple[bool, str]:
    """
    Validates the serialized Playwright storage-state contract: a JSON
    object containing a "cookies" list.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        return False, f"unreadable-or-invalid-json ({type(exc).__name__})"
    if not isinstance(data, dict):
        return False, "not-a-json-object"
    if not isinstance(data.get("cookies"), list):
        return False, "missing-cookies-list"
    return True, "structure-ok"


# ---------------------------------------------------------------------------
# Timestamp and durability helpers
# ---------------------------------------------------------------------------

def force_mtime_strictly_greater(path: str, old_mtime: Optional[float]) -> float:
    """
    Sets path's mtime to a value strictly greater than old_mtime (if any)
    and at least the current wall clock. Returns the applied mtime.
    """
    applied = time.time()
    for _ in range(5):
        if old_mtime is not None and applied <= old_mtime + MTIME_EPSILON_SECONDS:
            applied = old_mtime + MTIME_EPSILON_SECONDS * (10 ** (_ + 1))
        try:
            os.utime(path, ns=(int(applied * 1_000_000_000), int(applied * 1_000_000_000)))
        except OSError:
            return os.path.getmtime(path)
        actual = os.path.getmtime(path)
        if old_mtime is None or actual > old_mtime + MTIME_EPSILON_SECONDS / 2:
            return actual
        applied = actual + MTIME_EPSILON_SECONDS * (10 ** (_ + 1))
    return os.path.getmtime(path)


def fsync_file(path: str) -> None:
    """
    fsync a file's contents; RAISES on failure (callers fail closed).

    Opens read-write: on Windows, FlushFileBuffers requires a handle with
    write access, so a read-only descriptor would spuriously fail.
    """
    fd = os.open(path, os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fsync_dir(path: str) -> None:
    """POSIX-only directory fsync; best-effort (unavailable on Windows)."""
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def chmod_strict(path: str, mode: int) -> None:
    """chmod that RAISES on failure (callers fail closed)."""
    os.chmod(path, mode)


# ---------------------------------------------------------------------------
# Transactional session publication
# ---------------------------------------------------------------------------

class PublicationError(Exception):
    """A fail-closed failure inside the publication transaction."""


def publish_state_atomically(
    staged_file: str,
    target_path: str,
    backup_path: str,
) -> Tuple[bool, str, Optional[float]]:
    """
    Publishes the staged state file to the target with an explicit
    recovery guarantee for the previously valid session.

    Sequence:
      1. Hard-link backup of the existing target (if any) into the staging
         area - zero-copy, same-filesystem by construction.
      2. Enforce 0600 on the staged file and pre-set its mtime strictly
         newer than the file being replaced; VERIFY both before publishing
         (rename preserves them, so what is verified is what lands).
      3. Atomic os.replace onto the target.
      4. Post-replacement validation (mode via lstat on POSIX, strictly
         newer mtime). On failure: restore the backup via os.replace
         (explicit recovery) - never delete or truncate state ad hoc.

    Returns (published, detail, applied_mtime).
    """
    old_mtime = os.path.getmtime(target_path) if os.path.exists(target_path) else None
    had_previous = old_mtime is not None

    if had_previous:
        try:
            if os.path.exists(backup_path):
                os.remove(backup_path)
            os.link(target_path, backup_path)
        except OSError as exc:
            logger.warning(
                "Prior-session backup could not be created (%s); proceeding without recovery copy.",
                type(exc).__name__,
            )
            backup_path = ""  # type: ignore[assignment]

    try:
        # Permissions and timestamp enforced and VERIFIED on the staged
        # file BEFORE the atomic replacement.
        chmod_strict(staged_file, 0o600)
        if os.name != "nt":
            st = os.stat(staged_file)
            if stat_module.S_IMODE(st.st_mode) != 0o600:
                raise PublicationError("staged-mode-not-0600")
        applied = force_mtime_strictly_greater(staged_file, old_mtime)
        if old_mtime is not None and applied <= old_mtime:
            raise PublicationError("staged-mtime-not-strictly-newer")

        os.replace(staged_file, target_path)
    except PublicationError:
        recovered = _restore_backup(backup_path, target_path)
        return False, ("recovered-to-previous" if recovered else
                       ("pre-publish-validation-failed" + (";no-backup" if had_previous else ""))), None
    except OSError as exc:
        # os.replace/chmod/utime failure: target untouched (replace never
        # ran or failed atomically).
        detail = f"publication-error:{type(exc).__name__}"
        if os.path.exists(target_path) and os.path.getmtime(target_path) == old_mtime:
            return False, detail + ";target-untouched", None
        recovered = _restore_backup(backup_path, target_path)
        return False, detail + (";recovered-to-previous" if recovered else ""), None

    fsync_dir(os.path.dirname(target_path) or ".")

    # Post-replacement validation with explicit recovery.
    try:
        st = os.lstat(target_path)
        if stat_module.S_ISLNK(st.st_mode) or not stat_module.S_ISREG(st.st_mode):
            raise PublicationError("published-not-regular-file")
        if os.name != "nt" and stat_module.S_IMODE(st.st_mode) != 0o600:
            raise PublicationError("published-mode-not-0600")
        final_mtime = os.path.getmtime(target_path)
        if old_mtime is not None and final_mtime <= old_mtime:
            raise PublicationError("published-mtime-not-strictly-newer")
    except PublicationError as exc:
        recovered = _restore_backup(backup_path, target_path)
        return False, (f"{exc};"
                       + ("recovered-to-previous" if recovered else "RECOVERY-FAILED-MANUAL-REVIEW")), None

    # Success: the verified replacement stands; the backup is removed with
    # the staging directory by the caller.
    return True, "published", applied


def _restore_backup(backup_path: str, target_path: str) -> bool:
    if not backup_path or not os.path.exists(backup_path):
        return False
    try:
        os.replace(backup_path, target_path)
        return True
    except OSError:
        logger.error("Explicit recovery from backup failed; manual review required.")
        return False
