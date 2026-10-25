"""
Stage C2 Gate 4 + POSIX evidence: Disposable LINUX fixture suite
================================================================
MUST run on a genuine Linux kernel (e.g. WSL2 Ubuntu or the ARM64
runner); it refuses to run on Windows. Stdlib-only: imports
operator_primitives (kernel flock backend), never Playwright.

Volume-permission compatibility model (production topology):
  /app/data is root:root mode 0755 (Stage A observation); the operator
  container runs as uid 1000. This suite proves on a real Linux kernel:

  L1  uid-1000 CANNOT create a directory inside a root-owned 0755
      directory (probed against real root:root /etc) - hence the
      authorized one-time ownership setup is REQUIRED.
  L2  Once the session directory exists and is owned by uid 1000 with
      mode 0700, the full operator lifecycle works: exclusive kernel
      lock, transactional publication with literal 0600, strictly-newer
      mtime, and the persistent inert lock file.
  L3  Coexistence rule: a file the operator cannot WRITE (mode 0444,
      modeling a root:root file created by the watcher) can still be
      atomically REPLACED inside the operator-owned directory, because
      rename is governed by directory write permission. (The watcher,
      running as root, bypasses DAC entirely - kernel rule, documented.)
  L4  Real symlink rejection (final component and ancestor).
  L5  flock semantics: cross-process exclusivity, death auto-release,
      advisory-lock payload rewrite ignored, read-only directory fails
      closed.
  L6  Filesystem mtime precision: utime with nanosecond values is
      observable via st_mtime (strictly-newer epsilon of 1ms is safely
      above the filesystem's actual granularity).

No root privileges are used; the root-ownership aspect is exercised
against real system directories (/etc) with harmless failed operations.
"""

import json
import os
import shutil
import stat as stat_module
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from operator_primitives import (  # noqa: E402
    OperatorLock,
    publish_state_atomically,
    validate_destination_safety,
)

WORKER = os.path.join(HERE, "_lock_race_worker.py")

EXECUTED = 0
PASSED = 0
FAILED = 0


def record(label, ok, detail=""):
    global EXECUTED, PASSED, FAILED
    EXECUTED += 1
    if ok:
        PASSED += 1
        print(f"PASS {label}")
    else:
        FAILED += 1
        print(f"FAIL {label}: {detail}")


