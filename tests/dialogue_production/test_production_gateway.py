from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "runtime"))

from v3_dialogue.contracts import AuthorizedDialogueIngress
from v3_dialogue_production.bundle import DefinitionError, definition_digest
from v3_dialogue_production.gateway import ProductionGatewayConfig
from v3_dialogue_production.service import build_dialogue_service


class FakeTransport:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[tuple[str, dict, float]] = []

    def post_json(self, endpoint: str, body: dict, timeout_seconds: float) -> dict:
        self.calls.append((endpoint, body, timeout_seconds))
        return self.response


class DialogueProductionGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.definition = PROJECT / "skill" / "dada-dialogue-state-machine"
        self.unit = PROJECT / "config" / "dialogue-units" / "grade6-english-unit-1-school-life" / "unit.v1.json"
        self.policy = PROJECT / "config" / "dialogue-policy.json"
        self.schedule = PROJECT / "config" / "review-schedule.test.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _service(self, digest: str, transport: FakeTransport):
        return build_dialogue_service(
            Path(self.temp.name) / "archive.sqlite3", self.definition, digest,
            ProductionGatewayConfig("dialogue", "dialogue-model", "https://provider.invalid/dialogue", 5, "responses", "DadaDialogue/1"),
            transport, self.unit, self.policy, self.schedule,
        )

    def test_fixed_definition_request_and_no_credentials_in_audit(self) -> None:
        output = {"contract_name": "dada.dialogue_state_machine_result", "contract_version": 1, "data": {"assistant_response": "Hello!", "level_behavior": "guided_question", "evaluation": {"target_evidence": "exposed", "scenario_achievement": "none", "grammar_observations": [], "content_slots_covered": []}, "repetition_outcome": "not_requested", "capture_candidate_target_ids": [], "difficulty_suggestion": "stay", "requested_transition": None}}
        transport = FakeTransport({"output_text": json.dumps(output)})
        service = self._service(definition_digest(self.definition), transport)
        try:
            delivery = service.handle(AuthorizedDialogueIngress("start", "2026-09-01T00:00:00Z", "child-production", True))
            self.assertTrue(delivery.reply_text.endswith("Hello!"))
            self.assertEqual(delivery.state_text, "【英语对话中】现在开始英语对话。想结束时，请说“对话结束”。")
            self.assertEqual(len(transport.calls), 1)
            endpoint, body, timeout = transport.calls[0]
            self.assertEqual((endpoint, body["model"], timeout, body["tools"]), ("https://provider.invalid/dialogue", "dialogue-model", 5, []))
            self.assertEqual(body["text"], {"format": {"type": "json_object"}})
            events = service.repository.list_events(service.repository.get_active_workflow("child-production", "dialogue")["workflow_id"])
            self.assertNotIn("api_key", json.dumps(events).lower())
            self.assertNotIn("authorization", json.dumps(events).lower())
        finally:
            service.close()

    def test_digest_drift_fails_before_transport(self) -> None:
        transport = FakeTransport({})
        with self.assertRaises(DefinitionError):
            self._service("0" * 64, transport)
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
