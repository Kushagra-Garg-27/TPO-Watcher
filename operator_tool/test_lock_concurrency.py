"""
Stage C2: Cross-Process Lock Concurrency Suite (kernel-managed revision)
========================================================================
Genuine OS-subprocess evidence for the exclusive single-writer policy of
the kernel-managed OperatorLock (fcntl.flock on Linux / msvcrt on Windows).
Stdlib-only: imports operator_primitives, never Playwright - runs
identically on the Windows host and on a native Linux fixture.

The former stale-threshold scenarios are retained with adjusted meaning:
with kernel-managed locking, a live holder is protected by kernel truth
(the threshold parameter is inert), and holder death releases the lock
automatically (no reclaim code path exists at all).
"""

import json
import os
import shutil
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
    print(f"DEFERRED {label} ({reason})")


def spawn_worker(dest, hold=1.0, stale=600.0):
    return subprocess.Popen(
        [sys.executable, "-I", WORKER, "--dest", dest,
         "--hold-seconds", str(hold), "--stale-seconds", str(stale)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def worker_result(proc):
    out, _ = proc.communicate(timeout=120)
    lines = (out or "").strip().splitlines()
    try:
        return json.loads(lines[-1]).get("acquired") is True
    except Exception:
        return False


def stray_artifacts(directory):
    # Persistent *.lock files are expected by design; anything else
    # (staging dirs, reclaim files) is leakage.
    return [f for f in os.listdir(directory) if f.startswith(".mfa_stage_") or ".reclaim." in f]


def main():
    temp_dir = tempfile.mkdtemp(prefix="tpo_c2_lockrace_")

    try:
        # 1. Ten simultaneous operators: exactly one acquires.
        dest = os.path.join(temp_dir, "state.json")
        procs = [spawn_worker(dest, hold=3.0) for _ in range(10)]
        results = [worker_result(p) for p in procs]
        record("01 ten simultaneous operators -> exactly one acquires",
               sum(results) == 1, f"winners={sum(results)}")

        # 2. Three consecutive rounds: one winner each, no artifact leakage.
        rounds_ok = True
        for round_no in range(3):
            rprocs = [spawn_worker(dest, hold=1.5) for _ in range(6)]
            rresults = [worker_result(p) for p in rprocs]
            time.sleep(2.0)  # winner releases
            if sum(rresults) != 1:
                rounds_ok = False
            if stray_artifacts(temp_dir):
                rounds_ok = False
        record("02 three 6-way rounds -> one winner each, zero artifact leakage",
               rounds_ok)

        # 3. Live holder is never displaced (kernel truth; threshold inert).
        holder = spawn_worker(dest, hold=4.0)
        time.sleep(1.5)
        contender = spawn_worker(dest, hold=0.1, stale=1.0)
        holder_won = worker_result(contender) is False
        holder_exit = holder.wait(timeout=30)
        record("03 live holder (1.5s-old lock) survives a 1s stale threshold: kernel-managed liveness",
               holder_won and holder_exit == 0)

        # 4. Staggered second operator fails while the first still holds.
        first = spawn_worker(dest, hold=5.0)
        time.sleep(1.5)
        second = spawn_worker(dest, hold=0.1)
        first_held = worker_result(second) is False
        first.wait(timeout=30)
        record("04 staggered second operator fails while first holds",
               first_held)

        # 5. Abandoned holder (killed mid-hold) -> kernel auto-release; the
        #    persistent lock FILE may remain, but it is inert and no reclaim
        #    artifacts appear.
        abandoned = spawn_worker(dest, hold=30.0)
        time.sleep(1.5)
        abandoned.kill()
        abandoned.communicate(timeout=30)
        reclaimer = spawn_worker(dest, hold=0.5)
        reclaimed = worker_result(reclaimer)
        time.sleep(0.8)
        record("05 killed holder: lock auto-released by the kernel; next operator proceeds",
               reclaimed and not stray_artifacts(temp_dir),
               f"reclaimed={reclaimed} strays={stray_artifacts(temp_dir)}")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    print()
    print(f"LOCK CONCURRENCY RECEIPT: EXECUTED={EXECUTED} PASSED={PASSED} FAILED={FAILED} "
          f"SKIPPED={SKIPPED} DEFERRED_PLATFORM={DEFERRED}"
          + (f" ({'; '.join(DEFERRED_REASONS)})" if DEFERRED_REASONS else ""))
    if os.name != "nt" and DEFERRED:
        print("FAIL: platform deferrals are not permitted on Linux")
        return 1
    return 0 if (FAILED == 0 and SKIPPED == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
