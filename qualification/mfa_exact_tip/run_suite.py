"""Exact-tip qualification harness for hardened MFA candidate.

Installs the UNCHANGED candidate isolation guard.
Only fictional tests are run. JUnit stays within the guard's disposable root;
the only exported receipt is a bounded JSON summary, never test failure text.
"""
import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

CANDIDATE = "f4def963294723ae57cdad5cd93b5e3f38667502"
NATIVE = (
    "test_native_sendmsg_method_has_installed_guard",
    "test_linux_owned_descriptor_operations_and_safe_cleanup",
    "test_linux_native_fchmod_on_disposable_file_succeeds",
)
BASE_TOTALS = {"full": 1393, "native": 3, "mfa": 33, "privacy": 21, "migration": 3}


def evaluate(root, exit_code, mode, expected_total):
    cases = list(root.iter("testcase"))

    def passed(case):
        return not any(case.find(kind) is not None for kind in ("failure", "error", "skipped"))

    native = {}
    for name in NATIVE:
        matches = [c for c in cases if c.get("name") == name
                   and c.get("classname", "").endswith("test_test_isolation")]
        native[name] = len(matches) == 1 and passed(matches[0])
    mfa = sum(c.get("classname", "").endswith("test_mfa_auth_recovery") for c in cases)
    privacy = sum(c.get("classname", "").endswith("test_shared_logging_privacy") for c in cases)
    migration = sum(c.get("classname", "").endswith("test_legacy_schema_migration") for c in cases)
    result = {
        "candidate": CANDIDATE,
        "scope": mode,
        "pytest_exit_code": int(exit_code),
        "total": len(cases),
        "expected_total": expected_total,
        "passed": sum(passed(c) for c in cases),
        "failed": sum(c.find("failure") is not None for c in cases),
        "errors": sum(c.find("error") is not None for c in cases),
        "skipped": sum(c.find("skipped") is not None for c in cases),
        "native_all_passed": all(native.values()) if mode in ("full", "native") else None,
        "native_cases": native if mode in ("full", "native") else {},
        "mfa_cases": mfa,
        "privacy_cases": privacy,
        "migration_cases": migration,
    }
    result["qualified"] = (
        exit_code == 0
        and len(cases) == expected_total
        and result["passed"] == len(cases)
        and result["failed"] == 0
        and result["errors"] == 0
        and result["skipped"] == 0
        and (mode not in ("full", "native") or result["native_all_passed"])
        and (mode != "mfa" or mfa == 33)
        and (mode != "privacy" or privacy == 21)
        and (mode != "migration" or migration == 3)
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("mode", choices=list(BASE_TOTALS.keys()))
    args = parser.parse_args()
    source = args.source.resolve()

    has_migration = (source / "tests/integration/test_legacy_schema_migration.py").exists()
    expected_total = 1396 if (args.mode == "full" and has_migration) else BASE_TOTALS[args.mode]

    # This wrapper adds evidence only; it neither replaces nor patches isolation.
    sys.path.insert(0, str(source / "tests"))
    import isolation_bootstrap as bootstrap
    root = bootstrap.install()
    sys.path.insert(0, str(source))
    import pytest
    migration_path = (
        source / "tests/integration/test_legacy_schema_migration.py"
        if (source / "tests/integration/test_legacy_schema_migration.py").exists()
        else source / "qualification/mfa_exact_tip/test_legacy_schema_migration.py"
    )
    selection = {
        "full": [str(source / "tests")],
        "native": [str(source / "tests/integration/test_test_isolation.py") + "::" + n for n in NATIVE],
        "mfa": [str(source / "tests/unit/test_mfa_auth_recovery.py")],
        "privacy": [str(source / "tests/integration/test_shared_logging_privacy.py")],
        "migration": [str(migration_path)],
    }[args.mode]
    junit = root / "qualification.xml"
    code = pytest.main([
        "-p", "pytest_asyncio.plugin", "-p", "pytest_mock", "-p", "no:cacheprovider",
        "--basetemp=" + str(root / "pytest"),
        "-o", "log_file=" + str(root / "pytest.log"),
        "--junitxml=" + str(junit),
        "-q", "--tb=short", "-rs", *selection,
    ])
    bootstrap.assert_no_violations()
    result = evaluate(ET.parse(junit).getroot(), code, args.mode, expected_total)
    print("QUALIFICATION_RECEIPT=" + json.dumps(result, sort_keys=True))
    return 0 if result["qualified"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print('QUALIFICATION_RECEIPT={"qualified":false,"category":"qualification_evidence_unavailable"}')
        raise SystemExit(2)
