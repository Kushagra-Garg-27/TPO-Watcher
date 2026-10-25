"""Offline qualification gates and actual metadata loops; no app, network, or Docker required."""
import ast
import contextlib
import importlib.util
import importlib.metadata as md
import io
import textwrap
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock

SPEC = importlib.util.spec_from_file_location("receipt_gate", Path(__file__).with_name("run_suite.py"))
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class EvidenceGateTests(unittest.TestCase):
    def make_junit(self, classname, names, fail=None, skip=None):
        root = ET.Element("testsuites")
        suite = ET.SubElement(root, "testsuite", name="pytest")
        for name in names:
            case = ET.SubElement(suite, "testcase", classname=classname, name=name)
            if fail and name == fail:
                ET.SubElement(case, "failure", message="simulated failure")
            if skip and name == skip:
                ET.SubElement(case, "skipped", message="simulated skip")
        return root

    def test_mfa_mode_requires_33_passes(self):
        names = [f"test_mfa_{i}" for i in range(33)]
        root = self.make_junit("tests.unit.test_mfa_auth_recovery", names)
        res = gate.evaluate(root, 0, "mfa", 33)
        self.assertTrue(res["qualified"])
        self.assertEqual(res["mfa_cases"], 33)
        self.assertEqual(res["passed"], 33)

    def test_mfa_mode_fails_if_less_than_33_cases(self):
        names = [f"test_mfa_{i}" for i in range(32)]
        root = self.make_junit("tests.unit.test_mfa_auth_recovery", names)
        res = gate.evaluate(root, 0, "mfa", 33)
        self.assertFalse(res["qualified"])

    def test_privacy_mode_requires_21_passes(self):
        names = [f"test_priv_{i}" for i in range(21)]
        root = self.make_junit("tests.integration.test_shared_logging_privacy", names)
        res = gate.evaluate(root, 0, "privacy", 21)
        self.assertTrue(res["qualified"])
        self.assertEqual(res["privacy_cases"], 21)

    def test_native_mode_requires_both_specific_cases(self):
        names = list(gate.NATIVE)
        root = self.make_junit("tests.integration.test_test_isolation", names)
        res = gate.evaluate(root, 0, "native", 2)
        self.assertTrue(res["qualified"])
        self.assertTrue(res["native_all_passed"])

    def test_full_mode_qualification(self):
        names = [f"test_{i}" for i in range(1371 - 2)]
        root = self.make_junit("tests.generic", names)
        # Add the two native cases
        suite = root.find("testsuite")
        for n in gate.NATIVE:
            ET.SubElement(suite, "testcase", classname="tests.integration.test_test_isolation", name=n)
        res = gate.evaluate(root, 0, "full", 1371)
        self.assertTrue(res["qualified"])
        self.assertEqual(res["total"], 1371)
        self.assertEqual(res["passed"], 1371)

    def test_failure_or_skip_disqualifies(self):
        names = list(gate.NATIVE)
        root = self.make_junit("tests.integration.test_test_isolation", names, fail=names[0])
        res = gate.evaluate(root, 1, "native", 2)
        self.assertFalse(res["qualified"])
        self.assertEqual(res["failed"], 1)

        root2 = self.make_junit("tests.integration.test_test_isolation", names, skip=names[0])
        res2 = gate.evaluate(root2, 0, "native", 2)
        self.assertFalse(res2["qualified"])
        self.assertEqual(res2["skipped"], 1)


def execute_metadata_loop(code, requirements, lookup):
    class FictionalPath:
        def __truediv__(self, _):
            return self

        def read_text(self):
            return requirements

    namespace = {
        "Path": lambda _: FictionalPath(),
        "os": types.SimpleNamespace(environ={"RUNNER_TEMP": "fictional-runner-temp"}),
        "md": types.SimpleNamespace(version=lookup),
    }
    with contextlib.redirect_stdout(io.StringIO()):
        exec(code, namespace)


class MetadataVerifierTests(unittest.TestCase):
    APPROVED = {
        "playwright": "1.63.0", "pydantic": "2.13.5", "pydantic-settings": "2.15.0",
        "httpx": "0.28.1", "pytest": "9.1.1", "pytest-asyncio": "1.4.0",
        "pytest-mock": "3.16.0", "tzdata": "2026.4", "fastapi": "0.141.1",
        "uvicorn": "0.54.0", "slowapi": "0.1.10", "jinja2": "3.1.6",
        "cryptography": "50.0.2",
    }

    @staticmethod
    def fixture_lookup(versions):
        def version(name):
            if name not in versions:
                raise md.PackageNotFoundError(name)
            return versions[name]
        return Mock(side_effect=version)

    def get_container_smoke_loop(self):
        container_tree = ast.parse(Path(__file__).with_name("container_smoke.py").read_text(encoding="utf-8"))
        container_main = next(n for n in container_tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        matches = [n for n in container_main.body if isinstance(n, ast.For) and "md.version" in ast.unparse(n)]
        self.assertEqual(len(matches), 1)
        return compile(ast.Module(body=matches, type_ignores=[]), "container", "exec")

    def test_extras_normalized_correctly(self):
        code = self.get_container_smoke_loop()
        lookup = self.fixture_lookup({"uvicorn": "0.54.0"})
        execute_metadata_loop(code, "uvicorn[standard]==0.54.0", lookup)
        lookup.assert_called_once_with("uvicorn")

    def test_incorrect_version_fails_closed(self):
        code = self.get_container_smoke_loop()
        with self.assertRaises(AssertionError):
            execute_metadata_loop(code, "pytest==9.1.1", self.fixture_lookup({"pytest": "9.1.0"}))

    def test_all_13_current_requirements_against_approved_fixture(self):
        code = self.get_container_smoke_loop()
        requirements = (Path(__file__).resolve().parents[2] / "requirements.txt").read_text(encoding="utf-8")
        self.assertEqual(len(self.APPROVED), 13)
        self.assertIn("uvicorn[standard]==0.54.0", requirements.splitlines())
        lookup = self.fixture_lookup(self.APPROVED)
        execute_metadata_loop(code, requirements, lookup)
        self.assertEqual(lookup.call_count, 13)
        self.assertEqual([call.args[0] for call in lookup.call_args_list], list(self.APPROVED))

    def test_all_13_current_requirements_against_installed_environment(self):
        code = self.get_container_smoke_loop()
        requirements = (Path(__file__).resolve().parents[2] / "requirements.txt").read_text(encoding="utf-8")
        execute_metadata_loop(code, requirements, md.version)


if __name__ == "__main__":
    unittest.main()
