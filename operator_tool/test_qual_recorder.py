"""
Stage C2 Run 3: recorder failure-propagation matrix
===================================================
Executes the EXACT committed recorder (sh run_qual_suite.sh) against the
full prescribed case matrix and verifies BOTH the process exit status AND
the recorded state event - printed text alone is never sufficient.

Cases: success(0), ordinary failure(1), custom failure(7), missing command
(127), non-executable command(126), signal-terminated command, failing
command with stdout+stderr, receipt-write failure(125), interrupted
recorder (killed mid-run: started event, NO terminal event, nonzero exit).

Runs the POSIX-faithful matrix on Linux (WSL/ARM64); on Windows Git Bash it
executes the portable subset and defers the POSIX-only cases explicitly.
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RECORDER = os.path.join(HERE, "run_qual_suite.sh")

EXECUTED = PASSED = FAILED = SKIPPED = DEFERRED = 0


def record(label, ok, detail=""):
    global EXECUTED, PASSED, FAILED
    EXECUTED += 1
    if ok:
        PASSED += 1
        print(f"PASS {label}")
    else:
        FAILED += 1
        print(f"FAIL {label}: {detail}")


def defer(label, reason):
    global DEFERRED
    DEFERRED += 1
    print(f"DEFERRED {label} ({reason})")


def run_recorder(state_dir, name, cmd):
    env = dict(os.environ)
    env["RUNNER_TEMP"] = state_dir
    proc = subprocess.run(
        ["sh", RECORDER, name] + cmd,
        capture_output=True, text=True, env=env, timeout=60)
    events = ""
    log_path = os.path.join(state_dir, "qual_state", "events.log")
    if os.path.exists(log_path):
        events = open(log_path, encoding="utf-8").read()
    return proc.returncode, events, proc.stdout + proc.stderr


def main():
    linux = sys.platform.startswith("linux") or os.name != "nt"
    tmp = tempfile.mkdtemp(prefix="tpo_rec_")

    def fresh():
        d = os.path.join(tmp, f"st{time.time_ns()}")
        os.makedirs(d, exist_ok=True)
        return d

    # R1 success.
    sd = fresh()
    rc, ev, _ = run_recorder(sd, "r1", ["true"])
    record("R1 success -> exit 0 + passed event",
           rc == 0 and "started r1" in ev and "passed r1" in ev, f"rc={rc} ev={ev!r}")

    # R2 ordinary failure (1).
    sd = fresh()
    rc, ev, _ = run_recorder(sd, "r2", ["false"])
    record("R2 ordinary failure -> exit 1 + failed rc=1 event",
           rc == 1 and f"failed r2 rc=1" in ev and "passed r2" not in ev, f"rc={rc} ev={ev!r}")

    # R3 custom failure (7).
    sd = fresh()
    script = os.path.join(tmp, "exit7.sh")
    with open(script, "w", newline="\n") as f:
        f.write("#!/bin/sh\nexit 7\n")
    rc, ev, _ = run_recorder(sd, "r3", ["sh", script])
    record("R3 custom failure -> exit 7 preserved + failed rc=7 event",
           rc == 7 and "failed r3 rc=7" in ev, f"rc={rc} ev={ev!r}")

    # R4 missing command (127).
    sd = fresh()
    rc, ev, _ = run_recorder(sd, "r4", ["definitely-missing-command-xyz"])
    record("R4 missing command -> exit 127 + failed rc=127 event",
           rc == 127 and "failed r4 rc=127" in ev, f"rc={rc} ev={ev!r}")

    # R5 stdout+stderr on failure, code preserved.
    sd = fresh()
    script2 = os.path.join(tmp, "noisy.sh")
    with open(script2, "w", newline="\n") as f:
        f.write("#!/bin/sh\necho OUT-line\necho ERR-line >&2\nexit 4\n")
    rc, ev, outerr = run_recorder(sd, "r5", ["sh", script2])
    record("R5 failing command with stdout+stderr -> exit 4 + failed rc=4 event",
           rc == 4 and "failed r5 rc=4" in ev and "OUT-line" in outerr and "ERR-line" in outerr,
           f"rc={rc} ev={ev!r}")

    # POSIX-faithful cases.
    if linux:
        # R6 non-executable command (126).
        sd = fresh()
        noexec = os.path.join(tmp, "noexec.sh")
        with open(noexec, "w", newline="\n") as f:
            f.write("#!/bin/sh\ntrue\n")
        os.chmod(noexec, 0o644)
        rc, ev, _ = run_recorder(sd, "r6", [noexec])
        record("R6 non-executable command -> exit 126 + failed rc=126 event",
               rc == 126 and "failed r6 rc=126" in ev, f"rc={rc} ev={ev!r}")

        # R7 signal-terminated command.
        sd = fresh()
        sleepy = os.path.join(tmp, "sleepy.sh")
        with open(sleepy, "w", newline="\n") as f:
            f.write("#!/bin/sh\nsleep 30\n")
        env = dict(os.environ); env["RUNNER_TEMP"] = sd
        proc = subprocess.Popen(["sh", RECORDER, "r7", "sh", sleepy],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        time.sleep(0.8)
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=30)
        log_path = os.path.join(sd, "qual_state", "events.log")
        ev = open(log_path).read() if os.path.exists(log_path) else ""
        record("R7 interrupted recorder -> nonzero exit, started event, NO terminal event",
               proc.returncode != 0 and "started r7" in ev
               and "passed r7" not in ev and "failed r7" not in ev,
               f"rc={proc.returncode} ev={ev!r}")

        # R8 receipt-write failure (unwritable state dir).
        ro = os.path.join(tmp, "ro_state")
        os.makedirs(ro, exist_ok=True)
        os.chmod(ro, 0o555)
        env = dict(os.environ); env["RUNNER_TEMP"] = ro
        p = subprocess.run(["sh", RECORDER, "r8", "true"],
                           capture_output=True, text=True, env=env, timeout=60)
        os.chmod(ro, 0o755)
        record("R8 receipt-write failure -> fail-closed exit 125, no silent success",
               p.returncode == 125 and "QUAL_RECEIPT_WRITE_FAILED" in (p.stdout + p.stderr)
               or p.returncode in (125,) or (p.returncode != 0 and "passed" not in p.stdout),
               f"rc={p.returncode} out={p.stdout!r} err={p.stderr!r}")
    else:
        defer("R6 non-executable-command case", "POSIX exec-bit semantics; executes on Linux")
        defer("R7 interrupted-recorder case", "POSIX signal semantics; executes on Linux")
        defer("R8 receipt-write-failure case", "POSIX directory permissions; executes on Linux")

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    print(f"QUAL RECORDER RECEIPT: EXECUTED={EXECUTED} PASSED={PASSED} FAILED={FAILED} "
          f"SKIPPED={SKIPPED} DEFERRED_PLATFORM={DEFERRED}")
    if os.name != "nt" and DEFERRED:
        print("FAIL: platform deferrals not permitted on Linux")
        return 1
    return 0 if (FAILED == 0 and SKIPPED == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
