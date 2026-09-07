from __future__ import annotations

import sqlite3
import json
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "runtime"))

from v3_review import AuthorizedReviewIngress, ReviewTurnService
from v3_review.contracts.review_turn import ReviewGatewayExecution
from v3_review.gateway import FakeReviewModelGateway, GatewayError, assessment_result, locked_question_guidance_result, question_result, transition_result
from v3_review.question_modes import allowed_question_modes, choose_question_mode
from v3_workflow.persistence.repository import WorkflowRepository
from v3_workflow.policy.review_schedule import ReviewSchedulePolicy


NOW = "2026-08-23T00:00:00Z"


def minute_policy() -> ReviewSchedulePolicy:
    return ReviewSchedulePolicy.from_file(PROJECT / "config" / "review-schedule.test.json")


def formal_policy() -> ReviewSchedulePolicy:
    return ReviewSchedulePolicy.from_file(PROJECT / "config" / "review-schedule.json")


def assessment(sequence: int, accuracy: float = 0.8) -> dict:
    return {
        "contract_name": "dada.review_assessment",
        "contract_version": 1,
        "data": {"question_sequence": sequence, "accuracy": accuracy, "explanation": "模型判断说明", "incorrect_words": [], "feedback_basis": "根据本题作答"},
    }


def test_question_mode_selector(_unit_type: str, previous_modes: tuple[str, ...]) -> str:
    return "zh_to_en" if _unit_type == "phrase" or previous_modes else "en_to_zh"


def phrase_review_context() -> dict:
    return {
        "schema_version": 1,
        "purpose_zh": "报告自己和同伴有相同的学校生活安排",
        "context_kind": "shared_school_routine",
        "information_slots": ["other_person_routine", "child_same_routine"],
        "prompt_constraint_zh": "先给出同伴事实，再要求孩子报告相同情况。",
        "accepted_expressions": [{"kind": "target_form", "text": "I go to school at {time}, too.", "note_zh": "保持目标表达。"}],
        "semantic_alternatives": [],
    }


class ReviewRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "workflow.sqlite3"
        self.repository = WorkflowRepository(self.path)
        self.repository.initialize()
        self._seed_due_item()
        self.gateway = FakeReviewModelGateway([])
        self.policy = minute_policy()
        self.service = ReviewTurnService(self.path, self.gateway, "固定复习状态机提示", self.policy, "固定录入状态机提示", test_question_mode_selector)

    def tearDown(self) -> None:
        self.service.close()
        self.temp.cleanup()

    def _seed_due_item(self, due_at: str = "2026-08-22T23:59:00Z") -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("""INSERT INTO workflows(workflow_id, external_session_id, workflow_type, phase, learning_item_id,
                question_sequence, started_at, closed_at) VALUES ('entry-1', 'child-1', 'entry', 'closed', NULL, 0, ?, ?)""", (NOW, NOW))
            connection.execute("""INSERT INTO learning_materials(material_id, source_workflow_id, status, title, language, unit_type,
                reference_text, needs_parent_review, audit_result_json, created_at, updated_at)
                VALUES ('material-1', 'entry-1', 'active', '题目', 'en', 'sentence', 'I go to school.', 0, '{}', ?, ?)""", (NOW, NOW))
            connection.execute("""INSERT INTO learning_items(learning_item_id, material_id, item_order, reference_text, meaning_zh,
                review_stage, next_review_at, completed_at, revision, created_at, updated_at)
                VALUES ('item-1', 'material-1', 1, 'I go to school.', '我去上学。', 0, ?, NULL, 1, ?, ?)""", (due_at, NOW, NOW))

    def _seed_second_due_item(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("""INSERT INTO learning_materials(material_id, source_workflow_id, status, title, language, unit_type,
                reference_text, needs_parent_review, audit_result_json, created_at, updated_at)
                VALUES ('material-2', 'entry-1', 'active', '第二题', 'en', 'sentence', 'I like apples.', 0, '{}', ?, ?)""", (NOW, NOW))
            connection.execute("""INSERT INTO learning_items(learning_item_id, material_id, item_order, reference_text, meaning_zh,
                review_stage, next_review_at, completed_at, revision, created_at, updated_at)
                VALUES ('item-2', 'material-2', 1, 'I like apples.', '我喜欢苹果。', 0, ?, NULL, 1, ?, ?)""", (NOW, NOW, NOW))

    def handle(self, text: str, at: str = NOW, start: bool = False):
        return self.service.handle(AuthorizedReviewIngress(text, at, "child-1", start))

    def start(self) -> str:
        self.gateway._executions.append(question_result("en_to_zh", {"prompt": "I go to school.", "instruction": "请说出这句话的中文意思。"}))
        delivery = self.handle("开始复习", start=True)
        workflow = self.repository.get_active_workflow("child-1", "review")
        self.assertIsNotNone(workflow)
        progress = self.repository.get_review_queue_progress(workflow["workflow_id"])
        self.assertEqual(delivery.progress_text, f"这轮共 {progress['total']} 题，现在从第 1 题开始。")
        self.assertEqual(delivery.speech_text, "I go to school.\n\n请说出这句话的中文意思。")
        self.assertEqual(delivery.reply_text, "I go to school.\n\n请说出这句话的中文意思。")
        return workflow["workflow_id"]

    def test_start_locks_question_only_after_events_commit(self) -> None:
        workflow_id = self.start()
        workflow = self.repository.get_review_context(workflow_id)
        self.assertEqual((workflow["question_sequence"], workflow["locked_question_mode"], workflow["locked_item_revision"]), (1, "en_to_zh", 1))
        events = self.repository.list_events(workflow_id)
        self.assertEqual([event["sequence_no"] for event in events], list(range(1, len(events) + 1)))
        self.assertIn("question_locked", [event["event_type"] for event in events])
        locked = next(event for event in events if event["event_type"] == "question_locked")
        delivered = next(event for event in events if event["event_type"] == "assistant_response")
        self.assertEqual(locked["payload"]["question_json"], {"prompt": "I go to school.", "instruction": "请说出这句话的中文意思。"})
        self.assertEqual(delivered["payload"]["text"], "这轮共 1 题，现在从第 1 题开始。\n\nI go to school.\n\n请说出这句话的中文意思。")
        self.assertEqual(self.gateway.prepared_turns[0].mode, "start_review")
        self.assertEqual((self.gateway.prepared_turns[0].task_contract_version, self.gateway.prepared_turns[0].selected_question_mode), (5, "en_to_zh"))
        self.assertEqual(self.gateway.prepared_turns[0].history[-1]["event_type"], "child_message")

    def test_program_question_mode_pools_are_fixed_and_balanced_random(self) -> None:
        self.assertEqual(allowed_question_modes("word"), ("spelling", "zh_to_en", "en_to_zh"))
        self.assertEqual(allowed_question_modes("phrase"), ("zh_to_en",))
        self.assertEqual(allowed_question_modes("sentence"), ("sentence_recall", "zh_to_en", "en_to_zh"))
        first = lambda candidates: candidates[0]
        self.assertEqual(choose_question_mode("word", (), first), "spelling")
        self.assertEqual(choose_question_mode("word", ("spelling",), first), "zh_to_en")
        self.assertEqual(choose_question_mode("word", ("spelling", "zh_to_en", "en_to_zh"), first), "spelling")

    def test_phrase_review_context_is_frozen_into_review_turn(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute("""INSERT INTO learning_materials(material_id, source_workflow_id, status, title, language, unit_type,
                reference_text, needs_parent_review, audit_result_json, created_at, updated_at)
                VALUES ('material-phrase', 'entry-1', 'active', '短语', 'en', 'phrase', '... too.', 0, '{}', ?, ?)""", (NOW, NOW))
            connection.execute("""INSERT INTO learning_items(learning_item_id, material_id, item_order, reference_text, meaning_zh,
                review_context_json, review_stage, next_review_at, completed_at, revision, created_at, updated_at)
                VALUES ('a-phrase', 'material-phrase', 1, '... too.', '……也是如此。', ?, 0, '2026-08-22T23:58:00Z', NULL, 1, ?, ?)""", (json.dumps(phrase_review_context(), ensure_ascii=False), NOW, NOW))
        self.service.close()
        self.gateway = FakeReviewModelGateway([question_result("zh_to_en", {"prompt": "你的同学说：I go to school at 7 a.m.\n你也一样，请用英语报告。", "instruction": "请用英语回答。"})])
        self.service = ReviewTurnService(self.path, self.gateway, "固定复习状态机提示", self.policy, "固定录入状态机提示", lambda _unit, _previous: "zh_to_en")
        delivery = self.handle("开始复习", start=True)
        self.assertIn("请用英语回答", delivery.reply_text)
        turn = self.gateway.prepared_turns[0].as_request()
        self.assertEqual(turn["task_contract_version"], 5)
        self.assertEqual(turn["selected_question_mode"], "zh_to_en")
        self.assertEqual(turn["learning_item"]["review_context"], phrase_review_context())

    def test_v4_spelling_locks_hidden_speech_text_for_audio_delivery(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute("""INSERT INTO learning_materials(material_id, source_workflow_id, status, title, language, unit_type,
                reference_text, needs_parent_review, audit_result_json, created_at, updated_at)
                VALUES ('material-word', 'entry-1', 'active', '单词', 'en', 'word', 'school', 0, '{}', ?, ?)""", (NOW, NOW))
            connection.execute("""INSERT INTO learning_items(learning_item_id, material_id, item_order, reference_text, meaning_zh,
                review_stage, next_review_at, completed_at, revision, created_at, updated_at)
                VALUES ('a-word', 'material-word', 1, 'school', '学校', 0, '2026-08-22T23:58:00Z', NULL, 1, ?, ?)""", (NOW, NOW))
        self.service.close()
        gateway = FakeReviewModelGateway([
            question_result("spelling", {"prompt": "请听音后输入完整英文单词。", "speech_text": "school"}),
        ])
        self.service = ReviewTurnService(self.path, gateway, "固定复习状态机提示", self.policy, "固定录入状态机提示", lambda _unit, _previous: "spelling")
        delivery = self.handle("开始复习", start=True)
        self.assertEqual(delivery.question_mode, "spelling")
        self.assertEqual(delivery.question_json, {"prompt": "请听音后输入完整英文单词。", "speech_text": "school"})
        self.assertNotIn("school", delivery.reply_text)

    def test_v4_sentence_recall_rejects_missing_speech_text(self) -> None:
        self.gateway._executions.extend([
            question_result("sentence_recall", {"prompt": "请听完后复述这句话。"}),
            question_result("sentence_recall", {"prompt": "请听完后复述这句话。"}),
        ])
        self.service.close()
        self.service = ReviewTurnService(self.path, self.gateway, "固定复习状态机提示", self.policy, "固定录入状态机提示", lambda _unit, _previous: "sentence_recall")
        delivery = self.handle("开始复习", start=True)
        self.assertEqual(delivery.reply_text, "刚才没有处理成功，请再明确说一次“开始复习”。")

    def test_two_model_question_mode_mismatches_end_with_persisted_retry_reply(self) -> None:
        self.gateway._executions.append(question_result("zh_to_en", {"prompt": "我去上学。", "instruction": "请写英文。"}))
        delivery = self.handle("开始复习", start=True)
        self.assertTrue(delivery.handled)
        self.assertEqual(delivery.reply_text, "刚才没有处理成功，请再明确说一次“开始复习”。")
        workflow = self.repository.get_active_workflow("child-1", "review")
        self.assertIsNotNone(workflow)
        self.assertIsNone(workflow["locked_question_mode"])

    def test_assessment_uses_policy_not_model_stage_and_closes(self) -> None:
        workflow_id = self.start()
        self.gateway._executions.append(assessment_result("答得不错，明天再来。", assessment(1)))
        delivery = self.handle("我去上学。", "2026-08-23T00:01:00Z")
        self.assertEqual(delivery.reply_text, "答得不错，明天再来。")
        self.assertEqual(delivery.progress_text, "这轮 1 题已完成。")
        self.assertEqual(self.repository.get_workflow(workflow_id)["phase"], "closed")
        with sqlite3.connect(self.path) as connection:
            item = connection.execute("SELECT review_stage, next_review_at, completed_at, revision FROM learning_items WHERE learning_item_id = 'item-1'").fetchone()
        self.assertEqual(item, (1, "2026-08-23T00:02:00Z", None, 2))
        schedule = next(event for event in self.repository.list_events(workflow_id) if event["event_type"] == "schedule_applied")
        self.assertEqual((schedule["payload"]["policy_id"], schedule["payload"]["policy_version"]), ("dada.review.test", 1))

    def test_assessment_advances_frozen_due_queue_and_asks_next_item(self) -> None:
        self._seed_second_due_item()
        self.gateway._executions.extend([
            question_result("en_to_zh", {"prompt": "I go to school.", "instruction": "请说中文意思。"}),
            assessment_result("答得很棒！", assessment(1)),
            question_result("zh_to_en", {"prompt": "我喜欢苹果。", "instruction": "请说英文意思。"}),
        ])
        review_id = self.handle("开始复习", start=True)
        self.assertEqual(review_id.reply_text, "I go to school.\n\n请说中文意思。")
        delivery = self.handle("我去上学。", "2026-08-23T00:01:00Z")
        self.assertEqual(delivery.reply_text, "答得很棒！\n\n我喜欢苹果。\n\n请说英文意思。")
        self.assertEqual(delivery.progress_text, "第 2 / 2 题，还剩 1 题。")
        workflow = self.repository.get_active_workflow("child-1", "review")
        self.assertIsNotNone(workflow)
        self.assertEqual((workflow["learning_item_id"], workflow["question_sequence"]), ("item-2", 2))
        with sqlite3.connect(self.path) as connection:
            queue = connection.execute("SELECT learning_item_id, status FROM review_queue_items WHERE workflow_id = ? ORDER BY queue_position", (workflow["workflow_id"],)).fetchall()
            schedule_event_id = connection.execute("SELECT event_id FROM workflow_log_events WHERE workflow_id = ? AND event_type = 'schedule_applied' ORDER BY sequence_no DESC LIMIT 1", (workflow["workflow_id"],)).fetchone()[0]
        self.assertEqual(queue, [("item-1", "completed"), ("item-2", "locked")])
        self.assertEqual(len(self.gateway.prepared_turns), 3)
        self.assertEqual(self.gateway.prepared_turns[-1].mode, "next_question")
        checkpoint_states = [
            item.checkpoint.get("channel_values", {})
            for item in self.service._checkpointer.list({"configurable": {"thread_id": workflow["workflow_id"]}})
        ]
        self.assertTrue(any(
            state.get("node") == "review_next_question" and state.get("last_committed_event_id") == schedule_event_id
            for state in checkpoint_states
        ))

    def test_formal_stage_one_through_four_full_cycle_archives_only_after_last_review(self) -> None:
        """Freeze the full 1h → 3h → 5d → 30d progression without wall-clock waiting."""

        self.service.close()
        self.policy = formal_policy()
        self.service = ReviewTurnService(self.path, self.gateway, "固定复习状态机提示", self.policy, "固定录入状态机提示", test_question_mode_selector)
        checkpoints = [
            ("2026-08-23T00:00:00Z", 1, "2026-08-23T01:00:00Z", "2026-08-23T00:59:59Z"),
            ("2026-08-23T01:00:00Z", 2, "2026-08-23T04:00:00Z", "2026-08-23T03:59:59Z"),
            ("2026-08-23T04:00:00Z", 3, "2026-08-28T04:00:00Z", "2026-08-28T03:59:59Z"),
            ("2026-08-28T04:00:00Z", 4, "2026-09-27T04:00:00Z", "2026-09-27T03:59:59Z"),
            ("2026-09-27T04:00:00Z", 4, None, None),
        ]
        for index, (at, expected_stage, expected_due, early_at) in enumerate(checkpoints, start=1):
            self.gateway._executions.extend([
                question_result("en_to_zh", {"prompt": "I go to school.", "instruction": "请说中文意思。"}),
                assessment_result(f"第 {index} 次答对。", assessment(1, 1.0)),
            ])
            start = self.handle("开始复习", at, start=True)
            self.assertEqual(start.reply_text, "I go to school.\n\n请说中文意思。")
            completed = self.handle("我去上学。", at)
            self.assertEqual(completed.reply_text, f"第 {index} 次答对。")
            with sqlite3.connect(self.path) as connection:
                item = connection.execute("SELECT review_stage, next_review_at, completed_at, revision FROM learning_items WHERE learning_item_id = 'item-1'").fetchone()
                material = connection.execute("SELECT status FROM learning_materials WHERE material_id = 'material-1'").fetchone()
            self.assertEqual((item[0], item[1]), (expected_stage, expected_due))
            self.assertEqual(item[2] is not None, index == 5)
            self.assertEqual(item[3], index + 1)
            self.assertEqual(material[0], "archived" if index == 5 else "active")
            if early_at is not None:
                early = self.handle("开始复习", early_at, start=True)
                self.assertTrue(early.no_due_item)
        with sqlite3.connect(self.path) as connection:
            workflow_count = connection.execute("SELECT COUNT(*) FROM workflows WHERE workflow_type = 'review'").fetchone()[0]
            schedules = connection.execute("SELECT COUNT(*) FROM workflow_log_events WHERE event_type = 'schedule_applied'").fetchone()[0]
        self.assertEqual((workflow_count, schedules), (5, 5))

    def test_formal_partial_and_incorrect_answers_reset_each_stage_to_configured_short_interval(self) -> None:
        """Every stage follows the family rule without waiting on the wall clock.

        <60% returns to the one-hour stage; 60%--94.99% returns to the
        three-hour stage.  Neither path archives or advances the item.
        """

        self.service.close()
        self.policy = formal_policy()
        self.service = ReviewTurnService(self.path, self.gateway, "固定复习状态机提示", self.policy, "固定录入状态机提示", test_question_mode_selector)
        cases = [
            (0, 0.59, 0, "2026-08-23T02:00:00Z"),
            (1, 0.59, 0, "2026-08-23T03:00:00Z"),
            (2, 0.59, 0, "2026-08-23T04:00:00Z"),
            (3, 0.59, 0, "2026-08-23T05:00:00Z"),
            (4, 0.59, 0, "2026-08-23T06:00:00Z"),
            (0, 0.60, 1, "2026-08-23T09:00:00Z"),
            (1, 0.60, 1, "2026-08-23T10:00:00Z"),
            (2, 0.60, 1, "2026-08-23T11:00:00Z"),
            (3, 0.60, 1, "2026-08-23T12:00:00Z"),
            (4, 0.60, 1, "2026-08-23T13:00:00Z"),
        ]
        for index, (stage_before, accuracy, expected_stage, expected_due) in enumerate(cases, start=1):
            at = f"2026-08-23T{index:02d}:00:00Z"
            with sqlite3.connect(self.path) as connection:
                connection.execute("UPDATE learning_materials SET status = 'active' WHERE material_id = 'material-1'")
                connection.execute("""UPDATE learning_items SET review_stage = ?, next_review_at = ?, completed_at = NULL,
                    revision = ?, updated_at = ? WHERE learning_item_id = 'item-1'""", (stage_before, at, index, at))
            self.gateway._executions.extend([
                question_result("en_to_zh", {"prompt": "I go to school.", "instruction": "请说中文意思。"}),
                assessment_result("继续巩固。", assessment(1, accuracy)),
            ])
            self.assertIsNotNone(self.handle("开始复习", at, start=True).reply_text)
            self.assertEqual(self.handle("我的回答", at).reply_text, "继续巩固。")
            with sqlite3.connect(self.path) as connection:
                item = connection.execute("SELECT review_stage, next_review_at, completed_at FROM learning_items WHERE learning_item_id = 'item-1'").fetchone()
                material = connection.execute("SELECT status FROM learning_materials WHERE material_id = 'material-1'").fetchone()
            self.assertEqual(item, (expected_stage, expected_due, None))
            self.assertEqual(material, ("active",))
            self.assertIsNone(self.repository.get_active_workflow("child-1", "review"))

    def test_stop_before_answer_keeps_current_queue_item_uncompleted(self) -> None:
        self._seed_second_due_item()
        workflow_id = self.start()
        self.gateway._executions.append(transition_result("好的，先停在这里。", "stop_review"))
        self.assertEqual(self.handle("复习结束", "2026-08-23T00:01:00Z").reply_text, "好的，先停在这里。")
        with sqlite3.connect(self.path) as connection:
            queue = connection.execute("SELECT learning_item_id, status FROM review_queue_items WHERE workflow_id = ? ORDER BY queue_position", (workflow_id,)).fetchall()
            item = connection.execute("SELECT review_stage, next_review_at FROM learning_items WHERE learning_item_id = 'item-1'").fetchone()
        self.assertEqual(queue, [("item-1", "locked"), ("item-2", "pending")])
        self.assertEqual(item, (0, "2026-08-22T23:59:00Z"))

    def test_unrelated_input_repeats_locked_question_without_assessment_or_rescheduling(self) -> None:
        workflow_id = self.start()
        self.gateway._executions.append(locked_question_guidance_result("现在正在复习。先回答当前这题；其他问题可以复习结束后再问。"))
        delivery = self.handle("我想吃冰淇淋。", "2026-08-23T00:01:00Z")
        self.assertEqual(delivery.reply_text, "现在正在复习。先回答当前这题；其他问题可以复习结束后再问。\n\nI go to school.\n\n请说出这句话的中文意思。")
        with sqlite3.connect(self.path) as connection:
            item = connection.execute("SELECT review_stage, next_review_at, completed_at, revision FROM learning_items WHERE learning_item_id = 'item-1'").fetchone()
            queue = connection.execute("SELECT status, completed_at FROM review_queue_items WHERE workflow_id = ? AND learning_item_id = 'item-1'", (workflow_id,)).fetchone()
        self.assertEqual(item, (0, "2026-08-22T23:59:00Z", None, 1))
        self.assertEqual(queue, ("locked", None))
        events = self.repository.list_events(workflow_id)
        self.assertFalse(any(event["event_type"] == "schedule_applied" for event in events))
        self.assertEqual(events[-1]["event_type"], "assistant_response")
        self.assertEqual(events[-1]["payload"]["text"], f"现在正在复习。先回答当前这题；其他问题可以复习结束后再问。\n\n这轮共 1 题，现在从第 1 题开始。\n\nI go to school.\n\n请说出这句话的中文意思。")

        self.gateway._executions.append(assessment_result("这次答对了。", assessment(1)))
        completed = self.handle("我去上学。", "2026-08-23T00:02:00Z")
        self.assertEqual(completed.reply_text, "这次答对了。")
        self.assertEqual(len(self.gateway.prepared_turns), 3)
        self.assertEqual(self.gateway.prepared_turns[-1].current_child_message["text"], "我去上学。")

    def test_no_due_item_creates_no_workflow_or_gateway_call(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE learning_items SET next_review_at = '2026-08-24T00:00:00Z'")
        delivery = self.handle("开始复习", start=True)
        self.assertEqual((delivery.handled, delivery.reply_text, delivery.no_due_item), (True, None, True))
        self.assertIsNone(self.repository.get_active_workflow("child-1", "review"))
        self.assertEqual(self.gateway.prepared_turns, [])

    def test_due_selection_is_isolated_by_external_session_id_in_one_database(self) -> None:
        """A test token and formal token can share SQLite without sharing due items."""

        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("""INSERT INTO workflows(workflow_id, external_session_id, workflow_type, phase, learning_item_id,
                question_sequence, started_at, closed_at) VALUES ('entry-test', 'child-test', 'entry', 'closed', NULL, 0, ?, ?)""", (NOW, NOW))
            connection.execute("""INSERT INTO learning_materials(material_id, source_workflow_id, status, title, language, unit_type,
                reference_text, needs_parent_review, audit_result_json, created_at, updated_at)
                VALUES ('material-test', 'entry-test', 'active', '测试题目', 'en', 'sentence', 'I like apples.', 0, '{}', ?, ?)""", (NOW, NOW))
            connection.execute("""INSERT INTO learning_items(learning_item_id, material_id, item_order, reference_text, meaning_zh,
                review_stage, next_review_at, completed_at, revision, created_at, updated_at)
                VALUES ('item-test', 'material-test', 1, 'I like apples.', '我喜欢苹果。', 0, ?, NULL, 1, ?, ?)""", ("2026-08-22T23:59:00Z", NOW, NOW))
        self.gateway._executions.extend([
            question_result("en_to_zh", {"prompt": "I go to school.", "instruction": "请说出第一句的中文意思。"}),
            question_result("en_to_zh", {"prompt": "I like apples.", "instruction": "请说出第二句的中文意思。"}),
        ])
        self.handle("开始复习", start=True)
        test_delivery = self.service.handle(AuthorizedReviewIngress("开始复习", NOW, "child-test", True))
        self.assertEqual(test_delivery.reply_text, "I like apples.\n\n请说出第二句的中文意思。")
        self.assertEqual(self.repository.get_active_workflow("child-1", "review")["learning_item_id"], "item-1")
        self.assertEqual(self.repository.get_active_workflow("child-test", "review")["learning_item_id"], "item-test")

    def test_stop_releases_lock_without_rescheduling(self) -> None:
        workflow_id = self.start()
        self.gateway._executions.append(transition_result("这次复习先停在这里，之后想继续时再说“开始复习”。", "stop_review"))
        self.assertEqual(self.handle("先到这里", "2026-08-23T00:01:00Z").reply_text, "这次复习先停在这里，之后想继续时再说“开始复习”。")
        self.assertEqual(self.repository.get_workflow(workflow_id)["phase"], "closed")
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT review_stage, next_review_at, revision FROM learning_items WHERE learning_item_id = 'item-1'").fetchone(), (0, "2026-08-22T23:59:00Z", 1))
        self.assertIn("question_released", [event["event_type"] for event in self.repository.list_events(workflow_id)])

    def test_v3_transition_stops_review_and_delivers_reply(self) -> None:
        workflow_id = self.start()
        self.gateway._executions.append(ReviewGatewayExecution({
            "contract_name": "dada.review_state_machine_result", "contract_version": 3,
            "data": {"next_operation": "request_transition", "requested_transition": "stop_review", "assistant_response": "这次复习先停在这里。"},
        }))
        delivery = self.handle("复习退出", "2026-08-23T00:01:00Z")
        self.assertEqual(delivery.reply_text, "这次复习先停在这里。")
        self.assertEqual(self.repository.get_workflow(workflow_id)["phase"], "closed")

    def test_gateway_failure_retries_once_without_asking_child_to_repeat(self) -> None:
        workflow_id = self.start()
        self.gateway._executions.extend([GatewayError("offline"), assessment_result("答得很好。", assessment(1, 1.0))])
        self.assertEqual(self.handle("我的回答", "2026-08-23T00:01:00Z").reply_text, "答得很好。")
        child_events = [event for event in self.repository.list_events(workflow_id) if event["event_type"] == "child_message"]
        self.assertEqual([event["payload"]["text"] for event in child_events], ["开始复习", "我的回答"])

    def test_malformed_model_output_retries_once_without_asking_child_to_repeat(self) -> None:
        workflow_id = self.start()
        self.gateway._executions.append(ReviewGatewayExecution({
            "contract_name": "dada.review_state_machine_result", "contract_version": 3,
            "data": {"next_operation": "complete_assessment", "assistant_response": "不应送达", "assessment": assessment(1, 1.0), "tool_calls": []},
        }))
        self.gateway._executions.append(assessment_result("重试成功。", assessment(1, 1.0)))
        self.assertEqual(self.handle("我的回答", "2026-08-23T00:01:00Z").reply_text, "重试成功。")
        event_types = [event["event_type"] for event in self.repository.list_events(workflow_id)]
        self.assertGreaterEqual(event_types.count("llm_response"), 3)
        child_events = [event for event in self.repository.list_events(workflow_id) if event["event_type"] == "child_message"]
        self.assertEqual([event["payload"]["text"] for event in child_events], ["开始复习", "我的回答"])

    def test_two_gateway_failures_then_show_retry_reply(self) -> None:
        self.start()
        self.gateway._executions.extend([GatewayError("offline"), GatewayError("offline")])
        self.assertEqual(self.handle("我的回答", "2026-08-23T00:01:00Z").reply_text, "刚才没有处理成功，请再发一次刚才的答案。")

    def test_new_message_retries_unlocked_next_question_after_terminal_gateway_failure(self) -> None:
        self._seed_second_due_item()
        self.gateway._executions.extend([
            question_result("en_to_zh", {"prompt": "I go to school.", "instruction": "请说中文意思。"}),
            assessment_result("答得很好。", assessment(1, 1.0)),
            GatewayError("offline"),
            GatewayError("offline"),
        ])
        self.assertEqual(self.handle("开始复习", start=True).reply_text, "I go to school.\n\n请说中文意思。")
        self.assertEqual(self.handle("我去上学。", "2026-08-23T00:01:00Z").reply_text, "刚才没有处理成功，请再明确说一次“开始复习”。")
        workflow = self.repository.get_active_workflow("child-1", "review")
        self.assertIsNotNone(workflow)
        self.assertIsNone(workflow["locked_question_mode"])

        self.gateway._executions.append(question_result("zh_to_en", {"prompt": "我喜欢苹果。", "instruction": "请说英文意思。"}))
        delivery = self.handle("继续复习", "2026-08-23T00:02:00Z")
        self.assertEqual(delivery.reply_text, "我喜欢苹果。\n\n请说英文意思。")
        self.assertEqual(self.gateway.prepared_turns[-1].mode, "next_question")

    def test_two_blank_question_outputs_end_with_retry_reply_without_lock(self) -> None:
        self.gateway._executions.append(question_result("en_to_zh", {"prompt": "   ", "instruction": "请用中文回答。"}))
        delivery = self.handle("开始复习", start=True)
        self.assertTrue(delivery.handled)
        self.assertEqual(delivery.reply_text, "刚才没有处理成功，请再明确说一次“开始复习”。")
        workflow = self.repository.get_active_workflow("child-1", "review")
        self.assertIsNotNone(workflow)
        self.assertIsNone(workflow["locked_question_mode"])
        self.assertNotIn("question_locked", [event["event_type"] for event in self.repository.list_events(workflow["workflow_id"])])

    def test_question_without_instruction_sends_prompt_only(self) -> None:
        self.gateway._executions.append(question_result("en_to_zh", {"prompt": "I go to school."}))
        delivery = self.handle("开始复习", start=True)
        self.assertEqual(delivery.reply_text, "I go to school.")

    def test_v2_question_result_remains_readable_but_uses_locked_prompt_for_delivery(self) -> None:
        self.gateway._executions.append(ReviewGatewayExecution({
            "contract_name": "dada.review_state_machine_result", "contract_version": 2,
            "data": {"next_operation": "ask_question", "question_mode": "en_to_zh", "question_json": {"prompt": "I go to school."}, "assistant_response": "旧版提示"},
        }))
        delivery = self.handle("开始复习", start=True)
        self.assertEqual(delivery.reply_text, "I go to school.")
        workflow = self.repository.get_active_workflow("child-1", "review")
        event = next(event for event in self.repository.list_events(workflow["workflow_id"]) if event["event_type"] == "assistant_response")
        self.assertEqual(event["payload"]["text"], "这轮共 1 题，现在从第 1 题开始。\n\nI go to school.")

    def test_model_can_ask_a_second_question_with_same_workflow_history(self) -> None:
        workflow_id = self.start()
        self.gateway._executions.append(question_result("zh_to_en", {"prompt": "我去上学。", "instruction": "再试试把中文说成英文。"}))
        self.assertEqual(self.handle("我去上学。", "2026-08-23T00:01:00Z").reply_text, "我去上学。\n\n再试试把中文说成英文。")
        workflow = self.repository.get_review_context(workflow_id)
        self.assertEqual((workflow["question_sequence"], workflow["locked_question_mode"]), (2, "zh_to_en"))
        history = self.gateway.prepared_turns[-1].history
        self.assertTrue(any(event["event_type"] == "question_locked" for event in history))
        self.assertTrue(any(event["event_type"] == "child_message" and event["data"]["text"] == "我去上学。" for event in history))

    def test_revision_conflict_keeps_original_lock_and_never_schedules(self) -> None:
        workflow_id = self.start()
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE learning_items SET revision = 2 WHERE learning_item_id = 'item-1'")
        self.gateway._executions.append(assessment_result("不应发送", assessment(1)))
        self.assertEqual(self.handle("我的回答", "2026-08-23T00:01:00Z").reply_text, "刚才没有处理成功，请再发一次刚才的答案。")
        workflow = self.repository.get_review_context(workflow_id)
        self.assertEqual((workflow["phase"], workflow["locked_item_revision"]), ("active", 1))
        self.assertFalse(any(event["event_type"] == "schedule_applied" for event in self.repository.list_events(workflow_id)))

    def test_final_policy_stage_archives_material(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE learning_items SET review_stage = 4 WHERE learning_item_id = 'item-1'")
        workflow_id = self.start()
        self.gateway._executions.append(assessment_result("已经完成这一项。", assessment(1, 1.0)))
        self.assertEqual(self.handle("我去上学。", "2026-08-23T00:01:00Z").reply_text, "已经完成这一项。")
        with sqlite3.connect(self.path) as connection:
            item = connection.execute("SELECT review_stage, next_review_at, completed_at, revision FROM learning_items WHERE learning_item_id = 'item-1'").fetchone()
            status = connection.execute("SELECT status FROM learning_materials WHERE material_id = 'material-1'").fetchone()
        self.assertEqual(item, (4, None, "2026-08-23T00:01:00Z", 2))
        self.assertEqual(status, ("archived",))
        self.assertIn("material_archived", [event["event_type"] for event in self.repository.list_events(workflow_id)])

    def test_checkpoint_contains_no_message_or_reply_body(self) -> None:
        self.start()
        with sqlite3.connect(self.path) as connection:
            values = []
            for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'checkpoint%'"):
                values.extend(" ".join(str(value) for value in row) for row in connection.execute(f"SELECT * FROM {table}"))
        stored = "\n".join(values)
        self.assertNotIn("固定复习状态机提示", stored)
        self.assertNotIn("请说出这句话的中文意思。", stored)

    def test_switch_to_entry_freezes_question_and_creates_only_entry_active(self) -> None:
        review_id = self.start()
        self.gateway._executions.append(transition_result("已经切换到录入状态，请逐条发送要录入的英文内容。", "start_entry"))
        delivery = self.handle("开始录入", "2026-08-23T00:01:00Z")
        self.assertEqual(delivery.reply_text, "已经切换到录入状态，请逐条发送要录入的英文内容。")
        paused = self.repository.get_workflow(review_id)
        self.assertEqual((paused["phase"], paused["locked_question_mode"], paused["locked_item_revision"]), ("paused_for_entry", "en_to_zh", 1))
        entry = self.repository.get_active_workflow("child-1", "entry")
        self.assertIsNotNone(entry)
        self.assertEqual(self.repository.get_active_workflow("child-1", "review"), None)
        self.assertIn("开始录入", str(self.repository.list_events(entry["workflow_id"])))

    def test_explicit_resume_restores_paused_workflow_and_keeps_lock(self) -> None:
        review_id = self.start()
        self.gateway._executions.append(transition_result("已经切换到录入状态。", "start_entry"))
        self.handle("开始录入", "2026-08-23T00:01:00Z")
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE workflows SET phase = 'closed', closed_at = '2026-08-23T00:02:00Z' WHERE workflow_type = 'entry' AND phase = 'active'")
        self.gateway._executions.append(question_result("zh_to_en", {"prompt": "我去上学。", "instruction": "继续这一题。"}))
        delivery = self.handle("开始复习", "2026-08-23T00:03:00Z", start=True)
        self.assertEqual(delivery.reply_text, "我去上学。\n\n继续这一题。")
        resumed = self.repository.get_active_workflow("child-1", "review")
        self.assertEqual(resumed["workflow_id"], review_id)
        self.assertEqual(resumed["question_sequence"], 2)

    def test_missing_checkpoint_recovers_without_replaying_gateway(self) -> None:
        workflow_id = self.start()
        self.service.close()
        with sqlite3.connect(self.path) as connection:
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'checkpoint%'")]
            for table in tables:
                connection.execute(f"DELETE FROM {table}")
        self.service = ReviewTurnService(self.path, self.gateway, "固定复习状态机提示", self.policy, "固定录入状态机提示", test_question_mode_selector)
        delivery = self.handle("我的回答", "2026-08-23T00:01:00Z")
        self.assertTrue(delivery.handled)
        self.assertEqual(delivery.reply_text, "I go to school.\n\n请说出这句话的中文意思。")
        self.assertEqual(len(self.gateway.prepared_turns), 1)
        self.assertEqual(self.repository.get_review_context(workflow_id)["locked_question_mode"], "en_to_zh")


if __name__ == "__main__":
    unittest.main()
