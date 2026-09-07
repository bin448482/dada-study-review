from __future__ import annotations

import json
from pathlib import Path
import unittest


class DialogueGoldenSuiteTests(unittest.TestCase):
    def test_frozen_case_manifest_is_unique_and_points_to_runtime_tests(self) -> None:
        cases = json.loads(Path(__file__).with_name("golden_cases.json").read_text(encoding="utf-8"))
        self.assertTrue(isinstance(cases, list) and cases)
        ids = [case.get("case_id") for case in cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(isinstance(case.get("test"), str) and case["test"].startswith("test_") for case in cases))


if __name__ == "__main__":
    unittest.main()
