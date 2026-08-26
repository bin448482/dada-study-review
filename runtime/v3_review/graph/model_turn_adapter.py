"""Review-specific boundary between Graph business commits and model pipeline."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from v3_workflow.model_turn.contracts import FailureKind, ModelTurnCommit
from v3_workflow.model_turn.pipeline import ModelTurnContractError
from v3_workflow.persistence.repository import RepositoryError, WorkflowRepository

from ..contracts.validation import ReviewContractError, assessment_data, render_locked_question, validate_review_state_machine_result
from ..gateway.port import ReviewModelGateway


class ReviewModelTurnAdapter:
    """Owns review-only validation, immutable-question delivery and commits."""

    def __init__(self, repository: WorkflowRepository, gateway: ReviewModelGateway, workflow_id: str, mode: str, created_at: str, context: Any) -> None:
        self._repository = repository
        self._gateway = gateway
        self._workflow_id = workflow_id
        self._mode = mode
        self._created_at = created_at
        self._context = context

    def prepare(self, turn: Any) -> Any:
        return self._gateway.prepare(turn)

    def execute(self, prepared: Any) -> Any:
        return self._gateway.execute(prepared)

    def append_log_event(self, event_type: str, payload: dict[str, Any], created_at: str) -> str:
        return self._repository.append_log_event(
            self._workflow_id, event_type, payload, created_at, require_active_type="review"
        )

    def validate(self, output: Any, turn: Any) -> Any:
        try:
            return validate_review_state_machine_result(output, turn.selected_question_mode)
        except ReviewContractError as exc:
            raise ModelTurnContractError(str(exc)) from exc

    def commit(self, result: Any, source_llm_response_event_id: str) -> ModelTurnCommit:
        if result.next_operation == "ask_question":
            reply_text = render_locked_question(result.question_json or {})
            if self._context.pending_feedback_text is not None:
                reply_text = f"{self._context.pending_feedback_text}\n\n{reply_text}"
                self._context.pending_feedback_text = None
            if self._mode in {"start_review", "next_question"}:
                progress = self._repository.get_review_queue_progress(self._workflow_id)
                reply_text = f"这轮有 {progress['total']} 题需要复习，现在还剩 {progress['remaining']} 题（含当前题）。\n\n{reply_text}"
            _, event_id = self._repository.lock_review_question(
                self._workflow_id, result.question_mode or "", result.question_json or {}, reply_text,
                source_llm_response_event_id, self._created_at,
            )
            return ModelTurnCommit(event_id, reply_text)
        if result.next_operation == "continue_locked_question":
            event_id, reply_text = self._repository.continue_locked_question(
                self._workflow_id, result.assistant_response or "", source_llm_response_event_id, self._created_at
            )
            return ModelTurnCommit(event_id, reply_text)
        if result.next_operation == "complete_assessment":
            outcome = self._repository.apply_review_assessment(
                self._workflow_id, assessment_data(result.assessment), self._context.policy,
                result.assistant_response or "", source_llm_response_event_id, self._created_at,
            )
            if outcome.has_next_item:
                self._context.pending_feedback_text = result.assistant_response
                return ModelTurnCommit(outcome.last_event_id, "", {"next_question": True})
            return ModelTurnCommit(outcome.last_event_id, result.assistant_response or "")
        if result.requested_transition == "stop_review":
            event_id = self._repository.stop_review(self._workflow_id, result.assistant_response or "", self._created_at)
            return ModelTurnCommit(event_id, result.assistant_response or "")
        if result.requested_transition == "start_entry":
            if self._context.entry_system_prompt_snapshot is None:
                raise RepositoryError("entry system prompt is unavailable")
            _, event_id = self._repository.pause_review_for_entry(
                self._workflow_id, str(uuid4()), self._context.entry_system_prompt_snapshot,
                self._context.ingress.message_text, result.assistant_response or "", self._created_at,
            )
            return ModelTurnCommit(event_id, result.assistant_response or "")
        raise ReviewContractError("review transition result is invalid")

    def commit_terminal_failure(self, failure_kind: FailureKind) -> ModelTurnCommit:
        del failure_kind
        text = (
            "刚才没有处理成功，请再发一次刚才的答案。"
            if self._mode == "answer_question"
            else "刚才没有处理成功，请再明确说一次“开始复习”。"
        )
        event_id = self._repository.append_log_event(
            self._workflow_id, "assistant_response", {"text": text}, self._created_at, require_active_type="review"
        )
        return ModelTurnCommit(event_id, text)
