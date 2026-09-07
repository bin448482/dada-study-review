"""Dialogue Graph routing and fixed model-turn orchestration."""

from __future__ import annotations

from typing import Any
import json
from uuid import uuid4

from langgraph.runtime import Runtime

from v3_workflow.model_turn import ModelTurnPipeline, ModelTurnSpec
from v3_workflow.persistence.repository import RepositoryError, WorkflowRepository

from ..contracts.dialogue_turn import DialogueMachineTurn
from ..gateway.port import DialogueModelGateway
from ..unit_definition import UnitDefinition
from .model_turn_adapter import DialogueModelTurnAdapter
from .selection import build_round_plan
from .state import DialogueGraphState, DialogueRuntimeContext


TASK_NAME = "dada.dialogue_state_machine_turn"
TASK_VERSION = 1


def _context(runtime: Runtime[DialogueRuntimeContext]) -> DialogueRuntimeContext:
    if runtime.context is None:
        raise RepositoryError("dialogue graph requires runtime context")
    return runtime.context


def _pending_ids(value: object) -> tuple[str, ...]:
    """Decode the legacy scalar column or the new JSON list representation."""

    if value is None or value == "":
        return ()
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return (value,)
        if isinstance(decoded, list) and all(isinstance(item, str) and item for item in decoded):
            return tuple(decoded)
        return (value,)
    return ()


def dialogue_route(repository: WorkflowRepository):
    def node(state: DialogueGraphState, runtime: Runtime[DialogueRuntimeContext]) -> dict[str, Any]:
        context = _context(runtime)
        ingress = context.ingress
        active = repository.get_active_workflow(ingress.external_session_ref)
        if active is None:
            if not ingress.start_requested:
                return {"node": "dialogue_terminal", "route": "terminal", "turn_mode": None}
            workflow_id = state.get("workflow_id")
            if not workflow_id:
                raise RepositoryError("dialogue graph has no candidate workflow id")
            snapshot = repository.get_dialogue_mastery_snapshot(ingress.external_session_ref, context.unit.unit_id, context.unit.version)
            retryable = repository.get_dialogue_retryable_targets(ingress.external_session_ref, context.unit.unit_id, context.unit.version)
            retryable_intents = repository.get_dialogue_retryable_question_intents(ingress.external_session_ref, context.unit.unit_id, context.unit.version)
            used_intents = repository.get_dialogue_used_question_intents(ingress.external_session_ref, context.unit.unit_id, context.unit.version)
            try:
                plan = build_round_plan(
                    context.unit, snapshot, retryable, used_intents, retryable_intents,
                    max_steps=context.policy.max_round_content_turns,
                )
            except ValueError as exc:
                if str(exc) != "Unit has no eligible round steps":
                    raise
                context.delivery.handled = True
                context.delivery.state_text = "【英语对话】当前没有可安排的新问题。"
                context.delivery.progress_text = "需要继续时，请再次说“开始对话”。"
                context.delivery.reply_text = "\n\n".join((context.delivery.state_text, context.delivery.progress_text))
                context.delivery.speech_text = context.delivery.reply_text
                return {"node": "dialogue_terminal", "route": "terminal", "turn_mode": None}
            first = plan["steps"][0]
            scenario_id, target_id = str(first["scenario_id"]), str(first["target_id"])
            child_event_id = repository.start_dialogue(
                workflow_id, ingress.external_session_ref, context.system_prompt_snapshot, ingress.message_text,
                context.unit.unit_id, context.unit.version, context.unit.content_hash, scenario_id, target_id,
                int(first["entry_difficulty"]), ingress.received_at, plan,
            )
            context.delivery.handled = True
            return {"workflow_id": workflow_id, "node": "dialogue_starting", "source_child_event_id": child_event_id,
                    "last_committed_event_id": child_event_id, "task_contract_name": TASK_NAME,
                    "task_contract_version": TASK_VERSION, "route": "start", "turn_mode": "start_dialogue"}
        if active["workflow_type"] != "dialogue":
            return {"node": "dialogue_terminal", "route": "terminal", "turn_mode": None}
        context.delivery.handled = True
        workflow_id = str(active["workflow_id"])
        if ingress.start_requested:
            return {"workflow_id": workflow_id, "node": "dialogue_terminal", "route": "terminal", "turn_mode": None}
        if state.get("node") not in (None, "dialogue_waiting_for_child", "dialogue_terminal"):
            return {"workflow_id": workflow_id, "node": "dialogue_recover", "route": "recover", "turn_mode": None}
        pending = repository.get_unprocessed_dialogue_child_event(workflow_id)
        if pending is None:
            child_event_id = repository.append_child_message(workflow_id, ingress.message_text, ingress.received_at, "dialogue")
        else:
            child_event_id = pending["event_id"]
        dialogue_state = repository.get_dialogue_context(workflow_id)
        mode = "continue_dialogue"
        return {"workflow_id": workflow_id, "node": "dialogue_continuing",
                "source_child_event_id": child_event_id, "last_committed_event_id": child_event_id,
                "task_contract_name": TASK_NAME, "task_contract_version": TASK_VERSION,
                "route": "continue", "turn_mode": mode}
    return node


