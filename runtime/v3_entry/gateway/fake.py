"""Deterministic test-only ModelGateway implementation."""

from __future__ import annotations

from collections import deque
from typing import Any, Iterable

from ..contracts.entry_turn import EntryMachineTurn, GatewayExecution, PreparedGatewayRequest
from .port import GatewayError


class FakeModelGateway:
    """Consumes scripted executions and never accesses a provider or credentials."""

    def __init__(self, executions: Iterable[GatewayExecution | Exception]) -> None:
        self._executions = deque(executions)
        self.prepared_turns: list[EntryMachineTurn] = []

    def prepare(self, turn: EntryMachineTurn) -> PreparedGatewayRequest:
        self.prepared_turns.append(turn)
        return PreparedGatewayRequest(provider="fake", model="fake-entry-state-machine", request={"task": turn.as_request(), "tools": []})

    def execute(self, prepared_request: PreparedGatewayRequest) -> GatewayExecution:
        if not self._executions:
            raise GatewayError("fake gateway has no scripted execution")
        execution = self._executions.popleft()
        if isinstance(execution, Exception):
            raise execution
        if execution.tool_calls or execution.tool_results:
            raise GatewayError("v1 entry state-machine task has no tools")
        return execution


def reply_only(text: str) -> GatewayExecution:
    """Convenient fake output for a valid reply-only state-machine result."""

    return GatewayExecution(
        output={
            "contract_name": "dada.entry_state_machine_result",
            "contract_version": 1,
            "data": {"assistant_response": text, "next_operation": "reply_only"},
        },
        internal_reasoning_unavailable_reason="provider did not return internal reasoning",
    )


def audit_result(text: str, entry_audit: dict[str, Any]) -> GatewayExecution:
    """Convenient fake output for a valid audit-producing result."""

    return GatewayExecution(
        output={
            "contract_name": "dada.entry_state_machine_result",
            "contract_version": 1,
            "data": {"assistant_response": text, "next_operation": "record_entry_audit", "entry_audit": entry_audit},
        },
        internal_reasoning_unavailable_reason="provider did not return internal reasoning",
    )
