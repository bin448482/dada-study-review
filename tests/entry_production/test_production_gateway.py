from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "runtime"))

from v3_entry import AuthorizedEntryIngress
from v3_entry_production import (
    ProductionGatewayConfig,
    TransportError,
    build_entry_service,
    definition_digest,
)
from v3_entry_production.gateway import UrllibBearerResponsesTransport


class FakeResponsesTransport:
    def __init__(self, responses: list[dict | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict, float]] = []

    def post_json(self, endpoint: str, body: dict, timeout_seconds: float) -> dict:
        self.calls.append((endpoint, body, timeout_seconds))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def reply(text: str) -> dict:
    return {
        "output_text": json.dumps(
            {
                "contract_name": "dada.entry_state_machine_result",
                "contract_version": 1,
                "data": {"assistant_response": text, "next_operation": "reply_only"},
            },
            ensure_ascii=False,
        )
    }


def chat_reply(text: str) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "contract_name": "dada.entry_state_machine_result",
                            "contract_version": 1,
                            "data": {"assistant_response": text, "next_operation": "reply_only"},
                        },
                        ensure_ascii=False,
                    )
                }
            }
        ]
    }


class ProductionGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.definition_dir = self.root / "definition"
        shutil.copytree(PROJECT / "skill" / "dada-entry-state-machine", self.definition_dir)
        self.digest = definition_digest(self.definition_dir)
        self.transport = FakeResponsesTransport([reply("开始吧。"), reply("这一句收好了。")])
        self.service = build_entry_service(
            self.root / "workflow.sqlite3",
            self.definition_dir,
            self.digest,
            ProductionGatewayConfig("owlai", "gpt-5.6-luna", "https://provider.invalid/v1/responses", 12, "responses", "Mozilla/5.0"),
            self.transport,
        )

    def tearDown(self) -> None:
        self.service.close()
        self.temp.cleanup()

    def test_real_gateway_shape_is_fixed_and_auditable_without_credentials(self) -> None:
        first = self.service.handle(AuthorizedEntryIngress("开始录入", "2026-08-22T00:00:00Z", "static-session", True))
        second = self.service.handle(AuthorizedEntryIngress("I go to school.", "2026-08-22T00:01:00Z", "static-session"))
        self.assertEqual((first.reply_text, second.reply_text), ("开始吧。", "这一句收好了。"))
        self.assertEqual(len(self.transport.calls), 2)
        endpoint, body, timeout = self.transport.calls[1]
        self.assertEqual((endpoint, body["model"], timeout, body["tools"]), ("https://provider.invalid/v1/responses", "gpt-5.6-luna", 12, []))
        user_message = json.loads(body["input"][1]["content"][0]["text"])
        self.assertEqual(user_message["current_message"]["text"], "I go to school.")
        self.assertNotIn("开始吧。", body["input"][1]["content"][0]["text"])
        workflow = self.service.repository.get_collecting_entry("static-session")
        self.assertIsNotNone(workflow)
        events = self.service.repository.list_events(workflow["entry_workflow_id"])
        request = next(event["payload"] for event in events if event["event_type"] == "llm_request")
        self.assertEqual(request["provider"], "owlai")
        self.assertEqual(request["request"]["definition_digest"], self.digest)
        self.assertNotIn("Authorization", json.dumps(request))

    def test_digest_drift_fails_before_any_provider_call(self) -> None:
        (self.definition_dir / "SKILL.md").write_text("changed", encoding="utf-8")
        with self.assertRaises(ValueError):
            build_entry_service(
                self.root / "other.sqlite3",
                self.definition_dir,
                self.digest,
                ProductionGatewayConfig("owlai", "gpt-5.6-luna", "https://provider.invalid/v1/responses", 12, "responses", "Mozilla/5.0"),
                self.transport,
            )
        self.assertEqual(self.transport.calls, [])

    def test_chat_completions_uses_messages_json_mode_and_parses_output(self) -> None:
        self.service.close()
        transport = FakeResponsesTransport([chat_reply("开始录入。")])
        self.service = build_entry_service(
            self.root / "chat.sqlite3",
            self.definition_dir,
            self.digest,
            ProductionGatewayConfig("volcengine-agent-plan", "deepseek-v4-pro", "https://provider.invalid/chat/completions", 12, "chat-completions", "Mozilla/5.0"),
            transport,
        )
        result = self.service.handle(AuthorizedEntryIngress("开始录入", "2026-08-22T00:00:00Z", "static-session", True))
        self.assertEqual(result.reply_text, "开始录入。")
        _, body, _ = transport.calls[0]
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["messages"][1]["role"], "user")
        self.assertNotIn("tools", body)

    def test_provider_failures_keep_collecting_with_persisted_retry_delivery(self) -> None:
        self.service.close()
        transport = FakeResponsesTransport([TransportError("offline")])
        self.service = build_entry_service(
            self.root / "failure.sqlite3",
            self.definition_dir,
            self.digest,
            ProductionGatewayConfig("owlai", "gpt-5.6-luna", "https://provider.invalid/v1/responses", 12, "responses", "Mozilla/5.0"),
            transport,
        )
        result = self.service.handle(AuthorizedEntryIngress("开始录入", "2026-08-22T00:00:00Z", "static-session", True))
        self.assertTrue(result.handled)
        self.assertEqual(result.reply_text, "刚才这句没有处理成功，请原样再发一次。")
        self.assertIsNotNone(self.service.repository.get_collecting_entry("static-session"))

    def test_direct_transport_uses_the_fixed_user_agent(self) -> None:
        response = MagicMock()
        response.read.return_value = b'{"output_text":"ok"}'
        context = MagicMock()
        context.__enter__.return_value = response
        with patch("v3_entry_production.gateway.urlopen", return_value=context) as opener:
            UrllibBearerResponsesTransport("secret", "Mozilla/5.0").post_json("https://provider.invalid/v1/responses", {"model": "fake"}, 12)
        request = opener.call_args.args[0]
        self.assertEqual(dict((key.lower(), value) for key, value in request.header_items())["user-agent"], "Mozilla/5.0")
