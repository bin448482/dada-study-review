"""Strict validation for newly written unified workflow events."""

from __future__ import annotations

from typing import Any, Iterable

from .workflow_events import EVENT_CONTRACT_NAME, EVENT_CONTRACT_VERSION, EVENT_TYPES


class WorkflowContractError(ValueError):
    """A supplied event does not satisfy the fixed shared workflow contract."""


def validate_workflow_log_event(event_type: str, data: Any) -> dict[str, Any]:
    """Return a strict `dada.workflow_log_event` v1 envelope.

    This is deliberately structural: English text, questions, correction and
    assessment content remain the controlled LLM's responsibility.
    """

    if event_type not in EVENT_TYPES:
        raise WorkflowContractError("workflow log event type is unsupported")
    if event_type in {"system_prompt", "child_message", "internal_reasoning"}:
        item = _exact_mapping(data, {"text"}, f"{event_type} event")
        _text(item["text"], f"{event_type} text")
    elif event_type == "llm_request":
        item = _exact_mapping(data, {"provider", "model", "request", "source_child_event_id"}, "llm request")
        _text(item["provider"], "provider")
        _text(item["model"], "model")
        _text(item["source_child_event_id"], "source child event id")
        if not isinstance(item["request"], dict):
            raise WorkflowContractError("llm request must be an object")
    elif event_type == "internal_reasoning_unavailable":
        item = _exact_mapping(data, {"reason_code"}, "reasoning unavailable")
        _enum(item["reason_code"], ("provider_not_returned", "provider_unsupported", "not_exposed_by_adapter", "provider_failed", "contract_rejected"), "reason code")
    elif event_type == "tool_call":
        item = _exact_mapping(data, {"tool_name", "arguments"}, "tool call")
        _text(item["tool_name"], "tool name")
        if not isinstance(item["arguments"], dict):
            raise WorkflowContractError("tool arguments must be an object")
    elif event_type == "tool_result":
        item = _exact_mapping(data, {"tool_name", "is_error", "result"}, "tool result")
        _text(item["tool_name"], "tool result name")
        if type(item["is_error"]) is not bool:
            raise WorkflowContractError("tool result is_error must be boolean")
    elif event_type == "llm_response":
        item = _exact_mapping(data, {"task_contract_name", "task_contract_version", "output", "source_child_event_id"}, "llm response")
        _text(item["task_contract_name"], "task contract name")
        if type(item["task_contract_version"]) is not int or item["task_contract_version"] <= 0:
            raise WorkflowContractError("task contract version must be a positive integer")
        _text(item["source_child_event_id"], "source child event id")
    elif event_type == "model_turn_attempt_failed":
        item = _exact_mapping(data, {"source_child_event_id", "phase", "attempt"}, "model turn attempt failure")
        _text(item["source_child_event_id"], "source child event id")
        _enum(item["phase"], ("gateway", "contract", "commit", "checkpoint"), "model turn failure phase")
        if type(item["attempt"]) is not int or item["attempt"] not in (1, 2):
            raise WorkflowContractError("model turn failure attempt is unsupported")
    elif event_type == "assistant_response":
        if not isinstance(data, dict) or set(data) not in ({"text"}, {"text", "source_llm_response_event_id"}):
            raise WorkflowContractError("assistant response has unknown or missing fields")
        item = dict(data)
        _text(item["text"], "assistant response text")
        if "source_llm_response_event_id" in item:
            _text(item["source_llm_response_event_id"], "source llm response event id")
    elif event_type == "state_transition":
        item = _exact_mapping(data, {"from_state", "to_state", "cause"}, "state transition")
        valid_states = (None, "entry_collecting", "review_active")
        if item["from_state"] not in valid_states or item["to_state"] not in valid_states:
            raise WorkflowContractError("state transition state is unsupported")
        _enum(item["cause"], ("start", "recover", "close", "switch", "resume"), "state transition cause")
    elif event_type == "reentry_requested":
        item = _exact_mapping(data, {"guidance", "unit_type"}, "re-entry request")
        _text(item["guidance"], "re-entry guidance")
        if item["unit_type"] is not None:
            _enum(item["unit_type"], ("word", "phrase", "sentence"), "re-entry unit type")
    elif event_type == "reentry_resolved":
        item = _exact_mapping(data, {"material_id"}, "re-entry resolution")
        _text(item["material_id"], "re-entry material id")
    elif event_type == "question_locked":
        item = _exact_mapping(data, {"question_sequence", "question_mode", "question_json", "item_revision"}, "question lock")
        if type(item["question_sequence"]) is not int or item["question_sequence"] <= 0:
            raise WorkflowContractError("question sequence must be positive")
        _text(item["question_mode"], "question mode")
        if not isinstance(item["question_json"], dict):
            raise WorkflowContractError("question json must be an object")
        if type(item["item_revision"]) is not int or item["item_revision"] <= 0:
            raise WorkflowContractError("item revision must be positive")
    elif event_type == "question_released":
        item = _exact_mapping(data, {"reason"}, "question release")
        _enum(item["reason"], ("review_stopped", "switch_to_entry", "assessment_completed"), "release reason")
    elif event_type == "schedule_applied":
        item = _exact_mapping(
            data,
            {"source_llm_response_event_id", "policy_id", "policy_version", "review_stage_before", "review_stage_after", "next_review_at_after", "item_revision_after"},
            "schedule applied",
        )
        _text(item["source_llm_response_event_id"], "source llm response event id")
        _text(item["policy_id"], "policy id")
        for key in ("policy_version", "review_stage_before", "review_stage_after", "item_revision_after"):
            if type(item[key]) is not int or item[key] < 0 or (key == "item_revision_after" and item[key] == 0):
                raise WorkflowContractError(f"{key} is invalid")
        if item["next_review_at_after"] is not None:
            _text(item["next_review_at_after"], "next review at")
    else:  # material_archived
        item = _exact_mapping(data, {"material_id"}, "material archived")
        _text(item["material_id"], "material id")
    return {"contract_name": EVENT_CONTRACT_NAME, "contract_version": EVENT_CONTRACT_VERSION, "data": item}


def _exact_mapping(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise WorkflowContractError(f"{label} has unknown or missing fields")
    return dict(value)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise WorkflowContractError(f"{label} must be a non-empty string")
    return value


def _enum(value: Any, values: Iterable[str], label: str) -> str:
    if value not in values:
        raise WorkflowContractError(f"{label} has an unsupported value")
    return str(value)
