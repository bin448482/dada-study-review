from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "runtime"))

from v3_dialogue.graph.selection import build_round_plan
from v3_dialogue.contracts import AuthorizedDialogueIngress
from v3_dialogue.gateway.fake import FakeDialogueModelGateway, dialogue_result
from v3_dialogue.service import DialogueTurnService
from v3_dialogue.unit_definition import load_dialogue_policy
from v3_dialogue.unit_definition import UnitDefinitionError, load_unit_definition
from v3_workflow.policy.review_schedule import ReviewSchedulePolicy


def _load_script(name: str):
    path = PROJECT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace("-", "_").replace(".py", ""), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DialogueUnitCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.english_text = self.root / "english-text"
        self.english_text.mkdir()
        (self.english_text / "README.md").write_text("# English text\n", encoding="utf-8")
        (self.english_text / "page-1.md").write_text(
            "# Page 1 — School subjects\n\n"
            "## A School subjects\n\n"
            "### Timetable\n\n"
            "A timetable has Science.\n\n"
            "### A1 Look and say\n\n"
            "What subjects are in the timetable?\n\n"
            "## Update my to-do list\n\n"
            "Find the subjects.\n",
            encoding="utf-8",
        )
        self.candidate = self.root / "unit.v5.candidate.json"
        self.task_audit = self.root / "unit.v5.candidate.task-audit.json"
        self.semantic_review = self.root / "unit.v5.candidate.semantic-review.json"
        self._write_valid_candidate()
        self._write_valid_audits()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_valid_candidate(self) -> None:
        self.candidate.write_text(json.dumps({
            "definition_schema_version": 3,
            "unit_id": "grade6-fixture-school-life",
            "version": 5,
            "title": "School life fixture",
            "source_pages": [1],
            "targets": [{
                "target_id": "subject_science",
                "target_type": "word",
                "core": True,
                "english": "Science",
                "meaning_zh": "科学",
                "source_pages": [1],
                "source_refs": [{
                    "page": 1,
                    "raw_heading": "A1 Look and say",
                    "source_locator": "A1 Look and say / question 1",
                }],
                "semantic_focus": "理解 Science 是课程表中的一门科目，并能围绕课程回答。",
                "dialogue_evidence": "understand_and_respond",
                "child_output_requirement": "respond",
                "scenario_ids": ["school-introduction"],
                "reviewable": True,
            }],
            "question_intents": [{
                "intent_key": "subject_science.primary",
                "target_id": "subject_science",
                "scenario_ids": ["school-introduction"],
                "purpose": "引导孩子理解课程表中的 Science 并完整回答。",
                "semantic_focus": "理解 Science 是课程表中的一门科目。",
                "dialogue_evidence": "understand_and_respond",
                "prompt_constraint": "使用教材问题或语义等价问法，引导孩子给出完整回答。",
            }],
            "scenarios": [{
                "scenario_id": "school-introduction",
                "required": True,
                "goal": "Talk about one subject at school.",
                "allowed_target_ids": ["subject_science"],
                "basic_task": "name one school subject",
                "advanced_modes": ["role_play", "reasoned_response"],
            }],
            "completion": {
                "all_core_targets_covered": True,
                "minimum_independent_ratio": 1.0,
                "required_scenarios": ["school-introduction"],
                "minimum_advanced_scenarios": 1,
            },
        }, ensure_ascii=False), encoding="utf-8")

    def _validate(self, previous_unit: Path | None = None) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(PROJECT / "scripts" / "validate-dialogue-unit-candidate.py"),
            "--candidate", str(self.candidate),
            "--english-text", str(self.english_text),
            "--unit-id", "grade6-fixture-school-life",
            "--version", "5",
            "--source-pages", "1",
        ]
        if previous_unit is not None:
            command.extend(["--previous-unit", str(previous_unit)])
        return subprocess.run(command, text=True, capture_output=True, check=False)

    def _write_valid_audits(self) -> None:
        self.task_audit.write_text(json.dumps({
            "audit_schema_version": 1,
            "unit_id": "grade6-fixture-school-life",
            "version": 5,
            "source_pages": [1],
            "pages": [{
                "page": 1,
                "sections": [{
                    "locator": "A1 Look and say / question 1",
                    "section_kind": "learning_task",
                    "learner_action": "回答课程表科目问题",
                    "information_dimensions": ["school subject"],
                    "output": "respond",
                    "modality": "required",
                    "coverage_target_ids": ["subject_science"],
                    "coverage_scenario_ids": ["school-introduction"],
                    "exclusion_reason": "",
                }],
            }],
        }, ensure_ascii=False), encoding="utf-8")
        content_hash = load_unit_definition(self.candidate, "grade6-fixture-school-life").content_hash
        self.semantic_review.write_text(json.dumps({
            "review_schema_version": 1,
            "unit_id": "grade6-fixture-school-life",
            "version": 5,
            "candidate_content_hash": content_hash,
            "review_status": "complete",
            "groups": [{
                "group_id": "science-target",
                "target_ids": ["subject_science"],
                "decision": "retain_distinct",
                "rationale": "课程表科目词是可独立验证的语义目标。",
            }],
        }, ensure_ascii=False), encoding="utf-8")

    def test_candidate_validates_and_injects_frozen_intent(self) -> None:
        completed = self._validate()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        unit = load_unit_definition(self.candidate, "grade6-fixture-school-life")
        self.assertEqual(unit.definition_schema_version, 3)
        plan = build_round_plan(unit, {"target_progress": {}, "scenario_progress": {}})
        self.assertEqual(
            plan["steps"][0]["question_intent"]["dialogue_evidence"],
            "understand_and_respond",
        )
        self.assertIn("完整回答", plan["steps"][0]["question_intent"]["prompt_constraint"])

    def test_v1_unit_remains_loadable(self) -> None:
        unit = load_unit_definition(
            PROJECT / "config" / "dialogue-units" / "grade6-english-unit-1-school-life" / "unit.v1.json",
            "grade6-english-unit-1-school-life",
        )
        self.assertEqual(unit.definition_schema_version, 1)

    def test_unit_v6_has_review_design_for_every_phrase(self) -> None:
        unit = load_unit_definition(
            PROJECT / "config" / "dialogue-units" / "grade6-english-unit-1-school-life" / "unit.v6.json",
            "grade6-english-unit-1-school-life",
        )
        phrases = [target for target in unit.targets if target["target_type"] == "phrase"]
        self.assertEqual(len(phrases), 22)
        self.assertTrue(all(target["review_design"]["accepted_expressions"] for target in phrases))

    def test_intent_reaches_the_frozen_gateway_turn(self) -> None:
        unit = load_unit_definition(self.candidate, "grade6-fixture-school-life")
        database = self.root / "dialogue.sqlite3"
        gateway = FakeDialogueModelGateway([dialogue_result("Tell me about Science.")])
        policy = load_dialogue_policy(PROJECT / "config" / "dialogue-policy.json")
        service = DialogueTurnService(
            database,
            gateway,
            "fixture dialogue prompt",
            unit,
            policy,
            ReviewSchedulePolicy.from_file(PROJECT / "config" / "review-schedule.test.json"),
        )
        try:
            delivery = service.handle(
                AuthorizedDialogueIngress("start", "2026-09-01T00:00:00Z", "fixture-child", True)
            )
            self.assertTrue(delivery.reply_text.endswith("Tell me about Science."))
            frozen = gateway.prepared_turns[0].active_step["question_intent"]
            self.assertEqual(frozen["intent_key"], "subject_science.primary")
            self.assertEqual(frozen["dialogue_evidence"], "understand_and_respond")
        finally:
            service.close()

    def test_any_source_heading_can_be_a_target_source(self) -> None:
        raw = json.loads(self.candidate.read_text(encoding="utf-8"))
        raw["targets"][0]["source_refs"][0]["raw_heading"] = "Timetable"
        raw["targets"][0]["source_refs"][0]["source_locator"] = "Timetable table / Science"
        self.candidate.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        completed = self._validate()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_unknown_source_heading_is_rejected(self) -> None:
        raw = json.loads(self.candidate.read_text(encoding="utf-8"))
        raw["targets"][0]["source_refs"][0]["raw_heading"] = "Missing section"
        self.candidate.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        completed = self._validate()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("not present", completed.stdout)

    def test_same_content_must_retain_previous_target_id(self) -> None:
        previous = self.root / "unit.v4.json"
        prior = json.loads(self.candidate.read_text(encoding="utf-8"))
        prior["version"] = 4
        previous.write_text(json.dumps(prior, ensure_ascii=False), encoding="utf-8")
        current = json.loads(self.candidate.read_text(encoding="utf-8"))
        current["targets"][0]["target_id"] = "renamed_science"
        current["scenarios"][0]["allowed_target_ids"] = ["renamed_science"]
        current["question_intents"][0]["target_id"] = "renamed_science"
        self.candidate.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
        completed = self._validate(previous)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("reuse target_id subject_science", completed.stdout)

    def test_loader_rejects_incompatible_output_requirement(self) -> None:
        raw = json.loads(self.candidate.read_text(encoding="utf-8"))
        raw["targets"][0]["child_output_requirement"] = "ask"
        self.candidate.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(UnitDefinitionError):
            load_unit_definition(self.candidate)

    def test_finalize_promotes_once_and_never_overwrites(self) -> None:
        output = self.root / "unit.v5.json"
        command = [
            sys.executable,
            str(PROJECT / "scripts" / "finalize-dialogue-unit-candidate.py"),
            "--candidate", str(self.candidate),
            "--english-text", str(self.english_text),
            "--task-audit", str(self.task_audit),
            "--semantic-review", str(self.semantic_review),
            "--unit-id", "grade6-fixture-school-life",
            "--version", "5",
            "--source-pages", "1",
            "--output", str(output),
        ]
        first = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertTrue(output.is_file())
        second = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("already exists", second.stdout)

    def test_finalize_requires_both_audits(self) -> None:
        self.task_audit.unlink()
        output = self.root / "unit.v5.json"
        command = [
            sys.executable,
            str(PROJECT / "scripts" / "finalize-dialogue-unit-candidate.py"),
            "--candidate", str(self.candidate),
            "--english-text", str(self.english_text),
            "--task-audit", str(self.task_audit),
            "--semantic-review", str(self.semantic_review),
            "--unit-id", "grade6-fixture-school-life",
            "--version", "5",
            "--source-pages", "1",
            "--output", str(output),
        ]
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("task audit", completed.stdout)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
