"""Offline qualification gates and actual metadata loops; no app/network/Docker."""
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


def receipt(mode):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    if mode in ("full", "native"):
        for name in gate.NATIVE:
            ET.SubElement(suite, "testcase", classname="tests.integration.test_test_isolation", name=name)
    if mode in ("full", "privacy"):
        for i in range(21):
            ET.SubElement(suite, "testcase", classname="tests.integration.test_shared_logging_privacy", name=f"test_privacy_{i}")
    if mode in ("full", "migration"):
        for i in range(3):
            ET.SubElement(suite, "testcase", classname="tests.integration.test_legacy_schema_migration", name=f"test_migration_{i}")
    if mode == "full":
        for i in range(1315):
            ET.SubElement(suite, "testcase", classname="tests.synthetic", name=f"test_other_{i}")
    return root


class EvidenceGateTests(unittest.TestCase):
    def test_all_expected_modes_pass(self):
        for mode, total in (("full", 1341), ("native", 2), ("privacy", 21), ("migration", 3)):
            with self.subTest(mode=mode):
                result = gate.evaluate(receipt(mode), 0, mode)
                self.assertTrue(result["qualified"])
                self.assertEqual(result["passed"], total)

    def test_nonzero_process_exit_fails_even_with_green_xml(self):
        self.assertFalse(gate.evaluate(receipt("full"), 2, "full")["qualified"])

    def test_any_failure_error_or_skip_fails(self):
        for kind in ("failure", "error", "skipped"):
            with self.subTest(kind=kind):
                root = receipt("full")
                ET.SubElement(root.find(".//testcase"), kind)
                self.assertFalse(gate.evaluate(root, 0, "full")["qualified"])

    def test_native_skip_is_never_a_pass(self):
        root = receipt("native")
        ET.SubElement(root.find(".//testcase"), "skipped", message="platform unavailable")
        result = gate.evaluate(root, 0, "native")
        self.assertFalse(result["qualified"])
        self.assertFalse(result["native_all_passed"])
        self.assertEqual(result["skipped"], 1)

    def test_missing_or_duplicate_native_case_fails(self):
        for duplicate in (False, True):
            root = receipt("full")
            cases = root.findall(".//testcase")
            cases[0].set("name", gate.NATIVE[1] if duplicate else "test_imposter")
            self.assertFalse(gate.evaluate(root, 0, "full")["qualified"])

    def test_missing_privacy_or_migration_coverage_fails(self):
        for module in ("test_shared_logging_privacy", "test_legacy_schema_migration"):
            root = receipt("full")
            case = next(c for c in root.iter("testcase") if c.get("classname").endswith(module))
            case.set("classname", "tests.synthetic")
            self.assertFalse(gate.evaluate(root, 0, "full")["qualified"])

    def test_wrong_total_fails(self):
        root = receipt("full")
        ET.SubElement(root.find("testsuite"), "testcase", classname="tests.extra", name="test_extra")
        self.assertFalse(gate.evaluate(root, 0, "full")["qualified"])

    def test_empty_receipt_fails(self):
        self.assertFalse(gate.evaluate(ET.Element("testsuites"), 0, "full")["qualified"])

    def test_failure_contents_are_not_exposed_in_receipt(self):
        root = receipt("privacy")
        ET.SubElement(root.find(".//testcase"), "failure", message="fictional-private-value").text = "fictional-private-value"
        self.assertNotIn("fictional-private-value", str(gate.evaluate(root, 1, "privacy")))


def metadata_loops():
    """Compile the actual two verifier loops without executing their entrypoints."""
    source = Path(__file__).resolve().parents[2]
    workflow = (source / ".github/workflows/privacy-exact-tip-qualification.yml").read_text()
    start = workflow.index("          import importlib.metadata as md, os, platform, sys\n")
    end = workflow.index("          PY\n", start)
    workflow_tree = ast.parse(textwrap.dedent(workflow[start:end]))
    container_tree = ast.parse(Path(__file__).with_name("container_smoke.py").read_text())
    container_main = next(n for n in container_tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    for name, body in (("workflow", workflow_tree.body), ("container", container_main.body)):
        matches = [n for n in body if isinstance(n, ast.For) and "md.version" in ast.unparse(n)]
        if len(matches) != 1:
            raise AssertionError("exactly_one_dependency_verifier_required")
        yield name, compile(ast.Module(body=matches, type_ignores=[]), name, "exec")


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

    def test_normal_pin_uses_installed_distribution(self):
        for name, code in metadata_loops():
            with self.subTest(verifier=name):
                lookup = self.fixture_lookup({"pytest": "9.1.1"})
                execute_metadata_loop(code, "pytest==9.1.1", lookup)
                lookup.assert_called_once_with("pytest")

    def test_optional_extras_use_distribution_name_and_preserve_pin(self):
        for name, code in metadata_loops():
            with self.subTest(verifier=name):
                lookup = self.fixture_lookup({"uvicorn": "0.54.0"})
                execute_metadata_loop(code, "uvicorn[standard]==0.54.0", lookup)
                lookup.assert_called_once_with("uvicorn")

    def test_incorrect_version_fails_closed(self):
        for name, code in metadata_loops():
            for requirement in ("pytest==9.1.1", "uvicorn[standard]==0.54.0"):
                with self.subTest(verifier=name, requirement=requirement):
                    with self.assertRaises(AssertionError):
                        execute_metadata_loop(code, requirement, self.fixture_lookup({"pytest": "9.1.0", "uvicorn": "0.53.0"}))

    def test_missing_distribution_exception_propagates(self):
        for name, code in metadata_loops():
            for requirement, distribution in (("pytest==9.1.1", "pytest"), ("uvicorn[standard]==0.54.0", "uvicorn")):
                with self.subTest(verifier=name, requirement=requirement):
                    with self.assertRaises(md.PackageNotFoundError) as missing:
                        execute_metadata_loop(code, requirement, self.fixture_lookup({}))
                    self.assertEqual(missing.exception.name, distribution)

    def test_malformed_exact_pins_fail_closed(self):
        for name, code in metadata_loops():
            for requirement in ("pytest>=9.1.1", "pytest==9.1.1==unexpected"):
                with self.subTest(verifier=name, requirement=requirement):
                    with self.assertRaises((ValueError, AssertionError)):
                        execute_metadata_loop(code, requirement, self.fixture_lookup({"pytest": "9.1.1"}))

    def test_all_13_current_requirements_against_approved_fixture(self):
        requirements = (Path(__file__).resolve().parents[2] / "requirements.txt").read_text()
        self.assertEqual(len(self.APPROVED), 13)
        self.assertIn("uvicorn[standard]==0.54.0", requirements.splitlines())
        for name, code in metadata_loops():
            with self.subTest(verifier=name):
                lookup = self.fixture_lookup(self.APPROVED)
                execute_metadata_loop(code, requirements, lookup)
                self.assertEqual(lookup.call_count, 13)
                self.assertEqual([call.args[0] for call in lookup.call_args_list], list(self.APPROVED))

    def test_all_13_current_requirements_against_installed_metadata(self):
        requirements = (Path(__file__).resolve().parents[2] / "requirements.txt").read_text()
        for name, code in metadata_loops():
            with self.subTest(verifier=name):
                execute_metadata_loop(code, requirements, md.version)


if __name__ == "__main__":
    unittest.main()
