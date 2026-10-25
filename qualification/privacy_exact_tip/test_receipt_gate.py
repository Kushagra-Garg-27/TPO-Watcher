"""Offline tests of qualification evidence handling; no app, network or Docker."""
import importlib.util
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
