from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "runtime"))

from v3_review import AuthorizedReviewIngress
from v3_review_production import ProductionGatewayConfig, TransportError, build_review_service, definition_digest
from v3_workflow.persistence.repository import WorkflowRepository
from v3_workflow.policy.review_schedule import ReviewSchedulePolicy


class FakeTransport:
    def __init__(self, responses: list[dict | Exception]) -> None:
        self.responses, self.calls = list(responses), []
    def post_json(self, endpoint: str, body: dict, timeout_seconds: float) -> dict:
        self.calls.append((endpoint, body, timeout_seconds))
        result = self.responses.pop(0)
        if isinstance(result, Exception): raise result
        return result


def question(text: str) -> dict:
    return {"output_text": json.dumps({"contract_name": "dada.review_state_machine_result", "contract_version": 3, "data": {"next_operation": "ask_question", "question_mode": "en_to_zh", "question_json": {"prompt": "I go to school.", "instruction": text}}}, ensure_ascii=False)}


def policy() -> ReviewSchedulePolicy:
    return ReviewSchedulePolicy.from_file(PROJECT / "config" / "review-schedule.test.json")


class ReviewProductionGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.definition_dir = self.root / "definition"; shutil.copytree(PROJECT / "skill" / "dada-review-state-machine", self.definition_dir)
        self.database = self.root / "workflow.sqlite3"; repository = WorkflowRepository(self.database); repository.initialize()
        with sqlite3.connect(self.database) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("INSERT INTO workflows(workflow_id, external_session_id, workflow_type, phase, learning_item_id, question_sequence, started_at, closed_at) VALUES ('entry', 'child', 'entry', 'closed', NULL, 0, '2026-08-23T00:00:00Z', '2026-08-23T00:00:00Z')")
            connection.execute("INSERT INTO learning_materials(material_id, source_workflow_id, status, title, language, unit_type, reference_text, needs_parent_review, audit_result_json, created_at, updated_at) VALUES ('material', 'entry', 'active', 'title', 'en', 'sentence', 'I go to school.', 0, '{}', '2026-08-23T00:00:00Z', '2026-08-23T00:00:00Z')")
            connection.execute("INSERT INTO learning_items(learning_item_id, material_id, item_order, reference_text, meaning_zh, review_stage, next_review_at, completed_at, revision, created_at, updated_at) VALUES ('item', 'material', 1, 'I go to school.', '我去上学。', 0, '2026-08-22T23:59:00Z', NULL, 1, '2026-08-23T00:00:00Z', '2026-08-23T00:00:00Z')")
        self.digest = definition_digest(self.definition_dir); self.transport = FakeTransport([question("请说中文意思。")])
        self.service = build_review_service(self.database, self.definition_dir, self.digest, ProductionGatewayConfig("fake-provider", "fake-model", "https://provider.invalid/v1/responses", 12), self.transport, policy(), question_mode_selector=lambda _unit_type, _previous: "en_to_zh")

    def tearDown(self) -> None:
        self.service.close(); self.temp.cleanup()

    def test_fixed_review_definition_request_and_no_credentials_in_audit(self) -> None:
        delivery = self.service.handle(AuthorizedReviewIngress("开始复习", "2026-08-23T00:00:00Z", "child", True))
        self.assertEqual(delivery.reply_text, "这轮有 1 题需要复习，现在还剩 1 题（含当前题）。\n\nI go to school.\n\n请说中文意思。")
        endpoint, body, timeout = self.transport.calls[0]
        self.assertEqual((endpoint, body["model"], timeout, body["tools"]), ("https://provider.invalid/v1/responses", "fake-model", 12, []))
        request = json.loads(body["input"][1]["content"][0]["text"])
        self.assertEqual((request["task_contract_name"], request["task_contract_version"], request["active_mode"], request["selected_question_mode"]), ("dada.review_state_machine_turn", 3, "review", "en_to_zh"))
        self.assertEqual(body["text"]["format"], {"type": "json_object"})
        workflow = self.service.repository.get_active_workflow("child", "review")
        event = next(event for event in self.service.repository.list_events(workflow["workflow_id"]) if event["event_type"] == "llm_request")
        self.assertEqual(event["payload"]["request"]["definition_digest"], self.digest)
        self.assertNotIn("Authorization", json.dumps(event))

    def test_digest_drift_fails_before_transport(self) -> None:
        (self.definition_dir / "SKILL.md").write_text("changed", encoding="utf-8")
        with self.assertRaises(ValueError):
            build_review_service(self.root / "other.sqlite3", self.definition_dir, self.digest, ProductionGatewayConfig("fake", "model", "https://provider.invalid/v1/responses", 12), self.transport, policy(), question_mode_selector=lambda _unit_type, _previous: "en_to_zh")
        self.assertEqual(self.transport.calls, [])

    def test_transport_failures_leave_review_active_with_persisted_retry_delivery(self) -> None:
        self.service.close(); transport = FakeTransport([TransportError("offline"), TransportError("offline")])
        self.service = build_review_service(self.database, self.definition_dir, self.digest, ProductionGatewayConfig("fake", "model", "https://provider.invalid/v1/responses", 12), transport, policy(), question_mode_selector=lambda _unit_type, _previous: "en_to_zh")
        delivery = self.service.handle(AuthorizedReviewIngress("开始复习", "2026-08-23T00:00:00Z", "child", True))
        self.assertTrue(delivery.handled); self.assertEqual(delivery.reply_text, "刚才没有处理成功，请再明确说一次“开始复习”。"); self.assertEqual(len(transport.calls), 2)
        self.assertIsNotNone(self.service.repository.get_active_workflow("child", "review"))