def _machine_turn(repository: WorkflowRepository, context: DialogueRuntimeContext, workflow_id: str, child_event_id: str, mode: str) -> DialogueMachineTurn:
    state = repository.get_dialogue_context(workflow_id)
    unit = context.unit
    if state["unit_id"] != unit.unit_id or int(state["unit_version"]) != unit.version or state["unit_content_hash"] != unit.content_hash:
        raise RepositoryError("loaded Unit differs from the workflow-frozen package")
    scenario = unit.scenarios_by_id.get(str(state["current_scenario_id"]))
    target = unit.targets_by_id.get(str(state["current_target_id"]))
    if scenario is None or target is None or str(target["target_id"]) not in scenario["allowed_target_ids"]:
        raise RepositoryError("dialogue control state is outside the loaded Unit")
    history = repository.list_dialogue_history(workflow_id, context.policy.history_limit)
    snapshot = repository.get_dialogue_mastery_snapshot(state["external_session_id"], unit.unit_id, unit.version)
    plan_data = json.loads(state.get("round_plan_json") or "{}")
    steps = list(plan_data.get("steps", []))
    step_index = int(state.get("current_step_index", 0))
    active_step = dict(steps[step_index]) if 0 <= step_index < len(steps) else {}
    used_intents = tuple(sorted(repository.get_dialogue_used_question_intents(state["external_session_id"], unit.unit_id, unit.version)))
    return DialogueMachineTurn(
        TASK_NAME, TASK_VERSION, mode, "dialogue_continuing",
        workflow_id,
        {"unit_id": unit.unit_id, "version": unit.version, "title": unit.title, "targets": [dict(item) for item in unit.targets]},
        scenario, target, int(state["current_difficulty_level"]), _pending_ids(state["pending_repetition_target_id"]), state["subphase"],
        snapshot, {"event_id": child_event_id, "text": repository.get_child_message(workflow_id, child_event_id)},
        history, len(history) == context.policy.history_limit, plan_data, active_step, used_intents,
        context.policy.max_candidate_per_turn,
    )


def dialogue_model_turn(repository: WorkflowRepository, gateway: DialogueModelGateway, pipeline: ModelTurnPipeline | None = None):
    model_turn_pipeline = pipeline or ModelTurnPipeline()
    def node(state: DialogueGraphState, runtime: Runtime[DialogueRuntimeContext]) -> dict[str, Any]:
        context = _context(runtime)
        workflow_id = state["workflow_id"]
        child_event_id = state.get("source_child_event_id")
        mode = state.get("turn_mode")
        if not child_event_id or mode not in ("start_dialogue", "continue_dialogue"):
            raise RepositoryError("dialogue model node has no child event")
        turn = _machine_turn(repository, context, workflow_id, child_event_id, mode)
        adapter = DialogueModelTurnAdapter(repository, gateway, workflow_id, context.ingress.received_at, context, repository.get_dialogue_context(workflow_id), turn)
        outcome = model_turn_pipeline.run(ModelTurnSpec(workflow_id, child_event_id, context.ingress.received_at, TASK_NAME, TASK_VERSION, turn), adapter)
        context.delivery.reply_text = outcome.reply_text
        return {"node": "dialogue_terminal" if outcome.metadata and outcome.metadata.get("closed") else "dialogue_waiting_for_child",
                "last_committed_event_id": outcome.last_event_id, "route": "terminal", "turn_mode": None}
    return node


def dialogue_recover(repository: WorkflowRepository):
    def node(state: DialogueGraphState, runtime: Runtime[DialogueRuntimeContext]) -> dict[str, Any]:
        context = _context(runtime)
        workflow_id = state.get("workflow_id")
        if workflow_id:
            context.delivery.handled = True
            pending = repository.get_unprocessed_dialogue_child_event(workflow_id)
            if pending is None:
                stored = repository.get_latest_assistant_delivery(workflow_id)
                context.delivery.reply_text = None if stored is None else stored[1]
                context.delivery.speech_text = context.delivery.reply_text
            else:
                # The next inbound call routes this frozen event through the
                # model path; recovery itself never appends another message.
                context.delivery.reply_text = "刚才的内容还在处理中，请再发一次刚才的话。"
        return {"node": "dialogue_waiting_for_child", "route": "terminal", "turn_mode": None}
    return node
