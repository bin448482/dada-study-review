"""Fixed OpenAI-compatible review Gateway with injected credential-holding transport."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from v3_review.contracts.review_turn import PreparedReviewGatewayRequest, ReviewGatewayExecution, ReviewMachineTurn
from v3_review.gateway.port import GatewayError

from .bundle import StateMachineDefinition


class TransportError(RuntimeError):
    """A redacted transport failure safe to classify in the workflow audit."""

    def __init__(self, message: str, reason_code: str = "provider_failed") -> None:
        super().__init__(message)
        self.reason_code = reason_code


class ResponsesTransport(Protocol):
    def post_json(self, endpoint: str, body: dict[str, Any], timeout_seconds: float) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ProductionGatewayConfig:
    provider: str
    model: str
    endpoint: str
    timeout_seconds: float
    api_style: str = "responses"
    user_agent: str = ""
    def __post_init__(self) -> None:
        if (
            not self.provider or not self.model or not self.endpoint.startswith("https://") or self.timeout_seconds <= 0
            or self.api_style not in {"responses", "chat-completions"} or not self.user_agent
            or len(self.user_agent) > 256 or "\r" in self.user_agent or "\n" in self.user_agent
        ):
            raise ValueError("production Gateway configuration is invalid")


class UrllibBearerResponsesTransport:
    def __init__(self, bearer_token: str, user_agent: str, max_response_bytes: int = 256 * 1024) -> None:
        if not bearer_token or not user_agent or "\r" in user_agent or "\n" in user_agent or max_response_bytes <= 0:
            raise ValueError("transport configuration is invalid")
        self._bearer_token, self._user_agent, self._max_response_bytes = bearer_token, user_agent, max_response_bytes
    def post_json(self, endpoint: str, body: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        request = Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._bearer_token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "User-Agent": self._user_agent,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read(self._max_response_bytes + 1)
                content_type = response.headers.get_content_type()
        except HTTPError as exc:
            content_type = exc.headers.get_content_type() if exc.headers is not None else "unknown"
            raise TransportError(f"provider rejected request (HTTP {exc.code}; content-type {content_type})", "provider_http_error") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise TransportError("provider request failed before a response", "provider_transport_error") from exc
        if len(raw) > self._max_response_bytes: raise TransportError("provider response exceeded the fixed limit", "provider_protocol_error")
        return _decode_provider_response(raw, content_type)


def _decode_provider_response(raw: bytes, content_type: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TransportError("provider response was not UTF-8", "provider_protocol_error") from exc
    if content_type.lower() == "text/event-stream":
        return _decode_sse_response(text)
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TransportError(f"provider response was not JSON (content-type {content_type})", "provider_protocol_error") from exc
    if not isinstance(decoded, dict): raise TransportError("provider response was not an object", "provider_protocol_error")
    return decoded


def _decode_sse_response(text: str) -> dict[str, Any]:
    output_text_parts: list[str] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        data = "\n".join(line[5:].lstrip() for line in block.split("\n") if line.startswith("data:"))
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError as exc:
            raise TransportError("provider SSE event was not JSON", "provider_protocol_error") from exc
        if not isinstance(event, dict):
            raise TransportError("provider SSE event was not an object", "provider_protocol_error")
        response = event.get("response")
        if isinstance(response, dict):
            return response
        if event.get("type") == "response.output_text.delta" and isinstance(event.get("delta"), str):
            output_text_parts.append(event["delta"])
    if output_text_parts:
        return {"output_text": "".join(output_text_parts)}
    raise TransportError("provider SSE response had no structured output", "provider_protocol_error")


class ResponsesModelGateway:
    def __init__(self, config: ProductionGatewayConfig, definition: StateMachineDefinition, transport: ResponsesTransport) -> None:
        self._config, self._definition, self._transport = config, definition, transport
    def prepare(self, turn: ReviewMachineTurn) -> PreparedReviewGatewayRequest:
        if turn.task_contract_name != "dada.review_state_machine_turn" or turn.task_contract_version != 5 or turn.mode not in {"start_review", "answer_question", "next_question"} or turn.graph_node not in {"review_starting", "review_process_answer", "review_next_question"}:
            raise GatewayError("state-machine turn is unsupported")
        current_turn = json.dumps(turn.as_request(), ensure_ascii=False, separators=(",", ":"))
        if self._config.api_style == "responses":
            body = {"model": self._config.model, "input": [{"role": "system", "content": [{"type": "input_text", "text": self._definition.system_prompt}]}, {"role": "user", "content": [{"type": "input_text", "text": current_turn}]}], "text": {"format": {"type": "json_object"}}, "stream": False, "tools": []}
        else:
            body = {"model": self._config.model, "messages": [{"role": "system", "content": self._definition.system_prompt}, {"role": "user", "content": current_turn}], "response_format": {"type": "json_object"}, "temperature": 0}
        return PreparedReviewGatewayRequest(self._config.provider, self._config.model, {"definition_id": self._definition.definition_id, "definition_digest": self._definition.digest, "api_style": self._config.api_style, "body": body})
    def execute(self, prepared_request: PreparedReviewGatewayRequest) -> ReviewGatewayExecution:
        request = prepared_request.request
        if not isinstance(request, dict) or set(request) != {"definition_id", "definition_digest", "api_style", "body"} or request["definition_id"] != self._definition.definition_id or request["definition_digest"] != self._definition.digest or request["api_style"] != self._config.api_style:
            raise GatewayError("prepared request is not bound to the reviewed definition")
        body = request["body"]
        if not isinstance(body, dict) or body.get("model") != self._config.model or (self._config.api_style == "responses" and body.get("tools") != []): raise GatewayError("prepared request is invalid")
        try: response = self._transport.post_json(self._config.endpoint, body, self._config.timeout_seconds)
        except TransportError as exc: raise GatewayError("provider call failed", exc.reason_code) from exc
        output = _output_text(response, self._config.api_style)
        if output is None: raise GatewayError("provider response did not contain structured output", "provider_protocol_error")
        try: decoded = json.loads(output)
        except json.JSONDecodeError as exc: raise GatewayError("provider output was not JSON", "provider_protocol_error") from exc
        if not isinstance(decoded, dict): raise GatewayError("provider output was not an object", "provider_protocol_error")
        return ReviewGatewayExecution(output=decoded, internal_reasoning_unavailable_reason="not exposed by adapter")


def _output_text(response: dict[str, Any], api_style: str) -> str | None:
    if api_style == "chat-completions":
        choices = response.get("choices"); message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
        return message.get("content") if isinstance(message, dict) and isinstance(message.get("content"), str) else None
    if isinstance(response.get("output_text"), str): return response["output_text"]
    texts: list[str] = []
    for item in response.get("output", []) if isinstance(response.get("output"), list) else []:
        for content in item.get("content", []) if isinstance(item, dict) and isinstance(item.get("content"), list) else []:
            if isinstance(content, dict) and content.get("type") == "output_text" and isinstance(content.get("text"), str): texts.append(content["text"])
    return "".join(texts) or None