def main():
    if os.name == "nt" or not sys.platform.startswith("linux"):
        print("LINUX FIXTURE SUITE: refusing to run on non-Linux platform "
              f"({sys.platform}); execute on WSL2/ARM64 Linux")
        return 1
    print(f"Linux kernel fixture: uid={os.getuid()} platform={sys.platform}")
    if os.getuid() == 0:
        print("LINUX FIXTURE SUITE: refusing to run as root (must model the "
              "non-root operator)")
        return 1

    # ---- L1: uid-1000 cannot create inside root-owned 0755 --------------
    probe = os.path.join("/etc", f"tpo_stage_c2_probe_{os.getpid()}")
    etc_stat = os.stat("/etc")
    root_owned_755 = (etc_stat.st_uid == 0 and stat_module.S_IMODE(etc_stat.st_mode) == 0o755)
    creation_denied = False
    try:
        os.mkdir(probe)
        os.rmdir(probe)  # should never happen
    except PermissionError:
        creation_denied = True
    except OSError:
        creation_denied = True
    record("L1 non-root uid cannot mkdir inside root:root 0755 (/etc) - "
           "one-time authorized session-dir setup is REQUIRED",
           root_owned_755 and creation_denied,
           f"root_owned_755={root_owned_755} denied={creation_denied}")

    # ---- L2: full operator lifecycle in a uid-1000-owned 0700 dir --------
    base = tempfile.mkdtemp(prefix="tpo_c2_linux_")
    data_root = os.path.join(base, "data")
    os.makedirs(data_root, mode=0o755)  # volume-root stand-in (same uid here)
    session_dir = os.path.join(data_root, "session")
    os.makedirs(session_dir, mode=0o700)
    os.chmod(session_dir, 0o700)
    target = os.path.join(session_dir, "playwright_state.json")

    lock = OperatorLock(target)
    lock_ok = lock.acquire() is True
    blocked_while_held = OperatorLock(target).acquire() is False
    lock.release()
    record("L2a exclusive flock inside 0700 session dir; second acquire fails",
           lock_ok and blocked_while_held)

    prior = json.dumps({"cookies": [{"name": "prior", "value": "P"}]}).encode()
    fresh = json.dumps({"cookies": [{"name": "fresh", "value": "N"}]}).encode()
    staging = tempfile.mkdtemp(prefix=".mfa_stage_", dir=session_dir)
    os.chmod(staging, 0o700)
    staged = os.path.join(staging, "staged_state.json")
    backup = os.path.join(staging, "previous_session.bak")
    with open(staged, "wb") as f:
        f.write(fresh)
    with open(target, "wb") as f:
        f.write(prior)
    os.chmod(target, 0o600)
    old_mtime = os.path.getmtime(target)

    published, detail, _ = publish_state_atomically(staged, target, backup)
    mode = stat_module.S_IMODE(os.stat(target).st_mode)
    new_mtime = os.path.getmtime(target)
    record("L2b transactional publication: literal 0600 and strictly-newer mtime",
           published and open(target, "rb").read() == fresh
           and mode == 0o600 and new_mtime > old_mtime,
           f"published={published} mode={oct(mode)} detail={detail}")

    record("L2c session dir mode remains literal 0700 after publication",
           stat_module.S_IMODE(os.stat(session_dir).st_mode) == 0o700)

    # ---- L3: replace of a non-writable file inside an owned dir ----------
    ro_file = os.path.join(session_dir, "watcher_created.json")
    with open(ro_file, "wb") as f:
        f.write(prior)
    os.chmod(ro_file, 0o444)  # operator cannot WRITE it (models root:root file)
    replaced = True
    try:
        os.replace(target, ro_file)  # rename governed by DIRECTORY write perm
    except PermissionError:
        replaced = False
    record("L3 non-writable (0444) file is replaceable inside the owned dir - "
           "root-watcher/operator coexistence rule",
           replaced and open(ro_file, "rb").read() == fresh)
    os.chmod(ro_file, 0o600)

    # ---- L4: real symlink rejection ---------------------------------------
    real_file = os.path.join(session_dir, "real.json")
    with open(real_file, "wb") as f:
        f.write(b"{}")
    link_file = os.path.join(session_dir, "link.json")
    os.symlink(real_file, link_file)
    safe, code, _ = validate_destination_safety(link_file)
    record("L4a final-component symlink rejected (real Linux symlink)",
           safe is False and code == "SYMLINK_REJECTED")

    real_dir = os.path.join(base, "real_dir")
    os.makedirs(real_dir, exist_ok=True)
    link_dir = os.path.join(base, "link_dir")
    os.symlink(real_dir, link_dir)
    safe2, code2, _ = validate_destination_safety(os.path.join(link_dir, "s.json"))
    record("L4b ancestor-directory symlink rejected",
           safe2 is False and code2 == "DESTINATION_UNSAFE")

    # ---- L5: flock semantics on Linux -------------------------------------
    holder = subprocess.Popen(
        [sys.executable, "-I", WORKER, "--dest", target, "--hold-seconds", "4.0"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    holder.stdout.readline()
    with open(f"{target}.lock", "w") as f:  # advisory: rewrite allowed
        json.dump({"pid": 9999999, "host": "x", "time": 1.0, "token": "lies"}, f)
    still_blocked = OperatorLock(target).acquire() is False
    holder.kill()
    holder.communicate(timeout=30)
    after_death = OperatorLock(target).acquire() is True
    OperatorLock(target).release()
    record("L5a payload rewrite under active holder ignored; death auto-releases",
           still_blocked and after_death)

    ro_dir = os.path.join(base, "ro_dir")
    os.makedirs(ro_dir, mode=0o755)
    os.chmod(ro_dir, 0o555)
    try:
        record("L5b read-only lock directory fails closed",
               OperatorLock(os.path.join(ro_dir, "x.json")).acquire() is False)
    finally:
        os.chmod(ro_dir, 0o755)

    # ---- L6: filesystem mtime precision -----------------------------------
    probe_file = os.path.join(session_dir, "mtime_probe.json")
    with open(probe_file, "wb") as f:
        f.write(b"{}")
    t = time.time()
    os.utime(probe_file, ns=(int(t * 1e9), int(t * 1e9)))
    observed = os.stat(probe_file).st_mtime_ns
    ns_preserved = abs(observed - int(t * 1e9)) < 1_000_000  # within 1 ms
    t2 = t + 0.0015  # 1.5 ms - above the tool's 1 ms epsilon
    os.utime(probe_file, ns=(int(t2 * 1e9), int(t2 * 1e9)))
    distinguishable = os.stat(probe_file).st_mtime > t
    record("L6 filesystem resolves millisecond-scale mtime deltas "
           "(strictly-newer epsilon is observable)",
           ns_preserved and distinguishable,
           f"ns_preserved={ns_preserved} distinguishable={distinguishable}")

    shutil.rmtree(base, ignore_errors=True)

    print()
    print(f"LINUX FIXTURE RECEIPT: EXECUTED={EXECUTED} PASSED={PASSED} FAILED={FAILED} "
          f"SKIPPED=0 DEFERRED_PLATFORM=0")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
