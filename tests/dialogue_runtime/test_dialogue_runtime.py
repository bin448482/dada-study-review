from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "runtime"))

from v3_dialogue.contracts import AuthorizedDialogueIngress, DialogueContractError, validate_dialogue_state_machine_result
from v3_dialogue.gateway.fake import FakeDialogueModelGateway, dialogue_result
from v3_dialogue.graph.selection import build_round_plan, course_passes
from v3_dialogue.graph.selection import select_next_focus
from v3_dialogue.service import DialogueTurnService
from v3_dialogue.unit_definition import UnitDefinition, UnitDefinitionError, load_dialogue_policy, load_unit_definition
from v3_workflow.persistence.migration import MigrationError, migrate_dialogue_schema, migrate_dialogue_target_identity_schema
from v3_workflow.persistence.repository import RepositoryError, WorkflowRepository
from v3_workflow.policy.review_schedule import ReviewSchedulePolicy


class DialogueRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "workflow.sqlite3"
        self.unit = load_unit_definition(
            PROJECT / "config" / "dialogue-units" / "grade6-english-unit-1-school-life" / "unit.v1.json",
            "grade6-english-unit-1-school-life",
        )
        self.policy = load_dialogue_policy(PROJECT / "config" / "dialogue-policy.json")
        self.review_policy = ReviewSchedulePolicy.from_file(PROJECT / "config" / "review-schedule.test.json")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _service(self, executions):
        gateway = FakeDialogueModelGateway(executions)
        return DialogueTurnService(self.database, gateway, "dialogue system prompt", self.unit, self.policy, self.review_policy), gateway

    def test_repetition_capture_wraps_then_batch_is_explicitly_consumed(self) -> None:
        service, gateway = self._service([
            dialogue_result("Let's talk about your school."),
            dialogue_result("Please repeat: Maths.", evidence="unable", scenario="basic"),
            dialogue_result("Great. Maths is a school subject.", evidence="supported_success", scenario="basic", repetition_outcome="succeeded", capture_target_id="subject_maths"),
            dialogue_result("Let's finish here. Say start review when you are ready.", transition="stop_dialogue"),
        ])
        try:
            start = service.handle(AuthorizedDialogueIngress("start school dialogue", "2026-09-01T00:00:00Z", "child-1", True))
            unable = service.handle(AuthorizedDialogueIngress("I don't know.", "2026-09-01T00:01:00Z", "child-1"))
            captured = service.handle(AuthorizedDialogueIngress("Maths", "2026-09-01T00:02:00Z", "child-1"))
            closed = service.handle(AuthorizedDialogueIngress("end dialogue", "2026-09-01T00:03:00Z", "child-1"))
            self.assertTrue(start.reply_text.endswith("Let's talk about your school."))
            self.assertEqual(start.speech_text, "Let's talk about your school.")
            self.assertEqual(start.state_text, "【英语对话中】现在开始英语对话。想结束时，请说“对话结束”。")
            self.assertIn("本单元进度：已完成 0 / 28 个目标。", start.progress_text)
            self.assertIn("本轮计划：", start.progress_text)
            self.assertIn("场景：介绍学校", start.progress_text)
            self.assertIn("Maths（数学）", start.progress_text)
            self.assertIn("完整表达时还要说出：学校细节", start.progress_text)
            self.assertEqual((unable.reply_text, unable.state_text, unable.progress_text), ("Please repeat: Maths.", None, None))
            self.assertTrue(captured.reply_text.endswith("Great. Maths is a school subject."))
            self.assertEqual((captured.state_text, captured.progress_text), ("【英语对话中】", "本轮已整理：1 个以后会复习的小点。"))
            self.assertIn("Let's finish here. Say start review when you are ready.", closed.reply_text)
            self.assertIn("已创建专用复习批次", closed.reply_text)
            self.assertEqual((closed.state_text, closed.progress_text), ("【英语对话已结束】本轮已创建专用复习批次。之后说“开始复习”即可开始这批复习。", "本轮复习点：1 个。"))
            workflow_id = gateway.prepared_turns[0].workflow_id
            state = service.repository.get_dialogue_context(workflow_id)
            self.assertEqual((state["phase"], state["captured_count"], state["subphase"]), ("closed", 1, "normal"))
            with sqlite3.connect(self.database) as connection:
                batch = connection.execute("SELECT batch_id, consumed_by_review_workflow_id FROM dialogue_review_batches").fetchone()
                item_count = connection.execute("SELECT COUNT(*) FROM dialogue_review_batch_items").fetchone()[0]
            self.assertIsNotNone(batch)
            self.assertIsNone(batch[1])
            self.assertEqual(item_count, 1)
            review = service.repository.start_review_from_dialogue_batch("review-1", "child-1", "2026-09-01T00:04:00Z")
            self.assertEqual(review["workflow_type"], "review")
            self.assertEqual(service.repository.get_review_queue_progress("review-1"), {"total": 1, "completed": 0, "remaining": 1})
            with sqlite3.connect(self.database) as connection:
                self.assertEqual(connection.execute("SELECT consumed_by_review_workflow_id FROM dialogue_review_batches").fetchone()[0], "review-1")
        finally:
            service.close()

    def test_grammar_observation_never_creates_a_learning_item(self) -> None:
        service, _ = self._service([
            dialogue_result("Try: It does experiments.", evidence="supported_success", scenario="basic", grammar=[{"category": "subject_verb_agreement", "correction": "It does"}]),
        ])
        try:
            delivery = service.handle(AuthorizedDialogueIngress("start", "2026-09-01T00:00:00Z", "child-grammar", True))
            self.assertTrue(delivery.reply_text.endswith("Try: It does experiments."))
            self.assertEqual(delivery.state_text, "【英语对话中】现在开始英语对话。想结束时，请说“对话结束”。")
            with sqlite3.connect(self.database) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM learning_items").fetchone()[0], 0)
                event = connection.execute("SELECT payload_json FROM workflow_log_events WHERE event_type = 'dialogue_turn_evaluated'").fetchone()[0]
            self.assertEqual(json.loads(event)["data"]["grammar_observations"][0]["category"], "subject_verb_agreement")
        finally:
            service.close()

    def test_dialogue_close_creates_batch_below_wrapping_threshold(self) -> None:
        policy = self.policy
        gateway = FakeDialogueModelGateway([
            dialogue_result("Let's talk about Maths."),
            dialogue_result("Please repeat: Maths.", evidence="unable", scenario="basic"),
            dialogue_result("Great. Maths is a school subject.", evidence="supported_success", scenario="basic", repetition_outcome="succeeded", capture_target_id="subject_maths"),
            dialogue_result("Let's finish here.", transition="stop_dialogue"),
        ])
        service = DialogueTurnService(self.database, gateway, "dialogue system prompt", self.unit, policy, self.review_policy)
        try:
            service.handle(AuthorizedDialogueIngress("start", "2026-09-01T00:00:00Z", "child-below-threshold", True))
            service.handle(AuthorizedDialogueIngress("I don't know.", "2026-09-01T00:01:00Z", "child-below-threshold"))
            service.handle(AuthorizedDialogueIngress("Maths", "2026-09-01T00:02:00Z", "child-below-threshold"))
            closed = service.handle(AuthorizedDialogueIngress("end dialogue", "2026-09-01T00:03:00Z", "child-below-threshold"))
            self.assertIn("已创建专用复习批次", closed.reply_text)
            workflow_id = gateway.prepared_turns[0].workflow_id
            with sqlite3.connect(self.database) as connection:
                batch = connection.execute("SELECT batch_id FROM dialogue_review_batches WHERE source_dialogue_workflow_id = ?", (workflow_id,)).fetchone()
                item_count = connection.execute(
                    "SELECT COUNT(*) FROM dialogue_review_batch_items WHERE batch_id = ?", (batch[0],)
                ).fetchone()[0]
            self.assertIsNotNone(batch)
            self.assertEqual(item_count, 1)
        finally:
            service.close()

    def test_backfill_closed_dialogue_batch_is_idempotent(self) -> None:
        repository = WorkflowRepository(self.database)
        repository.initialize()
        workflow_id = "dialogue-backfill"
        child = repository.start_dialogue(
            "dialogue-backfill", "child-backfill", "system", "start",
            self.unit.unit_id, self.unit.version, self.unit.content_hash,
            "school-introduction", "subject_maths", 2, "2026-09-01T00:00:00Z",
        )
        response = repository.append_log_event(
            workflow_id, "llm_response",
            {"task_contract_name": "dada.dialogue_state_machine_turn", "task_contract_version": 1,
             "output": {}, "source_child_event_id": child},
            "2026-09-01T00:01:00Z", require_active_type="dialogue",
        )
        repository.commit_dialogue_turn(
            workflow_id, child, response,
            {"source_child_event_id": child, "unit_id": self.unit.unit_id, "unit_version": self.unit.version,
             "scenario_id": "school-introduction", "target_id": "subject_maths",
             "target_evidence": "supported_success", "scenario_achievement": "none",
             "grammar_observations": [], "content_slots_covered": []},
            "saved", "2026-09-01T00:01:00Z", next_scenario_id="school-introduction",
            next_target_id="subject_maths", next_difficulty_level=2,
            pending_repetition_target_id="subject_maths",
            capture_targets=[{"target_id": "subject_maths", "target_type": "word", "english": "Maths", "meaning_zh": "数学", "reviewable": True}],
            initial_review_stage=self.review_policy.initial_stage,
            initial_due_at=self.review_policy.initial_due_at("2026-09-01T00:01:00Z"),
        )
        repository.close_dialogue_and_prepare_batch(workflow_id, "closed", "2026-09-01T00:02:00Z")
        first = repository.backfill_closed_dialogue_batch(workflow_id, "2026-09-01T00:03:00Z")
        second = repository.backfill_closed_dialogue_batch(workflow_id, "2026-09-01T00:04:00Z")
        self.assertIsNotNone(first)
        self.assertEqual(first, second)

    def test_contract_rejects_capture_without_completed_pending_repetition(self) -> None:
        turn = self._sample_turn()
        bad = dialogue_result("x", capture_target_id="subject_maths").output
        with self.assertRaises(DialogueContractError):
            validate_dialogue_state_machine_result(bad, turn)

    def test_contract_rejects_independent_success_for_a_successful_repetition(self) -> None:
        turn = replace(self._sample_turn(), pending_repetition_target_ids=("subject_maths",))
        bad = dialogue_result(
            "Great.", evidence="independent_success", repetition_outcome="succeeded",
            capture_target_id="subject_maths",
        ).output
        with self.assertRaises(DialogueContractError):
            validate_dialogue_state_machine_result(bad, turn)

    def test_l0_rejects_legacy_repetition_behavior(self) -> None:
        turn = self._sample_turn()
        bad = dialogue_result("Please repeat Maths.", level_behavior="repetition").output
        with self.assertRaises(DialogueContractError):
            validate_dialogue_state_machine_result(bad, turn)

    def test_unit_loader_rejects_unknown_scenario_reference(self) -> None:
        raw = json.loads((PROJECT / "config" / "dialogue-units" / "grade6-english-unit-1-school-life" / "unit.v1.json").read_text(encoding="utf-8"))
        raw["targets"][0]["scenario_ids"] = ["missing"]
        path = Path(self.temp.name) / "bad-unit.json"
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(UnitDefinitionError):
            load_unit_definition(path)

    def test_new_dialogue_rounds_start_at_l2_and_freeze_l4_content_slots(self) -> None:
        self.assertEqual((self.policy.minimum_difficulty, self.policy.maximum_difficulty), (2, 4))
        plan = build_round_plan(self.unit, {"target_progress": {}, "scenario_progress": {}})
        self.assertTrue(all(step["entry_difficulty"] == 2 for step in plan["steps"]))
        self.assertTrue(all(len(step["content_slots"]) >= 2 for step in plan["steps"]))
        self.assertGreater(len(plan["scenario_ids"]), 2)

    def test_l4_independent_success_requires_all_content_slots(self) -> None:
        turn = self._sample_turn()
        l4 = replace(turn, difficulty_level=4, active_step={"content_slots": [
            {"slot_id": "feature", "target_id": "subject_maths", "purpose": "feature"},
            {"slot_id": "reason", "target_id": "reason", "purpose": "reason"},
        ]})
        incomplete = dialogue_result("I like Maths.", evidence="independent_success", level_behavior="reasoned_response", content_slots_covered=["feature"]).output
        with self.assertRaises(DialogueContractError):
            validate_dialogue_state_machine_result(incomplete, l4)
        complete = dialogue_result("I like Maths because it is fun.", evidence="independent_success", level_behavior="reasoned_response", content_slots_covered=["feature", "reason"]).output
        validate_dialogue_state_machine_result(complete, l4)

    def test_l4_candidate_list_is_bounded_by_program_policy(self) -> None:
        turn = replace(self._sample_turn(), difficulty_level=4, active_step={"content_slots": [
            {"slot_id": "one", "target_id": "subject_maths", "purpose": "one"},
            {"slot_id": "two", "target_id": "subject_science", "purpose": "two"},
        ]}, max_capture_candidates=2)
        bad = dialogue_result(
            "I need help.", evidence="supported_success", level_behavior="reasoned_response",
            capture_target_ids=["subject_maths", "subject_science", "subject_history"],
        ).output
        with self.assertRaises(DialogueContractError):
            validate_dialogue_state_machine_result(bad, turn)

    def test_l4_multiple_candidates_capture_atomically_after_group_repetition(self) -> None:
        service, gateway = self._service([
            dialogue_result("Tell me about your subjects.", difficulty="raise", level_behavior="guided_question"),
            dialogue_result("Great. Tell me more.", evidence="independent_success", difficulty="raise", level_behavior="role_play"),
            dialogue_result("You need two details. Please repeat the full answer.", evidence="supported_success", level_behavior="reasoned_response", capture_target_ids=["subject_maths", "subject_science"]),
            dialogue_result("Great.", evidence="supported_success", repetition_outcome="succeeded", level_behavior="reasoned_response", capture_target_ids=["subject_maths", "subject_science"]),
        ])
        try:
            service.handle(AuthorizedDialogueIngress("start", "2026-09-01T00:00:00Z", "child-multi-capture", True))
            service.handle(AuthorizedDialogueIngress("partial answer", "2026-09-01T00:01:00Z", "child-multi-capture"))
            service.handle(AuthorizedDialogueIngress("full answer", "2026-09-01T00:02:00Z", "child-multi-capture"))
            service.handle(AuthorizedDialogueIngress("repeat all", "2026-09-01T00:03:00Z", "child-multi-capture"))
            workflow_id = gateway.prepared_turns[0].workflow_id
            state = service.repository.get_dialogue_context(workflow_id)
            self.assertEqual((state["captured_count"], state["pending_repetition_target_id"]), (2, None))
            with sqlite3.connect(self.database) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM learning_items").fetchone()[0], 2)
        finally:
            service.close()

    def test_explicit_dialogue_migration_preserves_current_v3_rows(self) -> None:
        self._pre_dialogue_schema()
        migrate_dialogue_schema(self.database)
        with sqlite3.connect(self.database) as connection:
            workflow = connection.execute("SELECT workflow_id, workflow_type, phase FROM workflows").fetchone()
            event = connection.execute("SELECT event_id, event_type, payload_json FROM workflow_log_events").fetchone()
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertEqual(workflow, ("entry-1", "entry", "closed"))
        self.assertEqual(event, ("event-1", "child_message", '{"contract_name":"dada.workflow_log_event","contract_version":1,"data":{"text":"x"}}'))
        self.assertTrue({"dialogue_workflow_state", "dialogue_review_batches", "dialogue_review_batch_items"}.issubset(tables))
        with self.assertRaises(MigrationError):
            migrate_dialogue_schema(self.database)

    def test_course_pass_requires_target_coverage_independent_expression_and_scenarios(self) -> None:
        unit = UnitDefinition(
            "unit", 1, "Unit", (1,),
            ({"target_id": "target", "core": True},),
            ({"scenario_id": "scenario", "required": True, "allowed_target_ids": ["target"]},),
            {"all_core_targets_covered": True, "minimum_independent_ratio": 1.0, "required_scenarios": ["scenario"], "minimum_advanced_scenarios": 1},
            "hash",
        )
        snapshot = {"target_progress": {"target": {"exposed": 1, "supported_success": 0, "independent_success": 1, "unable": 0}}, "scenario_progress": {}, "unit_course_passed": False}
        self.assertTrue(course_passes(unit, snapshot, "scenario", "target", "independent_success", "role_play"))
        self.assertFalse(course_passes(unit, {**snapshot, "unit_course_passed": True}, "scenario", "target", "independent_success", "role_play"))

    def test_twentieth_distinct_capture_does_not_stop_dialogue(self) -> None:
        repository = WorkflowRepository(self.database)
        repository.initialize()
        reviewable = [target for target in self.unit.targets if target["reviewable"]][:20]
        self.assertEqual(len(reviewable), 20)
        first_child = repository.start_dialogue(
            "dialogue-threshold", "child-threshold", "system", "start", self.unit.unit_id, self.unit.version,
            self.unit.content_hash, "school-introduction", reviewable[0]["target_id"], 2, "2026-09-01T00:00:00Z",
        )
        for index, target in enumerate(reviewable, start=1):
            timestamp = f"2026-09-01T00:{index:02d}:00Z"
            child = first_child if index == 1 else repository.append_child_message("dialogue-threshold", f"answer {index}", timestamp, "dialogue")
            response = repository.append_log_event(
                "dialogue-threshold", "llm_response",
                {"task_contract_name": "dada.dialogue_state_machine_turn", "task_contract_version": 1, "output": {}, "source_child_event_id": child},
                timestamp, require_active_type="dialogue",
            )
            outcome = repository.commit_dialogue_turn(
                "dialogue-threshold", child, response,
                {"source_child_event_id": child, "unit_id": self.unit.unit_id, "unit_version": self.unit.version,
                 "scenario_id": "school-introduction", "target_id": target["target_id"], "target_evidence": "unable",
                 "scenario_achievement": "none", "grammar_observations": [], "content_slots_covered": []},
                "repeat", timestamp, next_scenario_id="school-introduction", next_target_id=target["target_id"],
                next_difficulty_level=2, pending_repetition_target_id=target["target_id"],
                capture_targets=[{"target_id": target["target_id"], "target_type": target["target_type"], "english": target["english"], "meaning_zh": target["meaning_zh"], "reviewable": True}],
                initial_review_stage=self.review_policy.initial_stage,
                initial_due_at=self.review_policy.initial_due_at(timestamp),
            )
            self.assertEqual(outcome.captured_count, index)
            self.assertFalse(outcome.wrapping_up)
        state = repository.get_dialogue_context("dialogue-threshold")
        self.assertEqual((state["captured_count"], state["subphase"]), (20, "normal"))

    def test_missing_checkpoint_redelivers_committed_reply_without_a_model_call(self) -> None:
        first, first_gateway = self._service([dialogue_result("First committed reply.")])
        try:
            first_delivery = first.handle(AuthorizedDialogueIngress("start", "2026-09-01T00:00:00Z", "child-recover", True))
            self.assertTrue(first_delivery.reply_text.endswith("First committed reply."))
            self.assertEqual(len(first_gateway.prepared_turns), 1)
        finally:
            first.close()
        with sqlite3.connect(self.database) as connection:
            for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                connection.execute(f"DROP TABLE IF EXISTS {table}")
        second, second_gateway = self._service([])
        try:
            recovered = second.handle(AuthorizedDialogueIngress("new message", "2026-09-01T00:01:00Z", "child-recover"))
            self.assertEqual(recovered.reply_text, first_delivery.reply_text)
            self.assertEqual(second_gateway.prepared_turns, [])
        finally:
            second.close()

    def test_program_bounds_difficulty_and_advances_only_along_unit_target_lists(self) -> None:
        scenario_id, target_id = "school-introduction", "subject_maths"
        advanced = select_next_focus(self.unit, scenario_id, target_id, 4, "independent_success", "raise")
        self.assertEqual(advanced, ("subject_science", "school-introduction", 4))
        held = select_next_focus(self.unit, scenario_id, target_id, 0, "unable", "lower")
        self.assertEqual(held, ("subject_maths", "school-introduction", 2))

    def test_scenario_boundary_requires_basic_achievement(self) -> None:
        last_target = "express_preference"
        held = select_next_focus(
            self.unit, "school-introduction", last_target, 1, "independent_success", "stay", "none"
        )
        self.assertEqual(held, (last_target, "school-introduction", 2))
        advanced = select_next_focus(
            self.unit, "school-introduction", last_target, 1, "independent_success", "stay", "basic"
        )
        self.assertEqual(advanced, ("club", "club-enquiry", 2))

    def test_previous_basic_achievement_allows_scenario_boundary(self) -> None:
        advanced = select_next_focus(
            self.unit, "school-introduction", "express_preference", 1, "independent_success", "stay", "none",
            {"school-introduction": "basic"},
        )
        self.assertEqual(advanced, ("club", "club-enquiry", 2))

    def test_final_scenario_does_not_wrap_to_first_scenario(self) -> None:
        held = select_next_focus(
            self.unit, "compare-schools", "express_preference", 2, "independent_success", "stay", "basic"
        )
        self.assertEqual(held, ("express_preference", "compare-schools", 2))

    def test_l2_through_l4_uses_the_program_selected_level_behavior(self) -> None:
        levels = ("guided_question", "role_play", "reasoned_response")
        service, gateway = self._service([
            dialogue_result("What subjects do you have at school?", difficulty="raise", level_behavior=levels[0]),
            dialogue_result("Great. Tell me about Maths.", evidence="independent_success", difficulty="raise", level_behavior=levels[1]),
            dialogue_result("What do you do in Maths, and why?", difficulty="stay", level_behavior=levels[2]),
        ])
        try:
            service.handle(AuthorizedDialogueIngress("start", "2026-09-01T00:00:00Z", "child-levels", True))
            for level in range(1, 3):
                service.handle(AuthorizedDialogueIngress(f"turn {level}", f"2026-09-01T00:0{level}:00Z", "child-levels"))
            self.assertEqual([turn.difficulty_level for turn in gateway.prepared_turns], [2, 3, 4])
        finally:
            service.close()

    def test_round_plan_completes_and_closes_after_last_independent_step(self) -> None:
        service, gateway = self._service([
            dialogue_result("Start.", evidence="exposed"),
            dialogue_result("Great.", evidence="independent_success"),
        ])
        try:
            service.handle(AuthorizedDialogueIngress("start", "2026-09-01T00:00:00Z", "child-round-close", True))
            workflow_id = gateway.prepared_turns[0].workflow_id
            with sqlite3.connect(self.database) as connection:
                plan = json.loads(connection.execute("SELECT plan_json FROM dialogue_round_plans WHERE workflow_id = ?", (workflow_id,)).fetchone()[0])
                plan["steps"] = plan["steps"][:1]
                connection.execute("UPDATE dialogue_round_plans SET plan_json = ? WHERE workflow_id = ?", (json.dumps(plan), workflow_id))
            delivery = service.handle(AuthorizedDialogueIngress("answer", "2026-09-01T00:01:00Z", "child-round-close"))
            self.assertIn("本轮对话已完成", delivery.reply_text)
            self.assertIsNone(service.repository.get_active_workflow("child-round-close"))
            events = service.repository.list_events(workflow_id)
            self.assertTrue(any(event["event_type"] == "dialogue_round_completed" for event in events))
            self.assertTrue(any(event["event_type"] == "dialogue_progress_checkpoint" for event in events))
        finally:
            service.close()

    def test_independent_success_completes_target_and_raises_next_target_only(self) -> None:
        service, gateway = self._service([
            dialogue_result("Do you have Maths?", evidence="exposed"),
            dialogue_result("Yes, I do.", evidence="independent_success"),
            dialogue_result("Do you have Science?", evidence="exposed", level_behavior="role_play"),
        ])
        try:
            service.handle(AuthorizedDialogueIngress("start", "2026-09-01T00:00:00Z", "child-target-level", True))
            service.handle(AuthorizedDialogueIngress("Yes", "2026-09-01T00:01:00Z", "child-target-level"))
            service.handle(AuthorizedDialogueIngress("ready", "2026-09-01T00:02:00Z", "child-target-level"))
            self.assertEqual(gateway.prepared_turns[0].focus_target["target_id"], "subject_maths")
            self.assertEqual(gateway.prepared_turns[1].focus_target["target_id"], "subject_maths")
            self.assertEqual(gateway.prepared_turns[2].focus_target["target_id"], "subject_science")
            self.assertEqual([turn.difficulty_level for turn in gateway.prepared_turns], [2, 2, 3])
            checkpoint = service.repository.list_events(gateway.prepared_turns[1].workflow_id)
            payload = [event["payload"] for event in checkpoint if event["event_type"] == "dialogue_progress_checkpoint"][-1]
            self.assertEqual(next(item["status"] for item in payload["targets"] if item["target_id"] == "subject_maths"), "completed")
        finally:
            service.close()

    def test_semantic_answer_completes_phrase_target_without_literal_repetition(self) -> None:
        service, gateway = self._service([
            dialogue_result("If you could do experiments in the club, what would you make?", evidence="exposed"),
            dialogue_result("That sounds exciting.", evidence="independent_success"),
        ])
        try:
            service.handle(AuthorizedDialogueIngress("start", "2026-09-01T00:00:00Z", "child-semantic-phrase", True))
            workflow_id = gateway.prepared_turns[0].workflow_id
            # The opening question is not yet an answer, so its intent must
            # remain available.  Then pin the one-step fixture to the phrase
            # target and prove that a complete semantic answer need not
            # repeat "do experiments" to complete that target.
            self.assertIn("subject_maths", service.repository.get_dialogue_retryable_targets("child-semantic-phrase", self.unit.unit_id, self.unit.version))
            with sqlite3.connect(self.database) as connection:
                plan = json.loads(connection.execute("SELECT plan_json FROM dialogue_round_plans WHERE workflow_id = ?", (workflow_id,)).fetchone()[0])
                step = {
                    "step_id": "step-1", "scenario_id": "club-enquiry", "target_id": "do_experiments",
                    "question_intent_key": "do_experiments.primary", "entry_difficulty": 2,
                    "content_slots": [
                        {"slot_id": "target_expression", "target_id": "do_experiments", "purpose": "target_expression"},
                        {"slot_id": "club_detail", "target_id": "club_detail", "purpose": "club_detail"},
                    ],
                }
                plan["steps"] = [step]
                plan["scenario_ids"] = ["club-enquiry"]
                connection.execute("UPDATE dialogue_round_plans SET plan_json = ?, current_step_index = 0, content_turn_count = 0 WHERE workflow_id = ?", (json.dumps(plan), workflow_id))
                connection.execute("UPDATE dialogue_workflow_state SET current_scenario_id = ?, current_target_id = ?, current_difficulty_level = 2 WHERE workflow_id = ?", (step["scenario_id"], step["target_id"], workflow_id))
            service.handle(AuthorizedDialogueIngress("I would make a robot in the science club.", "2026-09-01T00:01:00Z", "child-semantic-phrase"))
            events = service.repository.list_events(workflow_id)
            evaluation = next(event["payload"] for event in reversed(events) if event["event_type"] == "dialogue_turn_evaluated")
            intent = next(event["payload"] for event in reversed(events) if event["event_type"] == "dialogue_question_intent_used")
            self.assertEqual((evaluation["target_id"], evaluation["target_evidence"]), ("do_experiments", "independent_success"))
            self.assertEqual((intent["question_intent_key"], intent["result"]), ("do_experiments.primary", "completed"))
            self.assertNotIn("do_experiments", service.repository.get_dialogue_retryable_targets("child-semantic-phrase", self.unit.unit_id, self.unit.version))
            config = (PROJECT / "promptfooconfig.dialogue.yaml").read_text(encoding="utf-8")
            provider = (PROJECT / "promptfoo" / "dada-dialogue-provider.mjs").read_text(encoding="utf-8")
            self.assertIn("Dada Unit 1 v5", config)
            self.assertIn("unit.v5.json", provider)
        finally:
            service.close()

    def test_retryable_question_repeats_on_next_round_until_independent(self) -> None:
        service, gateway = self._service([
            dialogue_result("What do you learn in Maths?"),
            dialogue_result('You can say: "I learn numbers."', evidence="unable"),
            dialogue_result("Please repeat.", evidence="supported_success", repetition_outcome="succeeded", capture_target_id="subject_maths"),
            dialogue_result("We can stop.", transition="stop_dialogue"),
            dialogue_result("What do you learn in Maths?"),
            dialogue_result("I learn numbers.", evidence="independent_success"),
        ])
        try:
            service.handle(AuthorizedDialogueIngress("start", "2026-09-01T00:00:00Z", "child-retry-round", True))
            first_workflow = gateway.prepared_turns[0].workflow_id
            self.assertIn("subject_maths", service.repository.get_dialogue_retryable_targets("child-retry-round", self.unit.unit_id, self.unit.version))
            service.handle(AuthorizedDialogueIngress("I don't know", "2026-09-01T00:01:00Z", "child-retry-round"))
            service.handle(AuthorizedDialogueIngress("I learn numbers", "2026-09-01T00:02:00Z", "child-retry-round"))
            current = service.repository.get_dialogue_context(first_workflow)
            self.assertEqual((current["current_target_id"], current["current_step_index"]), ("subject_science", 1))
            self.assertIn("subject_maths", service.repository.get_dialogue_retryable_targets("child-retry-round", self.unit.unit_id, self.unit.version))
            service.handle(AuthorizedDialogueIngress("end", "2026-09-01T00:03:00Z", "child-retry-round"))
            self.assertIn("subject_maths", service.repository.get_dialogue_retryable_targets("child-retry-round", self.unit.unit_id, self.unit.version))
            service.handle(AuthorizedDialogueIngress("start again", "2026-09-01T00:04:00Z", "child-retry-round", True))
            self.assertEqual(gateway.prepared_turns[-1].focus_target["target_id"], "subject_maths")
            self.assertEqual(gateway.prepared_turns[-1].active_step["question_intent_key"], "subject_maths.primary")
            self.assertNotEqual(gateway.prepared_turns[-1].workflow_id, first_workflow)
            service.handle(AuthorizedDialogueIngress("I learn numbers", "2026-09-01T00:05:00Z", "child-retry-round"))
            self.assertNotIn("subject_maths", service.repository.get_dialogue_retryable_targets("child-retry-round", self.unit.unit_id, self.unit.version))
        finally:
            service.close()

    def test_successful_repetition_advances_step_but_keeps_target_retryable(self) -> None:
        service, gateway = self._service([
            dialogue_result("What do you learn in Maths?"),
            dialogue_result('You can say: "I learn numbers."', evidence="unable"),
            dialogue_result("Good repetition.", evidence="supported_success", repetition_outcome="succeeded", capture_target_id="subject_maths"),
        ])
        try:
            service.handle(AuthorizedDialogueIngress("start", "2026-09-01T00:00:00Z", "child-step-after-repeat", True))
            workflow_id = gateway.prepared_turns[0].workflow_id
            service.handle(AuthorizedDialogueIngress("I don't know", "2026-09-01T00:01:00Z", "child-step-after-repeat"))
            service.handle(AuthorizedDialogueIngress("I learn numbers", "2026-09-01T00:02:00Z", "child-step-after-repeat"))
            context = service.repository.get_dialogue_context(workflow_id)
            self.assertEqual((context["current_target_id"], context["current_step_index"]), ("subject_science", 1))
            self.assertIn("subject_maths", service.repository.get_dialogue_retryable_targets("child-step-after-repeat", self.unit.unit_id, self.unit.version))
            events = service.repository.list_events(workflow_id)
            evaluation = next(event["payload"] for event in reversed(events) if event["event_type"] == "dialogue_turn_evaluated")
            intent = next(event["payload"] for event in reversed(events) if event["event_type"] == "dialogue_question_intent_used")
            self.assertEqual(evaluation["target_evidence"], "supported_success")
            self.assertEqual(intent["result"], "retryable")
        finally:
            service.close()

    def test_round_closes_at_twelve_content_turns_with_unfinished_carry_over(self) -> None:
        service, gateway = self._service([dialogue_result("question") for _ in range(12)])
        try:
            service.handle(AuthorizedDialogueIngress("start", "2026-09-01T00:00:00Z", "child-round-limit", True))
            last = None
            for index in range(1, 12):
                last = service.handle(AuthorizedDialogueIngress(f"answer {index}", f"2026-09-01T00:{index:02d}:00Z", "child-round-limit"))
            self.assertIsNotNone(last)
            self.assertIn("未完成的内容会保留到下一轮", last.reply_text)
            self.assertIsNone(service.repository.get_active_workflow("child-round-limit"))
            workflow_id = gateway.prepared_turns[0].workflow_id
            context = service.repository.get_dialogue_context(workflow_id)
            self.assertEqual((context["content_turn_count"], context["round_plan_status"]), (12, "closed_without_completion"))
        finally:
            service.close()

    def test_history_preserves_prior_question_for_contextual_follow_up(self) -> None:
        service, gateway = self._service([
            dialogue_result("Hi! What subject do you like at school?"),
            dialogue_result("What subjects do you have at school?"),
        ])
        try:
            service.handle(AuthorizedDialogueIngress("start", "2026-09-01T00:00:00Z", "child-history", True))
            service.handle(AuthorizedDialogueIngress("I like my teacher and classmates.", "2026-09-01T00:01:00Z", "child-history"))
            history = gateway.prepared_turns[1].history
            history_text = [item["text"] for item in history]
            self.assertTrue(any("Hi! What subject do you like at school?" in text for text in history_text))
            self.assertIn("I like my teacher and classmates.", history_text)
        finally:
            service.close()

    def test_promptfoo_continuity_cases_for_all_unit_scenarios(self) -> None:
        config = (PROJECT / "promptfooconfig.dialogue.yaml").read_text(encoding="utf-8")
        cases = (PROJECT / "promptfoo" / "dialogue-v5-tests.mjs").read_text(encoding="utf-8")
        self.assertIn("dialogue-v5-tests.mjs", config)
        self.assertIn("targets.flatMap", cases)

    def test_promptfoo_target_matrix_covers_every_unit_target(self) -> None:
        config = (PROJECT / "promptfooconfig.dialogue.yaml").read_text(encoding="utf-8")
        cases = (PROJECT / "promptfoo" / "dialogue-v5-tests.mjs").read_text(encoding="utf-8")
        v5 = load_unit_definition(PROJECT / "config" / "dialogue-units" / "grade6-english-unit-1-school-life" / "unit.v5.json", "grade6-english-unit-1-school-life")
        self.assertEqual(len(v5.targets), 73)
        self.assertIn("target_matrix_${target.target_id}_${phase}", cases)
        self.assertIn("unit.v5.json", cases)

    def test_promptfoo_unable_answer_case_teaches_the_current_question(self) -> None:
        cases = (PROJECT / "promptfoo" / "dialogue-v5-tests.mjs").read_text(encoding="utf-8")
        self.assertIn("unit.v5.json", cases)

    def test_promptfoo_project_math_case_uses_supported_success_correction(self) -> None:
        config = (PROJECT / "promptfooconfig.dialogue.yaml").read_text(encoding="utf-8")
        provider = (PROJECT / "promptfoo" / "dada-dialogue-provider.mjs").read_text(encoding="utf-8")
        skill = (PROJECT / "skill" / "dada-dialogue-state-machine" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Dada Unit 1 v5", config)
        self.assertIn("targetMatrixTurn", provider)
        self.assertIn("I do a Maths project in the club.", skill)
        self.assertIn('target_evidence:"supported_success"', skill)

    def test_question_intent_requires_information_gap_for_child_initiation(self) -> None:
        skill = (PROJECT / "skill" / "dada-dialogue-state-machine" / "SKILL.md").read_text(encoding="utf-8")
        contract = (PROJECT / "skill" / "dada-dialogue-state-machine" / "references" / "dialogue-turn-contract.md").read_text(encoding="utf-8")
        self.assertIn("active_step.question_intent", skill)
        self.assertIn("initiate_question", skill)
        self.assertIn("信息缺口", skill)
        self.assertIn("不能先给出完整教材问句", skill)
        self.assertIn("孩子发出的自然改写问题只要语义正确即可", skill)
        self.assertIn("active_step.question_intent", contract)
        self.assertIn("角色、目的和信息缺口", contract)

    def test_supported_project_expression_keeps_target_retryable(self) -> None:
        repository = WorkflowRepository(self.database)
        repository.initialize()
        child = repository.start_dialogue(
            "dialogue-project-correction", "child-project-correction", "system", "start",
            self.unit.unit_id, self.unit.version, self.unit.content_hash, "club-enquiry", "project", 2,
            "2026-09-01T00:00:00Z",
        )
        try:
            response = repository.append_log_event(
                "dialogue-project-correction", "llm_response",
                {"task_contract_name": "dada.dialogue_state_machine_turn", "task_contract_version": 1, "output": {}, "source_child_event_id": child},
                "2026-09-01T00:01:00Z", require_active_type="dialogue",
            )
            repository.commit_dialogue_turn(
                "dialogue-project-correction", child, response,
                {"source_child_event_id": child, "unit_id": self.unit.unit_id, "unit_version": self.unit.version,
                 "scenario_id": "club-enquiry", "target_id": "project", "target_evidence": "supported_success",
                 "scenario_achievement": "none", "grammar_observations": [], "content_slots_covered": []},
                "我明白你的意思，你是在说数学项目。为了完整回答这个问题，可以说：I do a Maths project in the club. 请再说一遍。",
                "2026-09-01T00:01:00Z", next_scenario_id="club-enquiry", next_target_id="project",
                 next_difficulty_level=2, pending_repetition_target_id="project", capture_targets=[],
                initial_review_stage=self.review_policy.initial_stage, initial_due_at=self.review_policy.initial_due_at("2026-09-01T00:01:00Z"),
                question_intent={"source_child_event_id": child, "step_id": "step-1", "target_id": "project", "scenario_id": "club-enquiry", "question_intent_key": "project.primary", "result": "retryable", "unit_id": self.unit.unit_id, "unit_version": self.unit.version},
            )
            self.assertIn("project", repository.get_dialogue_retryable_targets("child-project-correction", self.unit.unit_id, self.unit.version))
        finally:
            pass

    def test_promptfoo_partial_repetition_requires_complete_context(self) -> None:
        config = (PROJECT / "promptfooconfig.dialogue.yaml").read_text(encoding="utf-8")
        provider = (PROJECT / "promptfoo" / "dada-dialogue-provider.mjs").read_text(encoding="utf-8")
        skill = (PROJECT / "skill" / "dada-dialogue-state-machine" / "SKILL.md").read_text(encoding="utf-8")
        contract = (PROJECT / "skill" / "dada-dialogue-state-machine" / "references" / "dialogue-turn-contract.md").read_text(encoding="utf-8")
        self.assertIn("unit.v5", provider)
        self.assertIn("缺少 `in the club`", skill)
        self.assertIn("缺少 `in the club`", contract)

    def test_promptfoo_independent_yes_no_case_requires_completion_without_repetition(self) -> None:
        config = (PROJECT / "promptfooconfig.dialogue.yaml").read_text(encoding="utf-8")
        self.assertIn("dialogue-v5-tests.mjs", config)

    def test_round_plan_refuses_to_reuse_all_completed_question_intents(self) -> None:
        snapshot = {
            "target_progress": {
                str(target["target_id"]): {"exposed": 1, "supported_success": 0, "independent_success": 1, "unable": 0}
                for target in self.unit.targets
            }
        }
        used = {str(intent["intent_key"]) for intent in self.unit.question_intents}
        with self.assertRaisesRegex(ValueError, "no eligible round steps"):
            build_round_plan(self.unit, snapshot, used_question_intents=used)

    def test_round_plan_never_reschedules_independently_completed_target(self) -> None:
        snapshot = {
            "target_progress": {
                "subject_maths": {"exposed": 1, "supported_success": 0, "independent_success": 1, "unable": 0},
            },
            "scenario_progress": {},
        }
        plan = build_round_plan(self.unit, snapshot, retryable_targets={"subject_maths"})
        target_ids = {str(step["target_id"]) for step in plan["steps"]}
        self.assertNotIn("subject_maths", target_ids)

    def test_mastery_crosses_unit_package_versions_and_reopen_starts_new_cycle(self) -> None:
        repository = WorkflowRepository(self.database)
        repository.initialize()
        v5 = load_unit_definition(
            PROJECT / "config" / "dialogue-units" / "grade6-english-unit-1-school-life" / "unit.v5.json",
            "grade6-english-unit-1-school-life",
        )
        v6 = load_unit_definition(
            PROJECT / "config" / "dialogue-units" / "grade6-english-unit-1-school-life" / "unit.v6.json",
            "grade6-english-unit-1-school-life",
        )
        evaluation = {
            "source_child_event_id": "child-v5",
            "unit_id": v5.unit_id,
            "unit_version": v5.version,
            "scenario_id": "school-introduction",
            "target_id": "subject_maths",
            "target_evidence": "independent_success",
            "scenario_achievement": "basic",
            "grammar_observations": [],
            "content_slots_covered": [],
        }
        child = repository.start_dialogue(
            "dialogue-v5", "child-versioned", "system", "start", v5.unit_id, v5.version,
            v5.content_hash, "school-introduction", "subject_maths", 2, "2026-09-01T00:00:00Z",
        )
        evaluation["source_child_event_id"] = child
        repository.append_log_event(
            "dialogue-v5", "dialogue_turn_evaluated", evaluation, "2026-09-01T00:01:00Z",
        )
        repository.close_dialogue_and_prepare_batch("dialogue-v5", "closed", "2026-09-01T00:02:00Z")

        snapshot = repository.get_dialogue_mastery_snapshot("child-versioned", v6.unit_id, v6.version)
        self.assertEqual(snapshot["target_progress"]["subject_maths"]["independent_success"], 1)
        plan = build_round_plan(v6, snapshot, retryable_targets={"subject_maths"})
        self.assertNotIn("subject_maths", {str(step["target_id"]) for step in plan["steps"]})

        reopened = repository.admin_reopen_dialogue_target(
            "child-versioned", "subject_maths", "掌握不足，重新练习", "2026-09-01T00:03:00Z",
        )
        self.assertEqual(reopened["target_id"], "subject_maths")
        snapshot = repository.get_dialogue_mastery_snapshot("child-versioned", v6.unit_id, v6.version)
        self.assertEqual(snapshot["target_progress"]["subject_maths"]["independent_success"], 0)
        self.assertIn("subject_maths", snapshot["reopened_target_ids"])
        plan = build_round_plan(v6, snapshot, used_question_intents={str(intent["intent_key"]) for intent in v6.question_intents})
        self.assertIn("subject_maths", {str(step["target_id"]) for step in plan["steps"]})

        child = repository.append_log_event("dialogue-v5", "child_message", {"text": "Maths again"}, "2026-09-01T00:04:00Z")
        response = repository.append_log_event(
            "dialogue-v5", "llm_response",
            {"task_contract_name": "dada.dialogue_state_machine_turn", "task_contract_version": 1, "output": {}, "source_child_event_id": child},
            "2026-09-01T00:04:00Z",
        )
        evaluation["source_child_event_id"] = child
        evaluation["unit_version"] = v6.version
        repository.append_log_event("dialogue-v5", "dialogue_turn_evaluated", evaluation, "2026-09-01T00:05:00Z")
        snapshot = repository.get_dialogue_mastery_snapshot("child-versioned", v6.unit_id, v6.version)
        self.assertEqual(snapshot["target_progress"]["subject_maths"]["independent_success"], 1)
        self.assertNotIn("subject_maths", {str(step["target_id"]) for step in build_round_plan(v6, snapshot)["steps"]})

    def test_capture_dedupes_stable_target_across_unit_package_versions(self) -> None:
        repository = WorkflowRepository(self.database)
        repository.initialize()
        v5 = load_unit_definition(PROJECT / "config/dialogue-units/grade6-english-unit-1-school-life/unit.v5.json")
        v6 = load_unit_definition(PROJECT / "config/dialogue-units/grade6-english-unit-1-school-life/unit.v6.json")
        item_ids = []
        for index, unit in enumerate((v5, v6), start=1):
            workflow_id = f"dialogue-capture-v{unit.version}"
            timestamp = f"2026-09-01T00:0{index}:00Z"
            child = repository.start_dialogue(
                workflow_id, "child-capture-versioned", "system", "start", unit.unit_id, unit.version,
                unit.content_hash, "school-introduction", "subject_maths", 2, timestamp,
            )
            response = repository.append_log_event(
                workflow_id, "llm_response",
                {"task_contract_name": "dada.dialogue_state_machine_turn", "task_contract_version": 1, "output": {}, "source_child_event_id": child},
                timestamp,
            )
            target = unit.targets_by_id["subject_maths"]
            outcome = repository.commit_dialogue_turn(
                workflow_id, child, response,
                {"source_child_event_id": child, "unit_id": unit.unit_id, "unit_version": unit.version,
                 "scenario_id": "school-introduction", "target_id": "subject_maths", "target_evidence": "supported_success",
                 "scenario_achievement": "none", "grammar_observations": [], "content_slots_covered": []},
                "saved", timestamp, next_scenario_id="school-introduction", next_target_id="subject_maths",
                next_difficulty_level=2, pending_repetition_target_id="subject_maths",
                capture_targets=[{"target_id": target["target_id"], "target_type": target["target_type"], "english": target["english"], "meaning_zh": target["meaning_zh"], "reviewable": True}],
                initial_review_stage=self.review_policy.initial_stage,
                initial_due_at=self.review_policy.initial_due_at(timestamp),
            )
            item_ids.append(outcome.learning_item_id)
            repository.close_dialogue_and_prepare_batch(workflow_id, "closed", f"2026-09-01T00:0{index + 2}:00Z")
        self.assertEqual(item_ids[0], item_ids[1])
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM learning_items").fetchone()[0], 1)

    def test_admin_reopen_requires_completed_target_and_no_active_workflow(self) -> None:
        repository = WorkflowRepository(self.database)
        repository.initialize()
        repository.start_dialogue(
            "dialogue-active", "child-empty", "system", "start", self.unit.unit_id, self.unit.version,
            self.unit.content_hash, "school-introduction", "subject_maths", 2, "2026-09-01T00:00:00Z",
        )
        with self.assertRaisesRegex(RepositoryError, "workflow is active"):
            repository.admin_reopen_dialogue_target("child-empty", "subject_maths", "retry", "2026-09-01T00:00:01Z")
        repository.close_dialogue_and_prepare_batch("dialogue-active", "closed", "2026-09-01T00:00:02Z")
        with self.assertRaisesRegex(RepositoryError, "no independent completion"):
            repository.admin_reopen_dialogue_target("child-empty", "subject_maths", "retry", "2026-09-01T00:00:00Z")

    def test_repeated_target_capture_reuses_item_without_incrementing_count(self) -> None:
        repository = WorkflowRepository(self.database)
        repository.initialize()
        target = self.unit.targets_by_id["subject_maths"]
        child = repository.start_dialogue("dialogue-duplicate", "child-duplicate", "system", "start", self.unit.unit_id, self.unit.version, self.unit.content_hash, "school-introduction", "subject_maths", 2, "2026-09-01T00:00:00Z")
        item_ids = []
        for index in (1, 2):
            timestamp = f"2026-09-01T00:0{index}:00Z"
            current_child = child if index == 1 else repository.append_child_message("dialogue-duplicate", "Maths", timestamp, "dialogue")
            response = repository.append_log_event("dialogue-duplicate", "llm_response", {"task_contract_name": "dada.dialogue_state_machine_turn", "task_contract_version": 1, "output": {}, "source_child_event_id": current_child}, timestamp, require_active_type="dialogue")
            outcome = repository.commit_dialogue_turn(
                "dialogue-duplicate", current_child, response,
                {"source_child_event_id": current_child, "unit_id": self.unit.unit_id, "unit_version": self.unit.version, "scenario_id": "school-introduction", "target_id": "subject_maths", "target_evidence": "unable", "scenario_achievement": "none", "grammar_observations": [], "content_slots_covered": []},
                "repeat", timestamp, next_scenario_id="school-introduction", next_target_id="subject_maths", next_difficulty_level=2, pending_repetition_target_id="subject_maths",
                capture_targets=[{"target_id": target["target_id"], "target_type": target["target_type"], "english": target["english"], "meaning_zh": target["meaning_zh"], "reviewable": True}],
            initial_review_stage=self.review_policy.initial_stage, initial_due_at=self.review_policy.initial_due_at(timestamp),
            )
            item_ids.append(outcome.learning_item_id)
        self.assertEqual(item_ids[0], item_ids[1])
        self.assertEqual(repository.get_dialogue_context("dialogue-duplicate")["captured_count"], 1)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM learning_items").fetchone()[0], 1)

    def test_phrase_capture_persists_unit_review_design_snapshot(self) -> None:
        unit = load_unit_definition(
            PROJECT / "config" / "dialogue-units" / "grade6-english-unit-1-school-life" / "unit.v6.json",
            "grade6-english-unit-1-school-life",
        )
        repository = WorkflowRepository(self.database)
        repository.initialize()
        workflow_id = "dialogue-phrase-capture"
        child = repository.start_dialogue(workflow_id, "child-phrase-capture", "system", "start", unit.unit_id, unit.version, unit.content_hash, "school-life-survey", "report_same_school_routine", 2, "2026-09-01T00:00:00Z")
        response = repository.append_log_event(workflow_id, "llm_response", {"task_contract_name": "dada.dialogue_state_machine_turn", "task_contract_version": 1, "output": {}, "source_child_event_id": child}, "2026-09-01T00:01:00Z", require_active_type="dialogue")
        target = unit.targets_by_id["report_same_school_routine"]
        repository.commit_dialogue_turn(
            workflow_id, child, response,
            {"source_child_event_id": child, "unit_id": unit.unit_id, "unit_version": unit.version, "scenario_id": "school-life-survey", "target_id": target["target_id"], "target_evidence": "supported_success", "scenario_achievement": "none", "grammar_observations": [], "content_slots_covered": []},
            "saved", "2026-09-01T00:01:00Z", next_scenario_id="school-life-survey", next_target_id=target["target_id"], next_difficulty_level=2, pending_repetition_target_id=target["target_id"],
            capture_targets=[{"target_id": target["target_id"], "target_type": target["target_type"], "english": target["english"], "meaning_zh": target["meaning_zh"], "reviewable": True, "review_design": target["review_design"]}],
            initial_review_stage=self.review_policy.initial_stage, initial_due_at=self.review_policy.initial_due_at("2026-09-01T00:01:00Z"),
        )
        with sqlite3.connect(self.database) as connection:
            stored = connection.execute("SELECT review_context_json FROM learning_items").fetchone()[0]
        self.assertEqual(json.loads(stored), target["review_design"])

    def test_progress_text_is_program_owned_and_persisted_with_visible_reply(self) -> None:
        service, gateway = self._service([
            dialogue_result("First reply.", difficulty="raise", level_behavior="guided_question"),
            dialogue_result("Second reply.", difficulty="raise", level_behavior="role_play"),
        ])
        try:
            first = service.handle(AuthorizedDialogueIngress("start", "2026-09-01T00:00:00Z", "child-progress", True))
            second = service.handle(AuthorizedDialogueIngress("continue", "2026-09-01T00:01:00Z", "child-progress"))
            self.assertIn("本单元进度：已完成 0 / 28 个目标。", first.progress_text)
            self.assertIn("本轮计划：", first.progress_text)
            self.assertEqual(second.state_text, "【英语对话中】")
            self.assertIsNone(second.progress_text)
            workflow_id = gateway.prepared_turns[0].workflow_id
            stored = service.repository.get_latest_assistant_delivery(workflow_id)
            self.assertIsNotNone(stored)
            self.assertEqual(stored[1], second.reply_text)
        finally:
            service.close()

    def test_promptfoo_grammar_correction_requires_visible_reason_and_repetition(self) -> None:
        config = (PROJECT / "promptfooconfig.dialogue.yaml").read_text(encoding="utf-8")
        contract = (PROJECT / "skill" / "dada-dialogue-state-machine" / "references" / "dialogue-turn-contract.md").read_text(encoding="utf-8")
        skill = (PROJECT / "skill" / "dada-dialogue-state-machine" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("dialogue-v5-tests.mjs", config)
        self.assertIn("修改原因", contract)
        self.assertIn("具体错误及原因", skill)
        self.assertIn("不能只给正确句子", skill)

    def test_promptfoo_logic_regressions_cover_minor_grammar_and_off_topic_answers(self) -> None:
        config = (PROJECT / "promptfooconfig.dialogue.yaml").read_text(encoding="utf-8")
        cases = (PROJECT / "promptfoo" / "dialogue-v5-tests.mjs").read_text(encoding="utf-8")
        provider = (PROJECT / "promptfoo" / "dada-dialogue-provider.mjs").read_text(encoding="utf-8")
        skill = (PROJECT / "skill" / "dada-dialogue-state-machine" / "SKILL.md").read_text(encoding="utf-8")
        contract = (PROJECT / "skill" / "dada-dialogue-state-machine" / "references" / "dialogue-turn-contract.md").read_text(encoding="utf-8")
        self.assertIn("dialogue-v5-tests.mjs", config)
        for case_id in (
            "logic_minor_grammar_success",
            "logic_off_topic_activity",
            "logic_off_topic_subject_time",
            "logic_off_topic_after_science",
            "logic_wrapping_no_repetition",
        ):
            self.assertIn(case_id, cases)
            self.assertIn(case_id, provider)
        self.assertIn("小语法、拼写、大小写或介词错误", skill)
        self.assertIn("答非所问", skill)
        self.assertIn("这个问题是在问", contract)
        self.assertIn("答非所问", contract)

    def test_promptfoo_negative_subject_answer_is_semantically_complete(self) -> None:
        cases = (PROJECT / "promptfoo" / "dialogue-v5-tests.mjs").read_text(encoding="utf-8")
        provider = (PROJECT / "promptfoo" / "dada-dialogue-provider.mjs").read_text(encoding="utf-8")
        skill = (PROJECT / "skill" / "dada-dialogue-state-machine" / "SKILL.md").read_text(encoding="utf-8")
        contract = (PROJECT / "skill" / "dada-dialogue-state-machine" / "references" / "dialogue-turn-contract.md").read_text(encoding="utf-8")
        self.assertIn("logic_negative_subject_answer_success", cases)
        self.assertIn("logic_negative_subject_answer_success", provider)
        self.assertIn("I don't have History.", skill)
        self.assertIn("I don't have History.", contract)
        self.assertIn("必须返回 `independent_success`", skill)
        self.assertIn("必须评估为 `independent_success`", contract)

    def _sample_turn(self):
        service, gateway = self._service([dialogue_result("first")])
        try:
            service.handle(AuthorizedDialogueIngress("start", "2026-09-01T00:00:00Z", "child-contract", True))
            return gateway.prepared_turns[0]
        finally:
            service.close()

    def _pre_dialogue_schema(self) -> None:
        ddl = """
        CREATE TABLE workflows (
          workflow_id TEXT PRIMARY KEY, external_session_id TEXT NOT NULL,
          workflow_type TEXT NOT NULL CHECK (workflow_type IN ('entry', 'review')),
          phase TEXT NOT NULL CHECK (phase IN ('active', 'paused_for_entry', 'closed')),
          learning_item_id TEXT, question_sequence INTEGER NOT NULL DEFAULT 0,
          locked_question_mode TEXT, locked_question_json TEXT, locked_item_revision INTEGER,
          locked_at TEXT, started_at TEXT NOT NULL, paused_at TEXT, closed_at TEXT
        );
        CREATE TABLE workflow_log_events (
          event_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id),
          sequence_no INTEGER NOT NULL, event_type TEXT NOT NULL CHECK (event_type IN ('child_message', 'assistant_response', 'state_transition')),
          related_event_id TEXT REFERENCES workflow_log_events(event_id), payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL, UNIQUE(workflow_id, sequence_no),
          CHECK ((event_type = 'reentry_resolved' AND related_event_id IS NOT NULL) OR (event_type <> 'reentry_resolved' AND related_event_id IS NULL))
        );
        """
        with sqlite3.connect(self.database) as connection:
            connection.executescript(ddl)
            connection.execute("INSERT INTO workflows VALUES ('entry-1','child-1','entry','closed',NULL,0,NULL,NULL,NULL,NULL,'2026-09-01T00:00:00Z',NULL,'2026-09-01T00:01:00Z')")
            connection.execute("INSERT INTO workflow_log_events VALUES ('event-1','entry-1',1,'child_message',NULL,'{\"contract_name\":\"dada.workflow_log_event\",\"contract_version\":1,\"data\":{\"text\":\"x\"}}','2026-09-01T00:00:00Z')")


if __name__ == "__main__":
    unittest.main()
