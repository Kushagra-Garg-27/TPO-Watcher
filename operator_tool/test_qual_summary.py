"""
Stage C2 Run 3: qualification-summary receipt-integrity tests
==============================================================
Feeds deterministic event streams to qual_summary.sh and verifies BOTH the
exit code and the classification. A stream is valid only when every
expected gate has exactly one `started` then exactly one `passed` event.

Streams: fully valid; one failed gate; missing gate; started-but-truncated;
contradictory (passed+failed); duplicate passed; malformed line; unknown
gate name; empty log; missing log file. Every invalid stream MUST exit
nonzero and print QUALIFICATION_INCOMPLETE; only the fully valid stream
exits 0 with ALL_GATES_SATISFIED_AND_EXECUTED.
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SUMMARY = os.path.join(HERE, "qual_summary.sh")
GATES = "g1 g2 g3"

EXECUTED = PASSED = FAILED = 0


def record(label, ok, detail=""):
    global EXECUTED, PASSED, FAILED
    EXECUTED += 1
    if ok:
        PASSED += 1
        print(f"PASS {label}")
    else:
        FAILED += 1
        print(f"FAIL {label}: {detail}")


def summarize(lines):
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False, newline="\n") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
        path = f.name
    proc = subprocess.run(["sh", SUMMARY, path] + GATES.split(),
                          capture_output=True, text=True, timeout=60)
    os.unlink(path)
    return proc.returncode, proc.stdout + proc.stderr


def main():
    valid = ["started g1", "passed g1", "started g2", "passed g2", "started g3", "passed g3"]

    rc, out = summarize(valid)
    record("S1 fully valid stream -> exit 0 + ALL_GATES_SATISFIED_AND_EXECUTED",
           rc == 0 and "ALL_GATES_SATISFIED_AND_EXECUTED" in out
           and "gates_passed=3" in out and "gates_missing=0" in out, f"rc={rc} out={out}")

    rc, out = summarize(["started g1", "passed g1", "started g2", "failed g2 rc=1", "started g3", "passed g3"])
    record("S2 one failed gate -> nonzero + gates_failed=1",
           rc != 0 and "QUALIFICATION_INCOMPLETE" in out and "gates_failed=1" in out, f"rc={rc} out={out}")

    rc, out = summarize(["started g1", "passed g1", "started g2", "passed g2"])
    record("S3 missing gate -> nonzero + gates_missing=1",
           rc != 0 and "gates_missing=1" in out, f"rc={rc} out={out}")

    rc, out = summarize(["started g1", "passed g1", "started g2", "started g3", "passed g3"])
    record("S4 truncated (started without terminal) -> nonzero + gates_incomplete=1",
           rc != 0 and "gates_incomplete=1" in out, f"rc={rc} out={out}")

    rc, out = summarize(["started g1", "passed g1", "failed g1 rc=3", "started g2", "passed g2", "started g3", "passed g3"])
    record("S5 contradictory events (passed AND failed) -> nonzero",
           rc != 0 and "QUALIFICATION_INCOMPLETE" in out, f"rc={rc} out={out}")

    rc, out = summarize(["started g1", "passed g1", "passed g1", "started g2", "passed g2", "started g3", "passed g3"])
    record("S6 duplicate terminal event -> nonzero",
           rc != 0 and "QUALIFICATION_INCOMPLETE" in out, f"rc={rc} out={out}")

    rc, out = summarize(valid + ["this line is malformed"])
    record("S7 malformed line -> nonzero + corrupt_lines=1",
           rc != 0 and "corrupt_lines=1" in out, f"rc={rc} out={out}")

    rc, out = summarize(valid + ["passed ghost_gate"])
    record("S8 unknown gate name -> nonzero (corrupt/unknown stream)",
           rc != 0 and "QUALIFICATION_INCOMPLETE" in out, f"rc={rc} out={out}")

    rc, out = summarize([])
    record("S9 empty log -> nonzero",
           rc != 0 and "QUALIFICATION_INCOMPLETE" in out, f"rc={rc} out={out}")

    p10 = subprocess.run(["sh", SUMMARY, os.path.join(tempfile.gettempdir(), "definitely-absent-q3.log")] + GATES.split(),
                         capture_output=True, text=True, timeout=60)
    record("S10 missing log file -> nonzero fail-closed",
           p10.returncode != 0 and "QUALIFICATION_INCOMPLETE" in p10.stdout,
           f"rc={p10.returncode} out={p10.stdout}")

    print()
    print(f"QUAL SUMMARY RECEIPT: EXECUTED={EXECUTED} PASSED={PASSED} FAILED={FAILED} "
          f"SKIPPED=0 DEFERRED_PLATFORM=0")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
