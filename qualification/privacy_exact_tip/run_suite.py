"""Exact-tip qualification harness. Installs the UNCHANGED candidate isolation guard.

Only fictional tests are run. JUnit stays within the guard's disposable root;
the only exported receipt is a bounded JSON summary, never test failure text.
"""
import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

CANDIDATE = "e761f8d47d8f3827c685bcecd7aab6adf5afe378"
NATIVE = (
    "test_native_sendmsg_method_has_installed_guard",
    "test_linux_owned_descriptor_operations_and_safe_cleanup",
)
TOTALS = {"full": 1341, "native": 2, "privacy": 21, "migration": 3}


def evaluate(root, exit_code, mode):
    cases = list(root.iter("testcase"))

    def passed(case):
        return not any(case.find(kind) is not None for kind in ("failure", "error", "skipped"))

    native = {}
    for name in NATIVE:
        matches = [c for c in cases if c.get("name") == name
                   and c.get("classname", "").endswith("test_test_isolation")]
        native[name] = len(matches) == 1 and passed(matches[0])
    privacy = sum(c.get("classname", "").endswith("test_shared_logging_privacy") for c in cases)
    migration = sum(c.get("classname", "").endswith("test_legacy_schema_migration") for c in cases)
    result = {
        "candidate": CANDIDATE, "scope": mode, "pytest_exit_code": int(exit_code),
        "total": len(cases), "expected_total": TOTALS[mode],
        "passed": sum(passed(c) for c in cases),
        "failed": sum(c.find("failure") is not None for c in cases),
        "errors": sum(c.find("error") is not None for c in cases),
        "skipped": sum(c.find("skipped") is not None for c in cases),
        "native_all_passed": all(native.values()) if mode in ("full", "native") else None,
        "native_cases": native if mode in ("full", "native") else {},
        "privacy_cases": privacy, "migration_cases": migration,
    }
    result["qualified"] = (
        exit_code == 0 and len(cases) == TOTALS[mode]
        and result["passed"] == len(cases)
        and (mode not in ("full", "native") or result["native_all_passed"])
        and (mode not in ("full", "privacy") or privacy == 21)
        and (mode not in ("full", "migration") or migration == 3)
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("mode", choices=TOTALS)
    args = parser.parse_args()
    source = args.source.resolve()
    # This wrapper adds evidence only; it neither replaces nor patches isolation.
    sys.path.insert(0, str(source / "tests"))
    import isolation_bootstrap as bootstrap
    root = bootstrap.install()
    sys.path.insert(0, str(source))
    import pytest
    selection = {
        "full": [str(source / "tests")],
        "native": [str(source / "tests/integration/test_test_isolation.py") + "::" + n for n in NATIVE],
        "privacy": [str(source / "tests/integration/test_shared_logging_privacy.py")],
        "migration": [str(source / "tests/integration/test_legacy_schema_migration.py")],
    }[args.mode]
    junit = root / "qualification.xml"
    code = pytest.main([
        "-p", "pytest_asyncio.plugin", "-p", "pytest_mock", "-p", "no:cacheprovider",
        "--basetemp=" + str(root / "pytest"), "--junitxml=" + str(junit),
        "-q", "--tb=short", "-rs", *selection,
    ])
    bootstrap.assert_no_violations()
    result = evaluate(ET.parse(junit).getroot(), code, args.mode)
    print("QUALIFICATION_RECEIPT=" + json.dumps(result, sort_keys=True))
    return 0 if result["qualified"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print('QUALIFICATION_RECEIPT={"qualified":false,"category":"qualification_evidence_unavailable"}')
        raise SystemExit(2)
