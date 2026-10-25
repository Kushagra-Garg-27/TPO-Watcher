"""Cross-process lock race worker for the Stage C2 adversarial suite.

Run as a subprocess by test_adversarial_mfa_operator.py / test_lock_concurrency.py.
It attempts to acquire the exclusive operator lock for a destination path,
prints one JSON result line, optionally holds the lock, then releases.

No network access, no browser, no app imports. Exit codes:
  0 = lock acquired (and held/released)
  3 = lock NOT acquired (contention or staleness race lost)
  4 = unexpected error
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from operator_primitives import OperatorLock  # noqa: E402  (stdlib-only)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest", required=True)
    parser.add_argument("--hold-seconds", type=float, default=1.0)
    parser.add_argument("--stale-seconds", type=float, default=600.0)
    args = parser.parse_args()

    lock = OperatorLock(args.dest, timeout_seconds=args.stale_seconds)
    try:
        acquired = lock.acquire()
    except Exception as exc:  # pragma: no cover - defensive
        print(json.dumps({"acquired": False, "error": type(exc).__name__}))
        return 4

    print(json.dumps({"acquired": acquired, "pid": os.getpid()}), flush=True)
    if not acquired:
        return 3

    time.sleep(args.hold_seconds)
    lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
