"""Private v3 review Graph nodes over fixed shared-workflow actions."""

from __future__ import annotations

from typing import Any, Callable
from uuid import uuid4

from langgraph.runtime import Runtime

from v3_workflow.model_turn import ModelTurnPipeline, ModelTurnSpec
from v3_workflow.persistence.repository import RepositoryError, WorkflowRepository

from ..contracts.review_turn import ReviewMachineTurn
from ..gateway.port import ReviewModelGateway
from .model_turn_adapter import ReviewModelTurnAdapter
from .state import ReviewGraphState, ReviewRuntimeContext
from ..question_modes import select_question_mode


TASK_NAME = "dada.review_state_machine_turn"
TASK_VERSION = 5


def _context(runtime: Runtime[ReviewRuntimeContext]) -> ReviewRuntimeContext:
    if runtime.context is None:
        raise RepositoryError("review graph requires runtime context")
    return runtime.context


def review_route(repository: WorkflowRepository):
    def node(state: ReviewGraphState, runtime: Runtime[ReviewRuntimeContext]) -> dict[str, Any]:
        context = _context(runtime)
        ingress = context.ingress
        active = repository.get_active_workflow(ingress.external_session_ref, "review")
        if active is None:
            if not ingress.start_requested:
                return {"node": "review_terminal", "route": "terminal", "recovery_required": False}
            candidate = state.get("workflow_id")
            if not candidate:
                raise RepositoryError("review graph has no candidate workflow id")
            workflow = repository.request_review_start(candidate, ingress.external_session_ref, ingress.received_at)
            context.delivery.handled = True
            if workflow is None:
                context.delivery.no_due_item = True
                return {"node": "review_terminal", "route": "terminal", "recovery_required": False}
            prompt_event_id = repository.append_review_system_prompt(workflow["workflow_id"], context.system_prompt_snapshot, ingress.received_at)
            child_event_id = repository.append_child_message(workflow["workflow_id"], ingress.message_text, ingress.received_at, "review")
            return {
                "workflow_id": workflow["workflow_id"], "node": "review_starting", "last_committed_event_id": child_event_id,
                "source_child_event_id": child_event_id,
                "recovery_required": False, "task_contract_name": TASK_NAME, "task_contract_version": TASK_VERSION,
                "route": "start", "turn_mode": "start_review",
            }
        context.delivery.handled = True
        workflow_id = active["workflow_id"]
        previous_node = state.get("node")
        if previous_node not in (None, "review_waiting_for_child", "review_terminal"):
            return {
                "workflow_id": workflow_id, "node": "review_recover", "last_committed_event_id": state.get("last_committed_event_id"),
                "source_child_event_id": state.get("source_child_event_id"),
                "recovery_required": True, "task_contract_name": TASK_NAME, "task_contract_version": TASK_VERSION,
                "route": "recover", "turn_mode": None,
            }
        if ingress.start_requested:
            # This is the already-authorized entry→review handoff resume
            # marker, not a child activity message. Preserve its locked
            # question without re-running the Review state-machine LLM.
            return {"workflow_id": workflow_id, "node": "review_waiting_for_child", "route": "terminal", "recovery_required": False}
        review_context = repository.get_review_context(workflow_id)
        if review_context["locked_question_mode"] is None:
            # The preceding assessment committed and the next-question model
            # turn reached a terminal failure. A new explicit message becomes
            # the frozen trigger for that still-pending queue item.
            child_event_id = repository.append_child_message(workflow_id, ingress.message_text, ingress.received_at, "review")
            return {
                "workflow_id": workflow_id, "node": "review_next_question", "last_committed_event_id": child_event_id,
                "source_child_event_id": child_event_id, "recovery_required": False,
                "task_contract_name": TASK_NAME, "task_contract_version": TASK_VERSION,
                "route": "next", "turn_mode": "next_question",
            }
        pending = repository.get_unprocessed_review_child_event(workflow_id)
        if pending is not None:
            base = {"workflow_id": workflow_id, "last_committed_event_id": pending["event_id"], "source_child_event_id": pending["event_id"], "recovery_required": True, "task_contract_name": TASK_NAME, "task_contract_version": TASK_VERSION}
            return {**base, "node": "review_process_answer", "route": "answer", "turn_mode": "answer_question"}
        child_event_id = repository.append_child_message(workflow_id, ingress.message_text, ingress.received_at, "review")
        base = {"workflow_id": workflow_id, "last_committed_event_id": child_event_id, "source_child_event_id": child_event_id, "recovery_required": False, "task_contract_name": TASK_NAME, "task_contract_version": TASK_VERSION}
        return {**base, "node": "review_process_answer", "route": "answer", "turn_mode": "answer_question"}
    return node


