"""Fixed OpenAI-Responses-shaped Gateway with an injected credential-holding transport."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from v3_entry.contracts.entry_turn import EntryMachineTurn, GatewayExecution, PreparedGatewayRequest
from v3_entry.gateway.port import GatewayError

from .bundle import StateMachineDefinition


class TransportError(RuntimeError):
    """The injected provider transport could not complete one request."""


class ResponsesTransport(Protocol):
    """Credential-holding deployment port; it never receives a repository or Graph state."""

    def post_json(self, endpoint: str, body: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        """Send one prepared JSON request and return a decoded JSON object."""


@dataclass(frozen=True)
class ProductionGatewayConfig:
    provider: str
    model: str
    endpoint: str
    timeout_seconds: float
    api_style: str = "responses"

    def __post_init__(self) -> None:
        if (
            not self.provider
            or not self.model
            or not self.endpoint.startswith("https://")
            or self.timeout_seconds <= 0
            or self.api_style not in {"responses", "chat-completions"}
        ):
            raise ValueError("production Gateway configuration is invalid")


class UrllibBearerResponsesTransport:
    """A deployment-only transport; its bearer token is never represented in a Gateway request."""

    def __init__(self, bearer_token: str, max_response_bytes: int = 256 * 1024) -> None:
        if not bearer_token or max_response_bytes <= 0:
            raise ValueError("transport configuration is invalid")
        self._bearer_token = bearer_token
        self._max_response_bytes = max_response_bytes

    def post_json(self, endpoint: str, body: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        request = Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={"Authorization": f"Bearer {self._bearer_token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read(self._max_response_bytes + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise TransportError("provider request failed") from exc
        if len(raw) > self._max_response_bytes:
            raise TransportError("provider response exceeded the fixed limit")
        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TransportError("provider response was not JSON") from exc
        if not isinstance(decoded, dict):
            raise TransportError("provider response was not an object")
        return decoded


class ResponsesModelGateway:
    """Runs one fixed task against an injected OpenAI-Responses-compatible transport."""

    def __init__(self, config: ProductionGatewayConfig, definition: StateMachineDefinition, transport: ResponsesTransport) -> None:
        self._config = config
        self._definition = definition
        self._transport = transport

    def prepare(self, turn: EntryMachineTurn) -> PreparedGatewayRequest:
        if (
            turn.task_contract_name != "dada.entry_state_machine_turn"
            or turn.task_contract_version != 1
            or turn.mode not in ("start_entry", "collect_message")
            or turn.active_mode != "entry"
            or turn.graph_node != "entry_process_turn"
            or not turn.message_text
        ):
            raise GatewayError("state-machine turn is unsupported")
        current_turn = json.dumps(turn.as_request(), ensure_ascii=False, separators=(",", ":"))
        if self._config.api_style == "responses":
            body = {
                "model": self._config.model,
                "input": [
                    {"role": "system", "content": [{"type": "input_text", "text": self._definition.system_prompt}]},
                    {"role": "user", "content": [{"type": "input_text", "text": current_turn}]},
                ],
                "text": {"format": {"type": "json_schema", "name": "dada_entry_state_machine_result", "strict": True, "schema": self._definition.output_schema}},
                "tools": [],
            }
        else:
            body = {
                "model": self._config.model,
                "messages": [
                    {"role": "system", "content": self._definition.system_prompt},
                    {"role": "user", "content": current_turn},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            }
        return PreparedGatewayRequest(
            provider=self._config.provider,
            model=self._config.model,
            request={"definition_id": self._definition.definition_id, "definition_digest": self._definition.digest, "api_style": self._config.api_style, "body": body},
        )

    def execute(self, prepared_request: PreparedGatewayRequest) -> GatewayExecution:
        request = prepared_request.request
        if (
            set(request) != {"definition_id", "definition_digest", "api_style", "body"}
            or request["definition_id"] != self._definition.definition_id
            or request["definition_digest"] != self._definition.digest
            or request["api_style"] != self._config.api_style
        ):
            raise GatewayError("prepared request is not bound to the reviewed definition")
        body = request["body"]
        if not isinstance(body, dict) or body.get("model") != self._config.model:
            raise GatewayError("prepared request is invalid")
        if self._config.api_style == "responses" and body.get("tools") != []:
            raise GatewayError("prepared request is invalid")
        if self._config.api_style == "chat-completions" and (not isinstance(body.get("messages"), list) or body.get("response_format") != {"type": "json_object"}):
            raise GatewayError("prepared request is invalid")
        try:
            response = self._transport.post_json(self._config.endpoint, body, self._config.timeout_seconds)
        except TransportError as exc:
            raise GatewayError("provider call failed") from exc
        tool_calls = _tool_calls(response, self._config.api_style)
        output_text = _output_text(response, self._config.api_style)
        if output_text is None:
            raise GatewayError("provider response did not contain structured output")
        try:
            output = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise GatewayError("provider output was not JSON") from exc
        if not isinstance(output, dict):
            raise GatewayError("provider output was not an object")
        return GatewayExecution(output=output, internal_reasoning_unavailable_reason="not exposed by adapter", tool_calls=tuple(tool_calls))


def _output_text(response: dict[str, Any], api_style: str) -> str | None:
    if api_style == "chat-completions":
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return None
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        return content if isinstance(content, str) else None
    direct = response.get("output_text")
    if isinstance(direct, str):
        return direct
    output = response.get("output")
    if not isinstance(output, list):
        return None
    texts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or not isinstance(item.get("content"), list):
            continue
        for content in item["content"]:
            if isinstance(content, dict) and content.get("type") == "output_text" and isinstance(content.get("text"), str):
                texts.append(content["text"])
    return "".join(texts) if texts else None


def _tool_calls(response: dict[str, Any], api_style: str) -> list[dict[str, Any]]:
    if api_style == "chat-completions":
        choices = response.get("choices")
        message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
        raw_calls = message.get("tool_calls") if isinstance(message, dict) else None
        calls: list[dict[str, Any]] = []
        for raw_call in raw_calls if isinstance(raw_calls, list) else []:
            function = raw_call.get("function") if isinstance(raw_call, dict) else None
            name = function.get("name") if isinstance(function, dict) else None
            arguments = function.get("arguments") if isinstance(function, dict) else None
            if isinstance(name, str) and isinstance(arguments, str):
                try:
                    parsed = json.loads(arguments)
                except json.JSONDecodeError:
                    parsed = {"raw": "unparseable"}
                calls.append({"tool_name": name, "arguments": parsed if isinstance(parsed, dict) else {"raw": "non_object"}})
        return calls
    output = response.get("output")
    if not isinstance(output, list):
        return []
    calls: list[dict[str, Any]] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") not in {"function_call", "tool_call"}:
            continue
        name = item.get("name")
        arguments = item.get("arguments")
        if isinstance(name, str) and isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                parsed = {"raw": "unparseable"}
            calls.append({"tool_name": name, "arguments": parsed if isinstance(parsed, dict) else {"raw": "non_object"}})
    return calls
