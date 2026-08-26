"""Private v3 entry Graph nodes; all facts flow through injected boundaries."""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from v3_workflow.model_turn import ModelTurnPipeline, ModelTurnSpec

from ..contracts.entry_turn import EntryMachineTurn
from ..gateway.port import ModelGateway
from ..persistence.repository import EntryRepository, RepositoryError
from .model_turn_adapter import EntryModelTurnAdapter
from .state import EntryGraphState, EntryRuntimeContext


TASK_NAME = "dada.entry_state_machine_turn"
TASK_VERSION = 1


def _context(runtime: Runtime[EntryRuntimeContext]) -> EntryRuntimeContext:
    if runtime.context is None:
        raise RepositoryError("entry graph requires a runtime context")
    return runtime.context


def entry_route(repository: EntryRepository):
    def node(state: EntryGraphState, runtime: Runtime[EntryRuntimeContext]) -> dict[str, Any]:
        context = _context(runtime)
        ingress = context.ingress
        if not isinstance(ingress.external_session_ref, str) or not ingress.external_session_ref:
            raise RepositoryError("entry route requires an external session")
        active = repository.get_collecting_entry(ingress.external_session_ref)
        if active is None:
            if not ingress.start_requested:
                return {"node": "entry_terminal", "route": "terminal", "recovery_required": False}
            workflow_id = state.get("entry_workflow_id")
            if not workflow_id:
                raise RepositoryError("entry graph has no candidate workflow ID")
            child_event_id = repository.start_entry(
                workflow_id,
                ingress.external_session_ref,
                context.system_prompt_snapshot,
                ingress.message_text,
                ingress.received_at,
            )
            context.delivery.handled = True
            return {
                "entry_workflow_id": workflow_id,
                "node": "entry_process_turn",
                "last_committed_event_id": child_event_id,
                "recovery_required": False,
                "task_contract_name": TASK_NAME,
                "task_contract_version": TASK_VERSION,
                "route": "process",
                "turn_mode": "start_entry",
            }

        workflow_id = active["entry_workflow_id"]
        context.delivery.handled = True
        previous_node = state.get("node")
        if previous_node not in (None, "entry_route", "entry_waiting_for_child", "entry_terminal"):
            recovery_event = repository.record_recovery(workflow_id, ingress.received_at)
            return {
                "entry_workflow_id": workflow_id,
                "node": "entry_recover",
                "last_committed_event_id": recovery_event,
                "recovery_required": True,
                "task_contract_name": TASK_NAME,
                "task_contract_version": TASK_VERSION,
                "route": "recover",
                "turn_mode": None,
            }
        pending = repository.get_unprocessed_entry_child_event(workflow_id)
        if pending is not None:
            return {
                "entry_workflow_id": workflow_id,
                "node": "entry_process_turn",
                "last_committed_event_id": pending["event_id"],
                "recovery_required": True,
                "task_contract_name": TASK_NAME,
                "task_contract_version": TASK_VERSION,
                "route": "process",
                "turn_mode": pending["turn_mode"],
            }
        child_event_id = repository.append_child_message(workflow_id, ingress.message_text, ingress.received_at)
        base = {
            "entry_workflow_id": workflow_id,
            "last_committed_event_id": child_event_id,
            "recovery_required": False,
            "task_contract_name": TASK_NAME,
            "task_contract_version": TASK_VERSION,
        }
        return {**base, "node": "entry_process_turn", "route": "process", "turn_mode": "collect_message"}

    return node


