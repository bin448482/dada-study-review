"""Entry-specific boundary between the Graph and shared model-turn pipeline."""

from __future__ import annotations

from typing import Any

from v3_workflow.model_turn.contracts import FailureKind, ModelTurnCommit
from v3_workflow.model_turn.pipeline import ModelTurnContractError

from ..contracts.entry_turn import EntryMachineTurn
from ..contracts.validation import ContractError, validate_entry_state_machine_result
from ..gateway.port import ModelGateway
from ..persistence.repository import EntryRepository, RepositoryError


class EntryModelTurnAdapter:
    """Owns Entry validation, transition commit and child-visible failure text."""

    def __init__(self, repository: EntryRepository, gateway: ModelGateway, workflow_id: str, source_child_event_id: str, mode: str, created_at: str, review_handoff: object | None, external_session_ref: str, trigger_message: str) -> None:
        self._repository = repository
        self._gateway = gateway
        self._workflow_id = workflow_id
        self._source_child_event_id = source_child_event_id
        self._mode = mode
        self._created_at = created_at
        self._review_handoff = review_handoff
        self._external_session_ref = external_session_ref
        self._trigger_message = trigger_message

    def prepare(self, turn: EntryMachineTurn) -> Any:
        return self._gateway.prepare(turn)

    def execute(self, prepared: Any) -> Any:
        return self._gateway.execute(prepared)

    def append_log_event(self, event_type: str, payload: dict[str, Any], created_at: str) -> str:
        return self._repository.append_log_event(self._workflow_id, event_type, payload, created_at)

    def validate(self, output: Any, turn: EntryMachineTurn) -> Any:
        try:
            result = validate_entry_state_machine_result(output)
            if self._mode == "start_entry" and result.next_operation != "reply_only":
                raise ContractError("start_entry only accepts reply_only")
            if self._mode == "start_entry" and (result.guidance_kind is not None or result.requested_transition is not None):
                raise ContractError("start_entry may not request guidance or a transition")
            return result
        except ContractError as exc:
            raise ModelTurnContractError(str(exc)) from exc

    def commit(self, result: Any, source_llm_response_event_id: str) -> ModelTurnCommit:
        if result.requested_transition == "finish_entry":
            _, event_id, response = self._repository.finish_entry_if_ready(self._workflow_id, self._created_at)
            return ModelTurnCommit(event_id, response)
        if result.requested_transition == "start_review":
            pending_reentries = self._repository.get_pending_reentry_requests(self._workflow_id)
            if pending_reentries:
                count = len(pending_reentries)
                text = f"还有 {count} 条需要补充。先补完再开始复习吧。"
                event_id = self._repository.commit_control_response(self._workflow_id, text, self._created_at)
                return ModelTurnCommit(event_id, text)
            handoff = self._review_handoff
            if handoff is None or not hasattr(handoff, "start_from_entry"):
                event_id = self._repository.commit_control_response(self._workflow_id, "现在还不能切换到复习，请继续录入。", self._created_at)
                return ModelTurnCommit(event_id, "现在还不能切换到复习，请继续录入。")
            outcome = handoff.start_from_entry(
                self._workflow_id, self._external_session_ref, self._trigger_message, self._created_at
            )
            if outcome is None:
                event_id = self._repository.commit_control_response(self._workflow_id, "现在还不能开始复习，请继续录入或稍后再试。", self._created_at)
                return ModelTurnCommit(event_id, "现在还不能开始复习，请继续录入或稍后再试。")
            return ModelTurnCommit(outcome.last_event_id, outcome.reply_text, {"switched_to_review": bool(getattr(outcome, "switched_to_review", True))})
        event_id, response_text = self._repository.commit_state_machine_result(
            self._workflow_id, self._source_child_event_id, result, self._created_at
        )
        return ModelTurnCommit(event_id, response_text)

    def commit_terminal_failure(self, failure_kind: FailureKind) -> ModelTurnCommit:
        text = "刚才这句没有处理成功，请原样再发一次。"
        event_id = self._repository.commit_control_response(self._workflow_id, text, self._created_at)
        return ModelTurnCommit(event_id, text)
