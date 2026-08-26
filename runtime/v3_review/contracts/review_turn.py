"""Pure DTOs for one authorized v3 review turn."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ReviewOperation = Literal["ask_question", "continue_locked_question", "complete_assessment", "request_transition"]
ReviewMode = Literal["start_review", "answer_question", "next_question"]


@dataclass(frozen=True)
class AuthorizedReviewIngress:
    message_text: str
    received_at: str
    external_session_ref: str
    start_requested: bool = False


@dataclass
class ReviewTurnDelivery:
    handled: bool
    reply_text: str | None = None
    no_due_item: bool = False


@dataclass(frozen=True)
class ReviewMachineTurn:
    task_contract_name: str
    task_contract_version: int
    mode: ReviewMode
    graph_node: str
    workflow_id: str
    learning_item: dict[str, Any]
    selected_question_mode: str
    current_child_message: dict[str, str] | None
    locked_question: dict[str, Any] | None
    history: tuple[dict[str, Any], ...]
    history_truncated: bool

    def as_request(self) -> dict[str, Any]:
        return {
            "task_contract_name": self.task_contract_name,
            "task_contract_version": self.task_contract_version,
            "mode": self.mode,
            "active_mode": "review",
            "graph_node": self.graph_node,
            "workflow_id": self.workflow_id,
            "learning_item": dict(self.learning_item),
            "selected_question_mode": self.selected_question_mode,
            "current_child_message": None if self.current_child_message is None else dict(self.current_child_message),
            "locked_question": None if self.locked_question is None else dict(self.locked_question),
            "history": [dict(event) for event in self.history],
            "history_truncated": self.history_truncated,
        }


@dataclass(frozen=True)
class ReviewAssessment:
    question_sequence: int
    accuracy: float
    explanation: str
    incorrect_words: tuple[dict[str, str], ...]
    feedback_basis: str


@dataclass(frozen=True)
class ReviewStateMachineResult:
    next_operation: ReviewOperation
    assistant_response: str | None = None
    question_mode: str | None = None
    question_json: dict[str, Any] | None = None
    assessment: ReviewAssessment | None = None
    requested_transition: Literal["stop_review", "start_entry"] | None = None


@dataclass(frozen=True)
class PreparedReviewGatewayRequest:
    provider: str
    model: str
    request: dict[str, Any]


@dataclass(frozen=True)
class ReviewGatewayExecution:
    output: Any
    internal_reasoning: Any | None = None
    internal_reasoning_unavailable_reason: str | None = None
    tool_calls: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    tool_results: tuple[dict[str, Any], ...] = field(default_factory=tuple)