def entry_process_turn(repository: EntryRepository, gateway: ModelGateway, pipeline: ModelTurnPipeline | None = None):
    model_turn_pipeline = pipeline or ModelTurnPipeline()
    def node(state: EntryGraphState, runtime: Runtime[EntryRuntimeContext]) -> dict[str, Any]:
        context = _context(runtime)
        workflow_id = state["entry_workflow_id"]
        child_event_id = state["last_committed_event_id"]
        mode = state.get("turn_mode")
        if not child_event_id or mode not in ("start_entry", "collect_message"):
            raise RepositoryError("entry process node has no current child message")
        message_text = repository.get_child_message(workflow_id, child_event_id)
        turn = EntryMachineTurn(
            task_contract_name=TASK_NAME,
            task_contract_version=TASK_VERSION,
            mode=mode,
            active_mode="entry",
            graph_node="entry_process_turn",
            event_id=child_event_id,
            message_text=message_text,
            pending_reentry_requests=repository.get_pending_reentry_requests(workflow_id),
        )
        adapter = EntryModelTurnAdapter(
            repository, gateway, workflow_id, child_event_id, mode, context.ingress.received_at,
            context.review_handoff, context.ingress.external_session_ref, context.ingress.message_text,
        )
        outcome = model_turn_pipeline.run(
            ModelTurnSpec(workflow_id, child_event_id, context.ingress.received_at, TASK_NAME, TASK_VERSION, turn), adapter
        )
        context.delivery.reply_text = outcome.reply_text
        return {
            "node": "entry_terminal" if outcome.metadata and outcome.metadata.get("switched_to_review") else "entry_waiting_for_child",
            "last_committed_event_id": outcome.last_event_id,
            "route": "terminal",
            "turn_mode": None,
        }

    return node


def entry_closing(repository: EntryRepository):
    def node(state: EntryGraphState, runtime: Runtime[EntryRuntimeContext]) -> dict[str, Any]:
        context = _context(runtime)
        _, event_id, response_text = repository.finish_entry_if_ready(state["entry_workflow_id"], context.ingress.received_at)
        context.delivery.reply_text = response_text
        return {"node": "entry_terminal", "last_committed_event_id": event_id, "route": "terminal", "turn_mode": None}

    return node


def entry_switch_to_review(repository: EntryRepository):
    def node(state: EntryGraphState, runtime: Runtime[EntryRuntimeContext]) -> dict[str, Any]:
        context = _context(runtime)
        handoff = context.review_handoff
        if handoff is None or not hasattr(handoff, "start_from_entry"):
            # Composition has not supplied the separate review Graph. Never
            # pretend the mode changed when that dependency is absent.
            return {"node": "entry_waiting_for_child", "route": "terminal", "turn_mode": None}
        try:
            outcome = handoff.start_from_entry(
                state["entry_workflow_id"], context.ingress.external_session_ref, context.ingress.message_text, context.ingress.received_at
            )
        except Exception:
            return {"node": "entry_waiting_for_child", "route": "terminal", "turn_mode": None}
        if outcome is None:
            return {"node": "entry_waiting_for_child", "route": "terminal", "turn_mode": None}
        context.delivery.reply_text = outcome.reply_text
        return {"node": "entry_terminal", "last_committed_event_id": outcome.last_event_id, "route": "terminal", "turn_mode": None}

    return node


def entry_recover(repository: EntryRepository) -> Any:
    def node(state: EntryGraphState, runtime: Runtime[EntryRuntimeContext]) -> dict[str, Any]:
        context = _context(runtime)
        pending = repository.get_unprocessed_entry_child_event(state["entry_workflow_id"])
        if pending is not None:
            return {
                "node": "entry_process_turn", "last_committed_event_id": pending["event_id"],
                "recovery_required": True, "route": "process", "turn_mode": pending["turn_mode"],
            }
        delivery = repository.get_latest_assistant_delivery(state["entry_workflow_id"])
        if delivery is not None:
            event_id, text = delivery
            context.delivery.handled = True
            context.delivery.reply_text = text
            return {"node": "entry_waiting_for_child", "last_committed_event_id": event_id, "recovery_required": False, "route": "terminal", "turn_mode": None}
        return {"node": "entry_waiting_for_child", "recovery_required": False, "route": "terminal", "turn_mode": None}

    return node
