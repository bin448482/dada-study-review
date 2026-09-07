"""Fixed provider gateway for the verified Dialogue definition."""

from __future__ import annotations

import json
from typing import Any

from v3_dialogue.contracts.dialogue_turn import DialogueGatewayExecution, DialogueMachineTurn, PreparedDialogueGatewayRequest
from v3_dialogue.gateway.port import DialogueGatewayError
from v3_review_production.gateway import ProductionGatewayConfig, ResponsesTransport, TransportError, UrllibBearerResponsesTransport, _output_text

from .bundle import StateMachineDefinition


class ResponsesModelGateway:
    def __init__(self, config: ProductionGatewayConfig, definition: StateMachineDefinition, transport: ResponsesTransport) -> None:
        self._config, self._definition, self._transport = config, definition, transport

    def prepare(self, turn: DialogueMachineTurn) -> PreparedDialogueGatewayRequest:
        if turn.task_contract_name != "dada.dialogue_state_machine_turn" or turn.task_contract_version != 1 or turn.mode not in {"start_dialogue", "continue_dialogue"} or turn.graph_node != "dialogue_continuing":
            raise DialogueGatewayError("state-machine turn is unsupported")
        current = json.dumps(turn.as_request(), ensure_ascii=False, separators=(",", ":"))
        if self._config.api_style == "responses":
            body = {"model": self._config.model, "input": [{"role": "system", "content": [{"type": "input_text", "text": self._definition.system_prompt}]}, {"role": "user", "content": [{"type": "input_text", "text": current}]}], "text": {"format": {"type": "json_object"}}, "stream": False, "tools": []}
        else:
            body = {"model": self._config.model, "messages": [{"role": "system", "content": self._definition.system_prompt}, {"role": "user", "content": current}], "response_format": {"type": "json_object"}, "temperature": 0}
        return PreparedDialogueGatewayRequest(self._config.provider, self._config.model, {"definition_id": self._definition.definition_id, "definition_digest": self._definition.digest, "api_style": self._config.api_style, "body": body})

    def execute(self, prepared_request: PreparedDialogueGatewayRequest) -> DialogueGatewayExecution:
        request = prepared_request.request
        if not isinstance(request, dict) or set(request) != {"definition_id", "definition_digest", "api_style", "body"} or request["definition_id"] != self._definition.definition_id or request["definition_digest"] != self._definition.digest or request["api_style"] != self._config.api_style:
            raise DialogueGatewayError("prepared request is not bound to the reviewed definition")
        body = request["body"]
        if not isinstance(body, dict) or body.get("model") != self._config.model or (self._config.api_style == "responses" and body.get("tools") != []):
            raise DialogueGatewayError("prepared request is invalid")
        try:
            response = self._transport.post_json(self._config.endpoint, body, self._config.timeout_seconds)
        except TransportError as exc:
            raise DialogueGatewayError("provider call failed", exc.reason_code) from exc
        text = _output_text(response, self._config.api_style)
        if text is None:
            raise DialogueGatewayError("provider response did not contain structured output", "provider_protocol_error")
        try:
            output = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DialogueGatewayError("provider output was not JSON", "provider_protocol_error") from exc
        if not isinstance(output, dict):
            raise DialogueGatewayError("provider output was not an object", "provider_protocol_error")
        return DialogueGatewayExecution(output=output, internal_reasoning_unavailable_reason="not exposed by adapter")


__all__ = ["ProductionGatewayConfig", "ResponsesModelGateway", "ResponsesTransport", "UrllibBearerResponsesTransport"]