def _model_turn(repository: WorkflowRepository, workflow_id: str, child_event_id: str, mode: str, question_mode_selector: Callable[[str, tuple[str, ...]], str]) -> ReviewMachineTurn:
    context = repository.get_review_context(workflow_id)
    history = repository.list_review_history(workflow_id, limit=20)
    current = {"event_id": child_event_id, "text": repository.get_child_message(workflow_id, child_event_id)}
    locked = None
    if context["locked_question_mode"] is not None:
        import json
        locked = {
            "question_sequence": context["question_sequence"], "question_mode": context["locked_question_mode"],
            "question_json": json.loads(context["locked_question_json"]), "item_revision": context["locked_item_revision"],
        }
    return ReviewMachineTurn(
        TASK_NAME, TASK_VERSION, mode, "review_starting" if mode == "start_review" else ("review_next_question" if mode == "next_question" else "review_process_answer"), workflow_id,
            {"learning_item_id": context["learning_item_id"], "reference_text": context["item_reference_text"], "meaning_zh": context["meaning_zh"], "revision": context["revision"], "unit_type": context["unit_type"], "review_context": context.get("review_context")},
        select_question_mode(context["unit_type"], repository.list_review_question_modes(workflow_id), question_mode_selector),
        current, locked, history, len(history) == 20,
    )


def review_model_turn(repository: WorkflowRepository, gateway: ReviewModelGateway, question_mode_selector: Callable[[str, tuple[str, ...]], str], pipeline: ModelTurnPipeline | None = None):
    model_turn_pipeline = pipeline or ModelTurnPipeline()
    def node(state: ReviewGraphState, runtime: Runtime[ReviewRuntimeContext]) -> dict[str, Any]:
        context = _context(runtime)
        workflow_id, child_event_id, mode = state["workflow_id"], state.get("source_child_event_id"), state.get("turn_mode")
        if not child_event_id or mode not in ("start_review", "answer_question", "next_question"):
            raise RepositoryError("review model node has no current child event")
        turn = _model_turn(repository, workflow_id, child_event_id, mode, question_mode_selector)
        adapter = ReviewModelTurnAdapter(repository, gateway, workflow_id, mode, context.ingress.received_at, context)
        outcome = model_turn_pipeline.run(
            ModelTurnSpec(workflow_id, child_event_id, context.ingress.received_at, TASK_NAME, TASK_VERSION, turn), adapter
        )
        if outcome.metadata and outcome.metadata.get("next_question"):
            return {"node": "review_next_question", "last_committed_event_id": outcome.last_event_id, "source_child_event_id": child_event_id, "route": "next", "turn_mode": "next_question"}
        context.delivery.reply_text = outcome.reply_text
        return {"node": "review_terminal" if outcome.success and outcome.result.next_operation in {"complete_assessment", "request_transition"} else "review_waiting_for_child", "last_committed_event_id": outcome.last_event_id, "route": "terminal", "turn_mode": None}
    return node


def review_closing(repository: WorkflowRepository):
    def node(state: ReviewGraphState, runtime: Runtime[ReviewRuntimeContext]) -> dict[str, Any]:
        context = _context(runtime)
        event_id = repository.stop_review(state["workflow_id"], STOP_RESPONSE, context.ingress.received_at)
        context.delivery.reply_text = STOP_RESPONSE
        return {"node": "review_terminal", "last_committed_event_id": event_id, "route": "terminal", "turn_mode": None}
    return node


def review_recover(repository: WorkflowRepository):
    def node(state: ReviewGraphState, runtime: Runtime[ReviewRuntimeContext]) -> dict[str, Any]:
        context = _context(runtime)
        workflow_id = state.get("workflow_id")
        if workflow_id:
            context.delivery.handled = True
            stored_reply = repository.get_locked_question_delivery(workflow_id)
            review_context = repository.get_review_context(workflow_id)
            if review_context["locked_question_mode"] is not None:
                import json
                context.delivery.question_mode = review_context["locked_question_mode"]
                context.delivery.question_json = json.loads(review_context["locked_question_json"])
                progress = repository.get_review_queue_progress(workflow_id)
                context.delivery.progress_text = f"第 {progress['completed'] + 1} / {progress['total']} 题，还剩 {progress['remaining']} 题。"
                first_prefix = f"这轮共 {progress['total']} 题，现在从第 1 题开始。\n\n"
                next_prefix = f"{context.delivery.progress_text}\n\n"
                context.delivery.reply_text = stored_reply.removeprefix(first_prefix).removeprefix(next_prefix)
                context.delivery.speech_text = context.delivery.reply_text
            else:
                context.delivery.reply_text = stored_reply
        return {"node": "review_waiting_for_child", "recovery_required": False, "route": "terminal", "turn_mode": None}
    return node
