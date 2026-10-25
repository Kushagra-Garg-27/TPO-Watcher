"""
Stage C2 Gate 5: Deterministic stale-lock / exclusivity proofs
==============================================================
Stdlib-only (imports operator_primitives, never Playwright), so this suite
runs identically on the Windows dev host (msvcrt backend) and on a native
Linux fixture such as WSL Ubuntu (fcntl.flock backend).

Part A reproduces the RETIRED rename-based reclaim algorithm with an
injected, deterministic interleaving and proves it could break exclusivity
(rationale for the redesign; the shipped code no longer contains it).

Part B proves the shipped kernel-managed design against every scenario in
the Stage C2 gate brief, using controlled sequencing rather than stress.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from operator_primitives import OperatorLock  # noqa: E402

WORKER = os.path.join(HERE, "_lock_race_worker.py")

EXECUTED = 0
PASSED = 0
FAILED = 0
SKIPPED = 0
DEFERRED = 0
DEFERRED_REASONS = []


def record(label, ok, detail=""):
    global EXECUTED, PASSED, FAILED
    EXECUTED += 1
    if ok:
        PASSED += 1
        print(f"PASS {label}")
    else:
        FAILED += 1
        print(f"FAIL {label}: {detail}")


def record_deferred(label, reason):
    global DEFERRED, DEFERRED_REASONS
    DEFERRED += 1
    DEFERRED_REASONS.append(f"{label}: {reason}")
    print(f"DEFERRED {label} ({reason}; executes on the ARM64 Linux qualification)")


def summary(suite):
    global SKIPPED
    print()
    print(f"{suite} RECEIPT: EXECUTED={EXECUTED} PASSED={PASSED} FAILED={FAILED} "
          f"SKIPPED={SKIPPED} DEFERRED_PLATFORM={DEFERRED}"
          + (f" ({'; '.join(DEFERRED_REASONS)})" if DEFERRED_REASONS else ""))
    return FAILED == 0 and SKIPPED == 0


# ---------------------------------------------------------------------------
# Part A: faithful reproduction of the retired rename-reclaim algorithm
# ---------------------------------------------------------------------------

class OldReclaimLock:
    """Retired algorithm (simplified to the race-relevant path)."""

    def __init__(self, path):
        self.path = path
        self.is_acquired = False

    def acquire(self, rename_hook=None):
        for _ in range(4):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                with os.fdopen(fd, "w") as f:
                    json.dump({"pid": os.getpid(), "time": time.time()}, f)
                self.is_acquired = True
                return True
            except FileExistsError:
                try:
                    with open(self.path) as f:
                        json.load(f)
                    stale = True  # pre-seeded stale payload
                except Exception:
                    stale = False
                if not stale:
                    return False
                aside = self.path + ".aside"
                if rename_hook:
                    rename_hook()  # DETERMINISTIC INTERLEAVING POINT
                try:
                    os.rename(self.path, aside)
                    os.remove(aside)
                except OSError:
                    continue
                continue
        return False


def prove_old_design_race():
    tmp = tempfile.mkdtemp(prefix="tpo_c2_oldrace_")
    lock_path = os.path.join(tmp, "state.json.lock")
    with open(lock_path, "w") as f:
        json.dump({"pid": 9999999, "time": time.time() - 900.0}, f)  # stale

    holder_b = OldReclaimLock(lock_path)

    def interleaving():  # process B reclaims the stale lock and acquires validly
        aside = lock_path + ".b"
        os.rename(lock_path, aside)
        os.remove(aside)
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"pid": os.getpid(), "time": time.time()}, f)
        holder_b.is_acquired = True  # B now validly holds

    thief_a = OldReclaimLock(lock_path)
    a_got = thief_a.acquire(rename_hook=interleaving)

    shutil.rmtree(tmp, ignore_errors=True)
    return a_got and holder_b.is_acquired


def main():
    print(f"Platform backend: {'msvcrt' if os.name == 'nt' else 'fcntl.flock'}")

    # ---- Part A: the retired design's race, deterministically -------------
    both_held = prove_old_design_race()
    record("A1 [DETERMINISTIC] Retired rename-reclaim design could break exclusivity "
           "(two holders) - justification for the kernel-lock redesign",
           both_held, "expected both holders to believe they hold the lock")

    # ---- Part B: shipped design -------------------------------------------
    tmp = tempfile.mkdtemp(prefix="tpo_c2_lockdet_")
    target = os.path.join(tmp, "state.json")

    # B1: live holder preserved against every contender; the retired design's
    # steal point (new valid holder vs stale reader) is structurally gone
    # because acquisition never renames or deletes anything.
    holder = subprocess.Popen(
        [sys.executable, "-I", WORKER, "--dest", target, "--hold-seconds", "6.0"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    holder_line = holder.stdout.readline()
    b1_pre = "true" in (holder_line or "")
    contender = OperatorLock(target)
    b1_contender_blocked = contender.acquire() is False
    holder_alive = holder.poll() is None
    holder.kill()
    holder.communicate(timeout=30)
    record("B1 [PROCESS] Valid holder between stale-check and (retired) rename point: "
           "kernel lock makes the steal structurally impossible",
           b1_pre and b1_contender_blocked and holder_alive,
           f"pre={b1_pre} blocked={b1_contender_blocked} alive={holder_alive}")

    # B1b: stale-LOOKING payload rewritten under an ACTIVE holder must not
    # displace it. Requires POSIX advisory-lock semantics: on Windows the
    # mandatory byte lock makes the rewrite itself impossible (equally
    # fail-closed), so the scenario executes only on Linux.
    if os.name != "nt":
        holder = subprocess.Popen(
            [sys.executable, "-I", WORKER, "--dest", target, "--hold-seconds", "4.0"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        holder.stdout.readline()
        with open(f"{target}.lock", "w") as f:  # advisory locks permit the write
            json.dump({"pid": 9999999, "host": socket.gethostname(),
                       "time": time.time() - 900.0, "token": "ancient"}, f)
        still_blocked = OperatorLock(target).acquire() is False
        holder.kill()
        holder.communicate(timeout=30)
        record("B1b [PROCESS] Stale-look payload rewrite under a live holder is ignored (flock)",
               still_blocked)
    else:
        record_deferred(
            "B1b payload-rewrite-under-holder",
            "POSIX advisory-lock semantics required; Windows mandatory lock prevents the write")

    # B2: after holder death, next acquirer succeeds (kernel auto-release).
    b2 = OperatorLock(target)
    b2_ok = b2.acquire() is True
    b2.release()
    record("B2 [PROCESS] Process death releases the lock; next acquire succeeds",
           b2_ok, "expected successful acquisition after holder death")

    # B3: two reclaimers racing on the dead holder's lock: exactly one wins.
    procs = [subprocess.Popen(
        [sys.executable, "-I", WORKER, "--dest", target, "--hold-seconds", "3.0"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    wins = []
    for proc in procs:
        out, _ = proc.communicate(timeout=60)
        lines = (out or "").strip().splitlines()
        try:
            wins.append(json.loads(lines[-1]).get("acquired") is True)
        except Exception:
            wins.append(False)
    record("B3 [PROCESS] Two simultaneous reclaimers of a dead holder's lock -> exactly one",
           sum(wins) == 1, f"winners={sum(wins)}")

    # B4: sequential contenders all blocked while a holder lives.
    holder = subprocess.Popen(
        [sys.executable, "-I", WORKER, "--dest", target, "--hold-seconds", "5.0"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    holder.stdout.readline()
    blocked = all(OperatorLock(target).acquire() is False for _ in range(3))
    holder_alive = holder.poll() is None
    holder.kill()
    holder.communicate(timeout=30)
    record("B4 [PROCESS] Live-holder preservation against every sequential contender",
           blocked and holder_alive)

    # B5: foreign token / foreign release can never unlock or delete.
    mine = OperatorLock(target)
    mine.acquire()
    foreign = OperatorLock(target)
    foreign.is_acquired = True  # a confused foreign instance believing it holds
    foreign.release()           # must be a no-op (no descriptor)
    still_blocked = OperatorLock(target).acquire() is False
    file_exists = os.path.exists(f"{target}.lock")
    mine.release()
    record("B5 [FS] Foreign release is a no-op; ownership (kernel) and file both intact",
           still_blocked and file_exists)

    # B6: PID reuse / nonsense payload cannot fool the lock (POSIX-executed;
    # on Windows the mandatory lock prevents rewriting the payload at all).
    if os.name != "nt":
        mine = OperatorLock(target)
        mine.acquire()
        with open(f"{target}.lock", "w") as f:
            json.dump({"pid": os.getpid() + 50000, "host": "other-host",
                       "time": 1.0, "token": "garbage"}, f)
        still_locked = OperatorLock(target).acquire() is False
        mine.release()
        record("B6 [FS] Payload with misleading PID cannot weaken kernel ownership",
               still_locked)
    else:
        record_deferred(
            "B6 misleading-PID payload",
            "POSIX advisory-lock semantics required; Windows mandatory lock prevents the write")

    # B7: filesystem failure fails closed (lock path replaced by a directory).
    dir_target = os.path.join(tmp, "dirlock.json")
    os.makedirs(f"{dir_target}.lock")  # open() must fail
    record("B7 [FS] Unusable lock path (directory) -> fail-closed acquisition",
           OperatorLock(dir_target).acquire() is False)

    # B7b (POSIX only): unwritable lock directory -> fail closed.
    if os.name != "nt":
        ro_dir = os.path.join(tmp, "ro")
        os.makedirs(ro_dir, mode=0o755)
        os.chmod(ro_dir, 0o555)
        try:
            record("B7b [FS] Read-only lock directory -> fail-closed acquisition (POSIX)",
                   OperatorLock(os.path.join(ro_dir, "x.json")).acquire() is False)
        finally:
            os.chmod(ro_dir, 0o755)
    else:
        record_deferred("B7b read-only-directory lock test", "requires POSIX permissions")

    # B8: same-process double acquire fails (flock is per open-description).
    first = OperatorLock(target)
    first.acquire()
    second = OperatorLock(target)
    record("B8 [FS] Same-process second acquisition of a held lock fails",
           second.acquire() is False)
    first.release()

    # B9: release without acquire never touches anything.
    before = os.path.exists(f"{target}.lock")
    OperatorLock(target).release()
    record("B9 [FS] release() without acquire() is inert",
           os.path.exists(f"{target}.lock") == before)

    shutil.rmtree(tmp, ignore_errors=True)
    ok = summary("LOCK DETERMINISTIC SUITE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
