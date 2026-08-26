"""Frozen, zero-wait state-machine golden regression suite for v3 entry."""

from __future__ import annotations

import json
from pathlib import Path
import unittest


MANIFEST = Path(__file__).with_name("golden_cases.json")


class EntryGoldenSuiteTests(unittest.TestCase):
    def test_every_frozen_golden_case_passes_without_wall_clock_waiting(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(
            (manifest.get("contract_name"), manifest.get("contract_version"), manifest.get("wall_clock_wait_seconds")),
            ("dada.entry_state_machine_golden_suite", 1, 0),
        )
        cases = manifest.get("cases")
        self.assertIsInstance(cases, list)
        self.assertGreaterEqual(len(cases), 7)
        seen: set[str] = set()
        for case in cases:
            with self.subTest(case=case.get("id")):
                self.assertEqual(set(case), {"id", "test"})
                self.assertIsInstance(case["id"], str)
                self.assertNotIn(case["id"], seen)
                seen.add(case["id"])
                suite = unittest.defaultTestLoader.loadTestsFromName(case["test"])
                result = unittest.TestResult()
                suite.run(result)
                self.assertTrue(result.wasSuccessful(), "\n".join(f"{test}: {detail}" for test, detail in result.failures + result.errors))


if __name__ == "__main__":
    unittest.main()
