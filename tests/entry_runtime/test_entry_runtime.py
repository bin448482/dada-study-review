from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "runtime"))

from v3_entry import AuthorizedEntryIngress, EntryTurnService
from v3_entry.contracts.validation import ContractError, validate_entry_state_machine_result
from v3_entry.contracts.entry_turn import GatewayExecution
from v3_entry.gateway import FakeModelGateway, GatewayError, audit_result, reply_only
from v3_review import AuthorizedReviewIngress, ReviewTurnService
from v3_review.gateway import FakeReviewModelGateway, assessment_result, question_result, transition_result
from v3_workflow.policy.review_schedule import ReviewSchedulePolicy


NOW = "2026-08-22T00:00:00Z"


def sample_audit(needs_parent_review: bool = False) -> dict:
    return {
        "contract_name": "dada.entry_audit",
        "contract_version": 1,
        "data": {
            "materials": [
                {
                    "title": "学校句子",
                    "language": "en",
                    "unit_type": "sentence",
                    "reference_text": "I go to school.",
                    "needs_parent_review": needs_parent_review,
                    "audit_result": {
                        "contract_name": "dada.material_audit_result",
                        "contract_version": 1,
                        "data": {
                            "audit_confidence": "high",
                            "needs_parent_review": needs_parent_review,
                            "audit_changes": [],
                            "semantic_duplicate_review": {
                                "source": "llm_semantic_review",
                                "decision": "needs_parent_review" if needs_parent_review else "no_semantic_duplicate",
                                "reason": "需要家长判断" if needs_parent_review else "含义独立",
                            },
                        },
                    },
                    "items": [{"reference_text": "I go to school.", "meaning_zh": "我去上学。"}],
                }
            ],
            "reentry_requests": [{"guidance": "请补充完整动作。", "unit_type": "sentence"}],
        },
    }


def due_audit() -> dict:
    value = sample_audit()
    value["data"]["reentry_requests"] = []
    return value


def phrase_context() -> dict:
    return {
        "schema_version": 1,
        "purpose_zh": "报告相同的学校生活安排",
        "context_kind": "shared_school_routine",
        "information_slots": ["other_person_routine", "child_same_routine"],
        "prompt_constraint_zh": "先给出同伴事实，再要求孩子报告相同情况。",
        "accepted_expressions": [{"kind": "target_form", "text": "I go to school at {time}, too.", "note_zh": "保持目标表达。"}],
        "semantic_alternatives": [],
    }


def phrase_audit_v3() -> dict:
    value = due_audit()
    value["contract_version"] = 3
    value["data"]["resolved_reentry_request_ids"] = []
    value["data"]["materials"][0].update({"unit_type": "phrase", "reference_text": "... too."})
    value["data"]["materials"][0]["items"] = [{"reference_text": "... too.", "meaning_zh": "……也是如此。", "review_context": phrase_context()}]
    return value


def transition_reply(text: str, transition: str) -> GatewayExecution:
    return GatewayExecution(
        output={
            "contract_name": "dada.entry_state_machine_result",
            "contract_version": 2,
            "data": {"assistant_response": text, "next_operation": "reply_only", "guidance_kind": None, "requested_transition": transition},
        }
    )


def handoff_policy() -> ReviewSchedulePolicy:
    return ReviewSchedulePolicy.from_file(PROJECT / "config" / "review-schedule.test.json")


def review_assessment(sequence: int, accuracy: float = 1.0) -> dict:
    return {
        "contract_name": "dada.review_assessment",
        "contract_version": 1,
        "data": {
            "question_sequence": sequence,
            "accuracy": accuracy,
            "explanation": "模型判断说明",
            "incorrect_words": [],
            "feedback_basis": "根据本题作答",
        },
    }


class EntryRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "workflow-v3.sqlite3"
        self.gateway = FakeModelGateway([])
        self.service = EntryTurnService(self.path, self.gateway, "固定系统提示")

    def tearDown(self) -> None:
        self.service.close()
        self.temp.cleanup()

    def handle(self, text: str, at: str = NOW, session: str = "external-session-1", start_requested: bool = False):
        return self.service.handle(AuthorizedEntryIngress(text, at, session, start_requested))

    def start(self) -> str:
        self.gateway._executions.append(reply_only("现在开始录入。"))
        delivery = self.handle("开始录入", start_requested=True)
        self.assertEqual(delivery.reply_text, "现在开始录入。")
        workflow = self.service.repository.get_collecting_entry("external-session-1")
        self.assertIsNotNone(workflow)
        return workflow["entry_workflow_id"]

    def test_contract_rejects_unknown_result_field(self) -> None:
        with self.assertRaises(ContractError):
            validate_entry_state_machine_result(
                {
                    "contract_name": "dada.entry_state_machine_result",
                    "contract_version": 1,
                    "data": {"assistant_response": "ok", "next_operation": "reply_only", "extra": True},
                }
            )

    def test_v2_audit_accepts_only_null_reply_control_compatibility_fields(self) -> None:
        audit = due_audit()
        audit["contract_version"] = 2
        audit["data"]["resolved_reentry_request_ids"] = []
        result = validate_entry_state_machine_result(
            {
                "contract_name": "dada.entry_state_machine_result", "contract_version": 2,
                "data": {
                    "assistant_response": "已保存。", "next_operation": "record_entry_audit", "entry_audit": audit,
                    "guidance_kind": None, "requested_transition": None,
                },
            }
        )
        self.assertEqual(result.next_operation, "record_entry_audit")
        invalid = {
            "contract_name": "dada.entry_state_machine_result", "contract_version": 2,
            "data": {
                "assistant_response": "已保存。", "next_operation": "record_entry_audit", "entry_audit": audit,
                "guidance_kind": "entry_guidance",
            },
        }
        with self.assertRaises(ContractError):
            validate_entry_state_machine_result(invalid)

    def test_start_records_full_ordered_trace_and_only_returns_after_checkpoint(self) -> None:
        workflow_id = self.start()
        workflow = self.service.repository.get_workflow(workflow_id)
        self.assertEqual(workflow["external_session_id"], "external-session-1")
        self.assertEqual(workflow["phase"], "collecting")
        events = self.service.repository.list_events(workflow_id)
        self.assertEqual([event["sequence_no"] for event in events], list(range(1, len(events) + 1)))
        self.assertEqual(
            [event["event_type"] for event in events],
            ["system_prompt", "state_transition", "child_message", "llm_request", "internal_reasoning_unavailable", "llm_response", "assistant_response"],
        )
        self.assertEqual(self.gateway.prepared_turns[0].mode, "start_entry")
        self.assertEqual(self.gateway.prepared_turns[0].message_text, "开始录入")

    def test_normal_message_only_sends_current_message_to_gateway(self) -> None:
        self.start()
        self.gateway._executions.append(reply_only("我已收到这一句。"))
        delivery = self.handle("I go to school.", "2026-08-22T00:01:00Z")
        self.assertEqual(delivery.reply_text, "我已收到这一句。")
        self.assertEqual(len(self.gateway.prepared_turns), 2)
        turn = self.gateway.prepared_turns[1]
        self.assertEqual(turn.mode, "collect_message")
        self.assertEqual(turn.message_text, "I go to school.")
        self.assertNotIn("现在开始录入", turn.as_request().__repr__())

    def test_audit_commit_creates_independent_active_material_and_reentry(self) -> None:
        workflow_id = self.start()
        self.gateway._executions.append(audit_result("已经建档。", sample_audit()))
        delivery = self.handle("I go to school.", "2026-08-22T00:01:00Z")
        self.assertEqual(delivery.reply_text, "已经建档。\n\n本次已录入 1 条学习内容。")
        materials = self.service.repository.list_materials(workflow_id)
        self.assertEqual(len(materials), 1)
        self.assertEqual(materials[0]["status"], "active")
        with sqlite3.connect(self.path) as connection:
            item = connection.execute("SELECT review_stage, next_review_at, completed_at, revision FROM learning_items").fetchone()
        self.assertEqual(item, (0, "2026-08-22T01:01:00Z", None, 1))
        events = self.service.repository.list_events(workflow_id)
        self.assertIn("reentry_requested", [event["event_type"] for event in events])
        reentry = next(event for event in events if event["event_type"] == "reentry_requested")
        self.assertEqual(reentry["payload"]["guidance"], "请补充完整动作。")

    def test_v3_phrase_audit_persists_review_context(self) -> None:
        workflow_id = self.start()
        output = GatewayExecution(output={
            "contract_name": "dada.entry_state_machine_result", "contract_version": 3,
            "data": {"assistant_response": "短语已保存。", "next_operation": "record_entry_audit", "entry_audit": phrase_audit_v3()},
        })
        self.gateway._executions.append(output)
        self.handle("... too.", "2026-08-22T00:01:00Z")
        with sqlite3.connect(self.path) as connection:
            row = connection.execute("SELECT reference_text, review_context_json FROM learning_items").fetchone()
        self.assertEqual(row[0], "... too.")
        self.assertEqual(json.loads(row[1]), phrase_context())
        self.assertEqual(self.service.repository.get_workflow(workflow_id)["phase"], "collecting")

    def test_recorded_material_reply_reports_program_owned_entry_count(self) -> None:
        workflow_id = self.start()
        first = due_audit()
        second = due_audit()
        second["data"]["materials"][0]["title"] = "另一个材料"
        self.gateway._executions.extend([audit_result("第一条已保存。", first), audit_result("第二条已保存。", second)])
        self.assertEqual(
            self.handle("I go to school.", "2026-08-22T00:01:00Z").reply_text,
            "第一条已保存。\n\n本次已录入 1 条学习内容。",
        )
        self.assertEqual(
            self.handle("I go to school.", "2026-08-22T00:02:00Z").reply_text,
            "第二条已保存。\n\n本次已录入 2 条学习内容。",
        )
        self.assertEqual(len(self.service.repository.list_materials(workflow_id)), 2)

    def test_reentry_only_audit_does_not_claim_a_recorded_count(self) -> None:
        self.start()
        reentry_only = {"contract_name": "dada.entry_audit", "contract_version": 1, "data": {"materials": [], "reentry_requests": [{"guidance": "请补完整句子。", "unit_type": "sentence"}]}}
        self.gateway._executions.append(audit_result("请补充。", reentry_only))
        self.assertEqual(self.handle("I to school.", "2026-08-22T00:01:00Z").reply_text, "请补充。")

    def test_parent_review_material_stays_draft_without_due_item(self) -> None:
        workflow_id = self.start()
        self.gateway._executions.append(audit_result("需要家长确认。", sample_audit(needs_parent_review=True)))
        self.handle("I go to school.", "2026-08-22T00:01:00Z")
        material = self.service.repository.list_materials(workflow_id)[0]
        self.assertEqual(material["status"], "draft")
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT review_stage, next_review_at, completed_at FROM learning_items").fetchone(), (None, None, None))

    def test_two_invalid_model_attempts_end_with_persisted_retry_reply(self) -> None:
        self.start()
        self.gateway._executions.append(
            GatewayExecution(
                output={
                    "contract_name": "dada.entry_state_machine_result",
                    "contract_version": 99,
                    "data": {"assistant_response": "坏输出", "next_operation": "reply_only"},
                },
                internal_reasoning_unavailable_reason="not provided",
            )
        )
        delivery = self.handle("bad", "2026-08-22T00:01:00Z")
        self.assertTrue(delivery.handled)
        self.assertEqual(delivery.reply_text, "刚才这句没有处理成功，请原样再发一次。")
        workflow_id = self.service.repository.get_collecting_entry("external-session-1")["entry_workflow_id"]
        events = self.service.repository.list_events(workflow_id)
        self.assertEqual(events[-1]["event_type"], "assistant_response")
        self.assertEqual([event["event_type"] for event in events].count("model_turn_attempt_failed"), 2)
        self.assertEqual(self.service.repository.list_materials(workflow_id), [])

    def test_two_gateway_failures_keep_collecting_with_persisted_retry_reply(self) -> None:
        workflow_id = self.start()
        self.gateway._executions.append(GatewayError("offline"))
        delivery = self.handle("another line", "2026-08-22T00:01:00Z")
        self.assertTrue(delivery.handled)
        self.assertEqual(delivery.reply_text, "刚才这句没有处理成功，请原样再发一次。")
        self.assertEqual(self.service.repository.get_workflow(workflow_id)["phase"], "collecting")
        self.assertEqual(self.service.repository.list_events(workflow_id)[-1]["event_type"], "assistant_response")
        self.assertEqual(len(self.gateway.prepared_turns), 3)

    def test_historical_unprocessed_entry_event_replays_without_new_child_message(self) -> None:
        workflow_id = self.start()
        old_event = self.service.repository.append_child_message(workflow_id, "旧的已入库输入", "2026-08-22T00:01:00Z")
        self.gateway._executions.append(reply_only("重放成功。"))
        delivery = self.handle("不应另存的新消息", "2026-08-22T00:02:00Z")
        self.assertEqual(delivery.reply_text, "重放成功。")
        self.assertEqual(self.gateway.prepared_turns[-1].event_id, old_event)
        children = [event for event in self.service.repository.list_events(workflow_id) if event["event_type"] == "child_message"]
        self.assertEqual([event["payload"]["text"] for event in children], ["开始录入", "旧的已入库输入"])

    def test_natural_finish_request_closes_only_after_state_machine_output(self) -> None:
        workflow_id = self.start()
        calls_before_close = len(self.gateway.prepared_turns)
        self.gateway._executions.append(transition_reply("这次录入结束。", "finish_entry"))
        delivery = self.handle("录入结束", "2026-08-22T00:01:00Z")
        self.assertTrue(delivery.handled)
        self.assertEqual(delivery.reply_text, "这次录入已经保存好了，做得很认真！")
        self.assertEqual(len(self.gateway.prepared_turns), calls_before_close + 1)
        self.assertEqual(self.service.repository.get_workflow(workflow_id)["phase"], "closed")
        self.assertIsNone(self.service.repository.get_collecting_entry("external-session-1"))

    def test_inactive_message_does_not_create_graph_workflow_or_call_gateway(self) -> None:
        delivery = self.handle("今天怎么样？")
        self.assertEqual((delivery.handled, delivery.reply_text), (False, None))
        self.assertEqual(self.gateway.prepared_turns, [])
        self.assertIsNone(self.service.repository.get_collecting_entry("external-session-1"))

    def test_collecting_workflows_are_isolated_by_external_session(self) -> None:
        self.gateway._executions.extend([reply_only("会话一开始。"), reply_only("会话二开始。")])
        self.handle("开始录入", start_requested=True)
        self.handle("开始录入", session="external-session-2", start_requested=True)
        self.assertIsNotNone(self.service.repository.get_collecting_entry("external-session-1"))
        self.assertIsNotNone(self.service.repository.get_collecting_entry("external-session-2"))

    def test_repeated_start_request_never_becomes_recording_content(self) -> None:
        self.start()
        delivery = self.handle("开始录入", "2026-08-22T00:01:00Z", start_requested=True)
        self.assertEqual((delivery.handled, delivery.reply_text), (False, None))
        self.assertEqual(len(self.gateway.prepared_turns), 1)

    def test_v2_guidance_stays_collecting_and_natural_finish_checks_reentry(self) -> None:
        workflow_id = self.start()
        self.gateway._executions.append(
            GatewayExecution(
                output={
                    "contract_name": "dada.entry_state_machine_result",
                    "contract_version": 2,
                    "data": {"assistant_response": "请一行一条发英文。", "next_operation": "reply_only", "guidance_kind": "entry_guidance", "requested_transition": None},
                }
            )
        )
        self.assertEqual(self.handle("怎么录？", "2026-08-22T00:01:00Z").reply_text, "请一行一条发英文。")
        self.gateway._executions.append(audit_result("请重写。", {"contract_name": "dada.entry_audit", "contract_version": 1, "data": {"materials": [], "reentry_requests": [{"guidance": "请补完整句子。", "unit_type": "sentence"}]}}))
        self.handle("I to school.", "2026-08-22T00:02:00Z")
        self.gateway._executions.append(
            GatewayExecution(
                output={
                    "contract_name": "dada.entry_state_machine_result",
                    "contract_version": 2,
                    "data": {"assistant_response": "我已结束。", "next_operation": "reply_only", "guidance_kind": None, "requested_transition": "finish_entry"},
                }
            )
        )
        delivery = self.handle("我录完了", "2026-08-22T00:03:00Z")
        self.assertEqual(delivery.reply_text, "还有 1 条需要补充。补完后再说“结束录入”吧。")
        self.assertEqual(self.service.repository.get_workflow(workflow_id)["phase"], "collecting")

    def test_unrelated_question_in_entry_stays_collecting_with_state_boundary_guidance(self) -> None:
        workflow_id = self.start()
        self.gateway._executions.append(
            GatewayExecution(
                output={
                    "contract_name": "dada.entry_state_machine_result", "contract_version": 2,
                    "data": {
                        "assistant_response": "现在正在录入。先继续发要学习的英文；今天星期几可以录入结束后再问。",
                        "next_operation": "reply_only", "guidance_kind": "entry_guidance", "requested_transition": None,
                    },
                }
            )
        )
        delivery = self.handle("今天星期几？", "2026-08-22T00:01:00Z")
        self.assertEqual(delivery.reply_text, "现在正在录入。先继续发要学习的英文；今天星期几可以录入结束后再问。")
        self.assertEqual(self.service.repository.get_workflow(workflow_id)["phase"], "collecting")
        self.assertEqual(self.service.repository.list_materials(workflow_id), [])

    def test_v2_audit_resolves_only_the_llm_selected_pending_request(self) -> None:
        workflow_id = self.start()
        self.gateway._executions.append(audit_result("请重写。", {"contract_name": "dada.entry_audit", "contract_version": 1, "data": {"materials": [], "reentry_requests": [{"guidance": "请补完整句子。", "unit_type": "sentence"}]}}))
        self.handle("I to school.", "2026-08-22T00:01:00Z")
        request_id = self.service.repository.get_pending_reentry_requests(workflow_id)[0]["event_id"]
        corrected = sample_audit()
        corrected["contract_version"] = 2
        corrected["data"]["reentry_requests"] = []
        corrected["data"]["resolved_reentry_request_ids"] = [request_id]
        self.gateway._executions.append(
            GatewayExecution(
                output={
                    "contract_name": "dada.entry_state_machine_result",
                    "contract_version": 2,
                    "data": {"assistant_response": "这次补好了。", "next_operation": "record_entry_audit", "entry_audit": corrected},
                }
            )
        )
        self.handle("I go to school.", "2026-08-22T00:02:00Z")
        self.assertEqual(self.service.repository.get_pending_reentry_requests(workflow_id), ())
        self.gateway._executions.append(transition_reply("结束。", "finish_entry"))
        delivery = self.handle("结束录入", "2026-08-22T00:03:00Z")
        self.assertEqual(delivery.reply_text, "这次录入已经保存好了，做得很认真！")
        self.assertEqual(self.service.repository.get_workflow(workflow_id)["phase"], "closed")

    def test_review_request_requires_state_machine_handoff_and_keeps_collecting_on_missing_handoff(self) -> None:
        workflow_id = self.start()
        self.gateway._executions.append(transition_reply("开始复习。", "start_review"))
        delivery = self.handle("开始复习", "2026-08-22T00:01:00Z")
        self.assertEqual(delivery.reply_text, "现在还不能切换到复习，请继续录入。")
        self.assertEqual(self.service.repository.get_workflow(workflow_id)["phase"], "collecting")
        self.assertNotIn("review_active", str(self.service.repository.list_events(workflow_id)))

    def test_pending_reentry_blocks_review_with_exact_count_without_review_model_call(self) -> None:
        self.service.close()
        review_gateway = FakeReviewModelGateway([])
        review_service = ReviewTurnService(
            self.path, review_gateway, "复习提示", handoff_policy(), "固定系统提示",
            lambda _unit_type, _previous: "en_to_zh",
        )
        self.service = EntryTurnService(self.path, self.gateway, "固定系统提示", review_service)
        workflow_id = self.start()
        reentry_only = {
            "contract_name": "dada.entry_audit", "contract_version": 1,
            "data": {"materials": [], "reentry_requests": [
                {"guidance": "请补完整句子。", "unit_type": "sentence"},
                {"guidance": "请补正确短语。", "unit_type": "phrase"},
            ]},
        }
        self.gateway._executions.extend([
            audit_result("请补充。", reentry_only),
            transition_reply("我想复习。", "start_review"),
        ])
        self.handle("I to school.", "2026-08-22T00:01:00Z")
        delivery = self.handle("我要复习了", "2026-08-22T00:02:00Z")
        self.assertEqual(delivery.reply_text, "还有 2 条需要补充。先补完再开始复习吧。")
        self.assertEqual(self.service.repository.get_workflow(workflow_id)["phase"], "collecting")
        self.assertIsNone(review_service.repository.get_active_workflow("external-session-1", "review"))
        self.assertEqual(review_gateway.prepared_turns, [])
        review_service.close()

    def test_ready_entry_switches_to_first_locked_review_question(self) -> None:
        self.service.close()
        review_gateway = FakeReviewModelGateway([question_result("en_to_zh", {"prompt": "I go to school.", "instruction": "请回答中文意思。"})])
        review_service = ReviewTurnService(self.path, review_gateway, "复习提示", handoff_policy(), "固定系统提示", lambda _unit_type, _previous: "en_to_zh")
        self.service = EntryTurnService(self.path, self.gateway, "固定系统提示", review_service)
        self.gateway._executions.extend([reply_only("现在开始录入。"), audit_result("已保存。", due_audit()), transition_reply("开始复习。", "start_review")])
        self.handle("开始录入", start_requested=True)
        self.handle("I go to school.", "2026-08-22T00:01:00Z")
        delivery = self.handle("开始复习", "2026-08-22T01:02:00Z")
        self.assertEqual(delivery.reply_text, "I go to school.\n\n请回答中文意思。")
        self.assertIsNone(self.service.repository.get_collecting_entry("external-session-1"))
        review = review_service.repository.get_active_workflow("external-session-1", "review")
        self.assertIsNotNone(review)
        self.assertEqual((review["question_sequence"], review["locked_question_mode"]), (1, "en_to_zh"))
        self.assertEqual(review_gateway.prepared_turns[0].mode, "start_review")
        review_service.close()

    def test_entry_to_review_freezes_all_due_items_and_continues_after_first_answer(self) -> None:
        self.service.close()
        review_gateway = FakeReviewModelGateway([
            question_result("en_to_zh", {"prompt": "I go to school.", "instruction": "请回答中文意思。"}),
            assessment_result("第一题答得不错。", review_assessment(1)),
            question_result("zh_to_en", {"prompt": "我喜欢苹果。", "instruction": "请说出英文。"}),
        ])
        review_service = ReviewTurnService(
            self.path, review_gateway, "复习提示", handoff_policy(), "固定系统提示", lambda unit_type, previous: "en_to_zh" if not previous else "zh_to_en"
        )
        self.service = EntryTurnService(self.path, self.gateway, "固定系统提示", review_service)
        second = due_audit()
        second["data"]["materials"][0].update({
            "title": "苹果句子",
            "reference_text": "I like apples.",
            "items": [{"reference_text": "I like apples.", "meaning_zh": "我喜欢苹果。"}],
        })
        self.gateway._executions.extend([
            reply_only("现在开始录入。"),
            audit_result("第一条已保存。", due_audit()),
            audit_result("第二条已保存。", second),
            transition_reply("开始复习。", "start_review"),
        ])
        self.start()
        self.handle("I go to school.", "2026-08-22T00:01:00Z")
        self.handle("I like apples.", "2026-08-22T00:02:00Z")

        started = self.handle("开始复习", "2026-08-22T01:02:00Z")
        self.assertEqual(started.reply_text, "I go to school.\n\n请回答中文意思。")
        review = review_service.repository.get_active_workflow("external-session-1", "review")
        self.assertIsNotNone(review)
        with sqlite3.connect(self.path) as connection:
            queue = connection.execute(
                "SELECT queue_position, learning_item_id, status FROM review_queue_items WHERE workflow_id = ? ORDER BY queue_position",
                (review["workflow_id"],),
            ).fetchall()
        self.assertEqual(len(queue), 2)
        self.assertEqual([row[0] for row in queue], [1, 2])
        self.assertEqual([row[2] for row in queue], ["locked", "pending"])

        continued = review_service.handle(AuthorizedReviewIngress("我去上学。", "2026-08-22T01:03:00Z", "external-session-1"))
        self.assertEqual(continued.progress_text, "第 2 / 2 题，还剩 1 题。")
        self.assertEqual(continued.reply_text, "第一题答得不错。\n\n我喜欢苹果。\n\n请说出英文。")
        self.assertEqual(review_gateway.prepared_turns[1].mode, "answer_question")
        self.assertEqual(review_gateway.prepared_turns[2].mode, "next_question")
        with sqlite3.connect(self.path) as connection:
            statuses = connection.execute(
                "SELECT queue_position, status FROM review_queue_items WHERE workflow_id = ? ORDER BY queue_position",
                (review["workflow_id"],),
            ).fetchall()
        self.assertEqual(statuses, [(1, "completed"), (2, "locked")])
        review_service.close()

    def test_entry_to_review_gateway_failure_keeps_collecting(self) -> None:
        self.service.close()
        review_service = ReviewTurnService(self.path, FakeReviewModelGateway([GatewayError("offline")]), "复习提示", handoff_policy(), "固定系统提示", lambda _unit_type, _previous: "en_to_zh")
        self.service = EntryTurnService(self.path, self.gateway, "固定系统提示", review_service)
        self.gateway._executions.extend([reply_only("现在开始录入。"), audit_result("已保存。", due_audit()), transition_reply("开始复习。", "start_review")])
        workflow_id = self.start()
        self.handle("I go to school.", "2026-08-22T00:01:00Z")
        delivery = self.handle("开始复习", "2026-08-22T01:02:00Z")
        self.assertEqual(delivery.reply_text, "现在还不能开始复习，请继续录入或稍后再试。")
        self.assertEqual(self.service.repository.get_workflow(workflow_id)["phase"], "collecting")
        self.assertIsNone(review_service.repository.get_active_workflow("external-session-1", "review"))
        self.assertEqual(len(review_service._gateway.prepared_turns), 2)
        self.assertEqual(
            [event["event_type"] for event in self.service.repository.list_events(workflow_id)].count("model_turn_attempt_failed"), 2
        )
        review_service.close()

    def test_entry_to_review_restores_paused_question_without_new_model_call(self) -> None:
        self.service.close()
        review_gateway = FakeReviewModelGateway([question_result("en_to_zh", {"prompt": "I go to school.", "instruction": "第一题。"})])
        review_service = ReviewTurnService(self.path, review_gateway, "复习提示", handoff_policy(), "固定系统提示", lambda _unit_type, _previous: "en_to_zh")
        self.service = EntryTurnService(self.path, self.gateway, "固定系统提示", review_service)
        self.gateway._executions.extend([reply_only("现在开始录入。"), audit_result("已保存。", due_audit()), transition_reply("结束录入。", "finish_entry"), transition_reply("开始复习。", "start_review")])
        self.start()
        self.handle("I go to school.", "2026-08-22T00:01:00Z")
        self.handle("结束录入", "2026-08-22T00:02:00Z")
        self.assertEqual(review_service.handle(AuthorizedReviewIngress("开始复习", "2026-08-22T01:02:00Z", "external-session-1", True)).reply_text, "I go to school.\n\n第一题。")
        review_id = review_service.repository.get_active_workflow("external-session-1", "review")["workflow_id"]
        review_gateway._executions.append(transition_result("开始录入。", "start_entry"))
        review_service.handle(AuthorizedReviewIngress("开始录入", "2026-08-22T01:03:00Z", "external-session-1"))
        self.assertIsNotNone(self.service.repository.get_collecting_entry("external-session-1"))
        delivery = self.handle("开始复习", "2026-08-22T01:04:00Z")
        self.assertEqual(delivery.reply_text, "已恢复刚才的复习题。")
        restored = review_service.repository.get_active_workflow("external-session-1", "review")
        self.assertEqual((restored["workflow_id"], restored["question_sequence"], restored["locked_question_mode"]), (review_id, 1, "en_to_zh"))
        self.assertEqual(len(review_gateway.prepared_turns), 2)
        review_service.close()

    def test_missing_checkpoint_replays_unprocessed_child_event_once(self) -> None:
        self.service.repository.start_entry("manual-workflow", "external-session-1", "prompt", "开始录入", NOW)
        delivery = self.handle("next message", "2026-08-22T00:01:00Z")
        self.assertTrue(delivery.handled)
        self.assertEqual(delivery.reply_text, "刚才这句没有处理成功，请原样再发一次。")
        self.assertEqual(len(self.gateway.prepared_turns), 2)
        events = self.service.repository.list_events("manual-workflow")
        self.assertEqual(events[-1]["event_type"], "assistant_response")

    def test_process_restart_uses_existing_checkpoint_and_current_message_only(self) -> None:
        self.start()
        self.service.close()
        restarted_gateway = FakeModelGateway([reply_only("重启后继续。")])
        self.gateway = restarted_gateway
        self.service = EntryTurnService(self.path, restarted_gateway, "固定系统提示")
        delivery = self.handle("重启后的新消息", "2026-08-22T00:01:00Z")
        self.assertEqual(delivery.reply_text, "重启后继续。")
        self.assertEqual(restarted_gateway.prepared_turns[0].message_text, "重启后的新消息")

    def test_checkpoint_contains_no_message_or_reply_body(self) -> None:
        self.start()
        self.gateway._executions.append(reply_only("私密回复"))
        self.handle("私密孩子消息", "2026-08-22T00:01:00Z")
        with sqlite3.connect(self.path) as connection:
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'checkpoint%'")]
            values = []
            for table in tables:
                for row in connection.execute(f"SELECT * FROM {table}"):
                    values.append(" ".join(str(value) for value in row))
        stored = "\n".join(values)
        self.assertNotIn("私密孩子消息", stored)
        self.assertNotIn("私密回复", stored)
        self.assertNotIn("固定系统提示", stored)
        self.assertNotIn("external-session-1", stored)

    def test_final_checkpoint_failure_suppresses_already_committed_reply(self) -> None:
        workflow_id = self.start()
        self.gateway._executions.append(reply_only("不应发送"))
        original_put = self.service._checkpointer.put
        calls = 0

        def fail_final_put(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 4:
                raise RuntimeError("checkpoint write failed")
            return original_put(*args, **kwargs)

        self.service._checkpointer.put = fail_final_put
        delivery = self.handle("persist then fail", "2026-08-22T00:01:00Z")
        self.assertTrue(delivery.handled)
        self.assertIsNone(delivery.reply_text)
        events = self.service.repository.list_events(workflow_id)
        self.assertEqual(events[-1]["payload"]["text"], "不应发送")
        self.assertEqual(self.gateway.prepared_turns[-1].message_text, "persist then fail")
        self.service._checkpointer.put = original_put
        recovered = self.handle("ignored while recovering", "2026-08-22T00:02:00Z")
        self.assertEqual(recovered.reply_text, "不应发送")
        self.assertEqual(self.gateway.prepared_turns[-1].message_text, "persist then fail")


if __name__ == "__main__":
    unittest.main()
