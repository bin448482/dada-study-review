"""Controlled Entry-to-Review handoff using the shared model-turn pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from v3_workflow.model_turn import ModelTurnCommit, ModelTurnPipeline, ModelTurnSpec
from v3_workflow.model_turn.contracts import FailureKind
from v3_workflow.model_turn.pipeline import ModelTurnContractError
from v3_workflow.persistence.repository import RepositoryError, WorkflowRepository

from .contracts.review_turn import ReviewMachineTurn
from .contracts.validation import ReviewContractError, render_locked_question, validate_review_state_machine_result
from .gateway.port import ReviewModelGateway


@dataclass(frozen=True)
class EntryReviewHandoffOutcome:
    reply_text: str
    last_event_id: str
    switched_to_review: bool = True


class EntryReviewHandoff:
    """Evaluates a first question before the atomic entry→review transition."""

    def __init__(self, repository: WorkflowRepository, gateway: ReviewModelGateway, system_prompt_snapshot: str, checkpoint_waiting: Callable[[str, str, str, str], bool], question_mode_selector: Callable[[str, tuple[str, ...]], str]) -> None:
        self._repository = repository
        self._gateway = gateway
        self._system_prompt_snapshot = system_prompt_snapshot
        self._checkpoint_waiting = checkpoint_waiting
        self._question_mode_selector = question_mode_selector

    def start_from_entry(self, entry_workflow_id: str, external_session_ref: str, trigger_message: str, received_at: str) -> EntryReviewHandoffOutcome | None:
        preview = self._repository.preview_entry_review_start(entry_workflow_id, received_at)
        if preview is None:
            return None
        if preview["kind"] == "resume":
            review_id, resume_event_id = self._repository.resume_review_from_entry(entry_workflow_id, received_at)
            if not self._checkpoint_waiting(review_id, external_session_ref, trigger_message, received_at):
                return None
            return EntryReviewHandoffOutcome("已恢复刚才的复习题。", resume_event_id)
        item = preview["item"]
        review_id, review_child_event_id = str(uuid4()), str(uuid4())
        source_child_event_id = self._repository.get_latest_child_event_id(entry_workflow_id)
        selected_question_mode = self._question_mode_selector(item["unit_type"], ())
        turn = ReviewMachineTurn(
            "dada.review_state_machine_turn", 3, "start_review", "review_starting", review_id,
            {"learning_item_id": item["learning_item_id"], "reference_text": item["reference_text"], "meaning_zh": item["meaning_zh"], "revision": item["revision"], "unit_type": item["unit_type"]},
            selected_question_mode, {"event_id": review_child_event_id, "text": trigger_message}, None, (), False,
        )
        adapter = _HandoffModelTurnAdapter(
            self._repository, self._gateway, entry_workflow_id, review_id, review_child_event_id,
            source_child_event_id, self._system_prompt_snapshot, trigger_message, received_at, selected_question_mode,
        )
        outcome = ModelTurnPipeline().run(
            ModelTurnSpec(entry_workflow_id, source_child_event_id, received_at, "dada.review_state_machine_turn", 3, turn), adapter
        )
        if outcome.infrastructure_failed or outcome.reply_text is None or outcome.last_event_id is None:
            return None
        if not outcome.success:
            return EntryReviewHandoffOutcome(outcome.reply_text, outcome.last_event_id, False)
        if not self._checkpoint_waiting(review_id, external_session_ref, trigger_message, received_at):
            return None
        return EntryReviewHandoffOutcome(outcome.reply_text, outcome.last_event_id)


class _HandoffModelTurnAdapter:
    """Buffers prospective review events until the atomic handoff commit."""

    def __init__(self, repository: WorkflowRepository, gateway: ReviewModelGateway, entry_workflow_id: str, review_workflow_id: str, review_child_event_id: str, source_child_event_id: str, system_prompt: str, trigger_message: str, created_at: str, selected_question_mode: str) -> None:
        self._repository = repository
        self._gateway = gateway
        self._entry_workflow_id = entry_workflow_id
        self._review_workflow_id = review_workflow_id
        self._review_child_event_id = review_child_event_id
        self._source_child_event_id = source_child_event_id
        self._system_prompt = system_prompt
        self._trigger_message = trigger_message
        self._created_at = created_at
        self._selected_question_mode = selected_question_mode
        self._prepared: Any | None = None
        self._execution: Any | None = None
        self._pending_events: list[tuple[str, dict[str, Any], str]] = []

    def prepare(self, turn: Any) -> Any:
        self._prepared = self._gateway.prepare(turn)
        return self._prepared

    def execute(self, prepared: Any) -> Any:
        self._execution = self._gateway.execute(prepared)
        return self._execution

    def append_log_event(self, event_type: str, payload: dict[str, Any], created_at: str) -> str:
        self._pending_events.append((event_type, dict(payload), created_at))
        return f"deferred-{len(self._pending_events)}"

    def validate(self, output: Any, turn: Any) -> Any:
        try:
            result = validate_review_state_machine_result(output, self._selected_question_mode)
        except ReviewContractError as exc:
            raise ModelTurnContractError(str(exc)) from exc
        if result.next_operation != "ask_question":
            raise ModelTurnContractError("entry handoff requires an ask_question result")
        return result

    def commit(self, result: Any, source_llm_response_event_id: str) -> ModelTurnCommit:
        del source_llm_response_event_id
        if self._prepared is None or self._execution is None:
            raise RepositoryError("handoff has no completed gateway execution")
        reply_text = render_locked_question(result.question_json or {})
        _, event_id = self._repository.commit_entry_to_new_review_question(
            self._entry_workflow_id, self._review_workflow_id, self._review_child_event_id,
            self._system_prompt, self._trigger_message, self._prepared.request, self._prepared.provider,
            self._prepared.model, {"output": self._execution.output, "internal_reasoning": self._execution.internal_reasoning},
            result.question_mode or "", result.question_json or {}, reply_text, self._created_at,
        )
        return ModelTurnCommit(event_id, reply_text)

    def commit_terminal_failure(self, failure_kind: FailureKind) -> ModelTurnCommit:
        del failure_kind
        # No review workflow exists on failure. Retain the attempt audit on the
        # still-active entry workflow, then make the failure a terminal child
        # outcome so later inbound traffic never replays it indefinitely.
        for event_type, payload, created_at in self._pending_events:
            self._repository.append_log_event(
                self._entry_workflow_id, event_type, payload, created_at, require_active_type="entry"
            )
        text = "现在还不能开始复习，请继续录入或稍后再试。"
        event_id = self._repository.append_log_event(
            self._entry_workflow_id, "assistant_response", {"text": text}, self._created_at, require_active_type="entry"
        )
        return ModelTurnCommit(event_id, text)
