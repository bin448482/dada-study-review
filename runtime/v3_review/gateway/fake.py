"""Deterministic no-credential ReviewModelGateway fake for offline tests."""

from __future__ import annotations

from collections import deque
from typing import Any, Iterable

from ..contracts.review_turn import PreparedReviewGatewayRequest, ReviewGatewayExecution, ReviewMachineTurn
from .port import GatewayError


class FakeReviewModelGateway:
    def __init__(self, executions: Iterable[ReviewGatewayExecution | Exception]) -> None:
        self._executions = deque(executions)
        self.prepared_turns: list[ReviewMachineTurn] = []

    def prepare(self, turn: ReviewMachineTurn) -> PreparedReviewGatewayRequest:
        self.prepared_turns.append(turn)
        return PreparedReviewGatewayRequest("fake", "fake-review-state-machine", {"task": turn.as_request(), "tools": []})

    def execute(self, prepared_request: PreparedReviewGatewayRequest) -> ReviewGatewayExecution:
        if not self._executions:
            raise GatewayError("fake gateway has no scripted execution")
        value = self._executions.popleft()
        if isinstance(value, Exception):
            raise value
        if value.tool_calls or value.tool_results:
            raise GatewayError("review state-machine task has no tools")
        return value


def question_result(question_mode: str, question_json: dict[str, Any]) -> ReviewGatewayExecution:
    return ReviewGatewayExecution({"contract_name": "dada.review_state_machine_result", "contract_version": 3, "data": {"next_operation": "ask_question", "question_mode": question_mode, "question_json": question_json}})


def locked_question_guidance_result(text: str) -> ReviewGatewayExecution:
    return ReviewGatewayExecution({"contract_name": "dada.review_state_machine_result", "contract_version": 3, "data": {"next_operation": "continue_locked_question", "assistant_response": text}})


def assessment_result(text: str, assessment: dict[str, Any]) -> ReviewGatewayExecution:
    return ReviewGatewayExecution({"contract_name": "dada.review_state_machine_result", "contract_version": 1, "data": {"next_operation": "complete_assessment", "assessment": assessment, "assistant_response": text}})


def transition_result(text: str, transition: str) -> ReviewGatewayExecution:
    return ReviewGatewayExecution({"contract_name": "dada.review_state_machine_result", "contract_version": 3, "data": {"next_operation": "request_transition", "requested_transition": transition, "assistant_response": text}})
