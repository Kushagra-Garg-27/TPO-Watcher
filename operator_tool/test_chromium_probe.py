"""
Stage C2 Gate (run 2): orphan-Chromium probe verification
=========================================================
Validates chromium_orphan_probe.sh on a real Linux /proc:
  P1  No browser process running        -> probe exits 0 (CHROMIUM_CLEAN).
  P2  A genuine orphan (a runnable copy of a system binary NAMED
      `chromium`, so its comm is exactly `chromium`) -> probe exits 1.
  P3  The checker's own command line contains the string "chromium"
      (sh -c wrapper, as inside the workflow's docker run) -> still exits 0
      (no self-match false positive; this is the defect of `pgrep -f`).
  P4  Old-defect demonstration: `pgrep -f chromium` inside an sh -c wrapper
      whose arguments merely CONTAIN the word -> self-match (documents why
      the replacement was necessary). Skipped if pgrep is unavailable.

Linux-only (/proc comm inspection). On Windows this suite records a
platform deferral and exits 0 WITHOUT counting the deferral as a pass.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE = os.path.join(HERE, "chromium_orphan_probe.sh")

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


def probe(wrapper_args=None):
    cmd = ["sh", PROBE] if wrapper_args is None else wrapper_args
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def main():
    global DEFERRED
    if os.name == "nt" or not sys.platform.startswith("linux") or not os.path.isdir("/proc"):
        DEFERRED = 1
        print("DEFERRED orphan-probe suite (requires Linux /proc; executes on WSL/ARM64)")
        print(f"CHROMIUM PROBE RECEIPT: EXECUTED=0 PASSED=0 FAILED=0 SKIPPED=0 DEFERRED_PLATFORM=1")
        return 0  # deferral permitted off-Linux; NOT counted as a pass

    # P1: clean state.
    rc, out = probe()
    record("P1 clean system -> CHROMIUM_CLEAN exit 0", rc == 0 and "CHROMIUM_CLEAN" in out, f"rc={rc} out={out}")

    # P2: genuine orphan - a real running process whose comm is `chromium`.
    tmp = tempfile.mkdtemp(prefix="tpo_probe_")
    fake = os.path.join(tmp, "chromium")
    shutil.copy2(shutil.which("sleep") or "/bin/sleep", fake)
    os.chmod(fake, 0o755)
    orphan = subprocess.Popen([fake, "30"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.4)  # let the kernel register comm
    try:
        rc, out = probe()
        record("P2 real orphan (comm=chromium) detected -> exit 1",
               rc == 1 and "ORPHAN_BROWSER_PROCESS" in out and "chromium" in out, f"rc={rc} out={out}")
    finally:
        orphan.kill()
        orphan.wait()
        shutil.rmtree(tmp, ignore_errors=True)

    # P3: checker's own cmdline contains 'chromium' -> no false positive.
    check3 = subprocess.run(
        ["sh", "-c", f'sh "{PROBE}"; echo PROBE_RC=$?; sh -c "echo chromium-marker" >/dev/null'],
        capture_output=True, text=True, timeout=60)
    ok3 = "PROBE_RC=0" in check3.stdout and "CHROMIUM_CLEAN" in check3.stdout
    record("P3 checker cmdline containing 'chromium' -> no self-match", ok3, check3.stdout.strip())

    # P4: old-defect demonstration with pgrep -f (documents the fix rationale).
    if shutil.which("pgrep"):
        p4 = subprocess.run(
            ["sh", "-c", "pgrep -f 'chromium_orphan_probe' >/dev/null 2>&1; echo SELFMATCH=$?"],
            capture_output=True, text=True, timeout=60)
        selfmatch = "SELFMATCH=0" in p4.stdout  # pgrep -f found the wrapper itself
        record("P4 pgrep -f self-match defect demonstrated (exit 0 = matched itself)",
               selfmatch, p4.stdout.strip())
    else:
        global SKIPPED
        SKIPPED += 1
        print("SKIP P4 pgrep unavailable")

    print()
    print(f"CHROMIUM PROBE RECEIPT: EXECUTED={EXECUTED} PASSED={PASSED} FAILED={FAILED} "
          f"SKIPPED={SKIPPED} DEFERRED_PLATFORM={DEFERRED}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
