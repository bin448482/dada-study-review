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
        _enum(item["reason_code"], ("provider_not_returned", "provider_unsupported", "not_exposed_by_adapter", "provider_failed", "provider_http_error", "provider_transport_error", "provider_protocol_error", "contract_rejected"), "reason code")
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
        valid_states = (None, "entry_collecting", "review_active", "dialogue_active")
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
    elif event_type == "dialogue_turn_evaluated":
        item = _exact_mapping(
            data,
            {"source_child_event_id", "unit_id", "unit_version", "scenario_id", "target_id", "target_evidence", "scenario_achievement", "grammar_observations", "content_slots_covered"},
            "dialogue turn evaluation",
        )
        _text(item["source_child_event_id"], "dialogue source child event id")
        _text(item["unit_id"], "dialogue unit id")
        _text(item["scenario_id"], "dialogue scenario id")
        _text(item["target_id"], "dialogue target id")
        if type(item["unit_version"]) is not int or item["unit_version"] <= 0:
            raise WorkflowContractError("dialogue unit version is invalid")
        _enum(item["target_evidence"], ("exposed", "supported_success", "independent_success", "unable"), "dialogue target evidence")
        _enum(item["scenario_achievement"], ("none", "basic", "role_play", "reasoned_response"), "dialogue scenario achievement")
        if not isinstance(item["grammar_observations"], list):
            raise WorkflowContractError("dialogue grammar observations must be an array")
        if not isinstance(item["content_slots_covered"], list) or any(not isinstance(value, str) or not value.strip() for value in item["content_slots_covered"]):
            raise WorkflowContractError("dialogue content slot evidence must be an array")
        if len(item["content_slots_covered"]) != len(set(item["content_slots_covered"])):
            raise WorkflowContractError("dialogue content slot evidence must be unique")
        observations: list[dict[str, str]] = []
        for raw in item["grammar_observations"]:
            observation = _exact_mapping(raw, {"category", "correction"}, "dialogue grammar observation")
            _enum(observation["category"], ("subject_verb_agreement", "verb_tense", "article", "preposition", "word_order"), "dialogue grammar category")
            _text(observation["correction"], "dialogue grammar correction")
            observations.append(observation)
        item["grammar_observations"] = observations
    elif event_type == "dialogue_capture_committed":
        item = _exact_mapping(data, {"source_child_event_id", "target_id", "learning_item_id", "captured_count_incremented"}, "dialogue capture")
        _text(item["source_child_event_id"], "dialogue source child event id")
        _text(item["target_id"], "dialogue capture target id")
        _text(item["learning_item_id"], "dialogue learning item id")
        if type(item["captured_count_incremented"]) is not bool:
            raise WorkflowContractError("dialogue capture count flag is invalid")
    elif event_type == "dialogue_wrapping_started":
        item = _exact_mapping(data, {"capture_threshold", "captured_count"}, "dialogue wrapping")
        for key in ("capture_threshold", "captured_count"):
            if type(item[key]) is not int or item[key] <= 0:
                raise WorkflowContractError(f"dialogue {key} is invalid")
        if item["captured_count"] < item["capture_threshold"]:
            raise WorkflowContractError("dialogue wrapping count is below threshold")
    elif event_type == "dialogue_batch_ready":
        item = _exact_mapping(data, {"batch_id", "item_count"}, "dialogue batch ready")
        _text(item["batch_id"], "dialogue batch id")
        if type(item["item_count"]) is not int or item["item_count"] <= 0:
            raise WorkflowContractError("dialogue batch item count is invalid")
    elif event_type == "dialogue_round_planned":
        item = _exact_mapping(data, {"plan_id", "unit_id", "unit_version", "unit_content_hash", "scenario_ids", "steps"}, "dialogue round plan")
        for key in ("plan_id", "unit_id", "unit_content_hash"):
            _text(item[key], f"dialogue plan {key}")
        if type(item["unit_version"]) is not int or item["unit_version"] <= 0:
            raise WorkflowContractError("dialogue plan unit version is invalid")
        if not isinstance(item["scenario_ids"], list) or not 1 <= len(item["scenario_ids"]):
            raise WorkflowContractError("dialogue plan scenarios are invalid")
        if not isinstance(item["steps"], list) or not 1 <= len(item["steps"]) <= 12:
            raise WorkflowContractError("dialogue plan steps are invalid")
        for scenario_id in item["scenario_ids"]:
            _text(scenario_id, "dialogue plan scenario id")
        for step in item["steps"]:
            step_fields = {"step_id", "scenario_id", "target_id", "question_intent_key", "entry_difficulty", "content_slots"}
            if not isinstance(step, dict) or set(step) not in (step_fields, step_fields | {"question_intent"}):
                raise WorkflowContractError("dialogue plan step has unknown or missing fields")
            step_item = dict(step)
            for key in ("step_id", "scenario_id", "target_id", "question_intent_key"):
                _text(step_item[key], f"dialogue plan step {key}")
            if type(step_item["entry_difficulty"]) is not int or not 2 <= step_item["entry_difficulty"] <= 4:
                raise WorkflowContractError("dialogue plan entry difficulty is invalid")
            slots = step_item["content_slots"]
            if not isinstance(slots, list) or len(slots) < 2:
                raise WorkflowContractError("dialogue plan content slots are invalid")
            slot_ids: set[str] = set()
            for slot in slots:
                slot_item = _exact_mapping(slot, {"slot_id", "target_id", "purpose"}, "dialogue plan content slot")
                for key in ("slot_id", "target_id", "purpose"):
                    _text(slot_item[key], f"dialogue plan content slot {key}")
                if slot_item["slot_id"] in slot_ids:
                    raise WorkflowContractError("dialogue plan content slot ids must be unique")
                slot_ids.add(slot_item["slot_id"])
            if "question_intent" in step_item:
                intent = _exact_mapping(
                    step_item["question_intent"],
                    {"intent_key", "target_id", "scenario_ids", "purpose", "semantic_focus", "dialogue_evidence", "prompt_constraint"},
                    "dialogue plan question intent",
                )
                if intent["intent_key"] != step_item["question_intent_key"] or intent["target_id"] != step_item["target_id"]:
                    raise WorkflowContractError("dialogue plan question intent differs from step")
                for key in ("intent_key", "target_id", "purpose", "semantic_focus", "prompt_constraint"):
                    _text(intent[key], f"dialogue plan question intent {key}")
                if not isinstance(intent["scenario_ids"], list) or not intent["scenario_ids"]:
                    raise WorkflowContractError("dialogue plan question intent scenarios are invalid")
                for scenario_id in intent["scenario_ids"]:
                    _text(scenario_id, "dialogue plan question intent scenario")
                _enum(
                    intent["dialogue_evidence"],
                    ("understand_and_respond", "semantic_expression", "initiate_question", "interaction"),
                    "dialogue plan question intent evidence",
                )
    elif event_type == "dialogue_question_intent_used":
        item = _exact_mapping(data, {"source_child_event_id", "step_id", "target_id", "scenario_id", "question_intent_key", "result", "unit_id", "unit_version"}, "dialogue question intent")
        for key in ("source_child_event_id", "step_id", "target_id", "scenario_id", "question_intent_key", "unit_id"):
            _text(item[key], f"dialogue question intent {key}")
        if type(item["unit_version"]) is not int or item["unit_version"] <= 0:
            raise WorkflowContractError("dialogue question intent unit version is invalid")
        _enum(item["result"], ("completed", "retryable"), "dialogue question intent result")
    elif event_type == "dialogue_progress_checkpoint":
        item = _exact_mapping(data, {"source_child_event_id", "unit_id", "unit_version", "targets", "scenarios", "unit_course_passed"}, "dialogue progress checkpoint")
        for key in ("source_child_event_id", "unit_id"):
            _text(item[key], f"dialogue progress {key}")
        if type(item["unit_version"]) is not int or item["unit_version"] <= 0:
            raise WorkflowContractError("dialogue progress unit version is invalid")
        if not isinstance(item["targets"], list) or not isinstance(item["scenarios"], list) or type(item["unit_course_passed"]) is not bool:
            raise WorkflowContractError("dialogue progress payload is invalid")
        for target in item["targets"]:
            target_item = _exact_mapping(
                target,
                {"target_id", "status", "exposed", "supported_success", "independent_success", "unable"},
                "dialogue progress target",
            )
            _text(target_item["target_id"], "dialogue progress target id")
            _enum(target_item["status"], ("not_started", "exposed", "supported", "completed"), "dialogue progress target status")
            for key in ("exposed", "supported_success", "independent_success", "unable"):
                if type(target_item[key]) is not int or target_item[key] < 0:
                    raise WorkflowContractError(f"dialogue progress target {key} is invalid")
        for scenario in item["scenarios"]:
            scenario_item = _exact_mapping(scenario, {"scenario_id", "achievement"}, "dialogue progress scenario")
            _text(scenario_item["scenario_id"], "dialogue progress scenario id")
            _enum(scenario_item["achievement"], ("none", "basic", "role_play", "reasoned_response"), "dialogue progress scenario achievement")
    elif event_type == "dialogue_round_completed":
        item = _exact_mapping(data, {"plan_id", "completed_steps", "content_turn_count", "reason"}, "dialogue round completed")
        _text(item["plan_id"], "dialogue completed plan id")
        for key in ("completed_steps", "content_turn_count"):
            if type(item[key]) is not int or item[key] < 0:
                raise WorkflowContractError(f"dialogue completed {key} is invalid")
        _enum(item["reason"], ("completed", "closed_without_completion"), "dialogue completion reason")
    elif event_type == "dialogue_target_reopened":
        item = _exact_mapping(data, {"target_id", "reason", "actor"}, "dialogue target reopened")
        _text(item["target_id"], "dialogue reopened target id")
        _text(item["reason"], "dialogue reopened reason")
        if len(item["reason"]) > 200 or "\n" in item["reason"] or "\r" in item["reason"]:
            raise WorkflowContractError("dialogue reopened reason is invalid")
        if item["actor"] != "admin_cli":
            raise WorkflowContractError("dialogue reopened actor is unsupported")
    elif event_type == "unit_course_passed":
        item = _exact_mapping(data, {"unit_id", "unit_version"}, "unit course passed")
        _text(item["unit_id"], "unit course pass id")
        if type(item["unit_version"]) is not int or item["unit_version"] <= 0:
            raise WorkflowContractError("unit course pass version is invalid")
    else:  # material_archived
        item = _exact_mapping(data, {"material_id"}, "material archived") if set(data) == {"material_id"} else _exact_mapping(
            data, {"material_id", "archive_mode", "archive_scope", "reason", "actor"}, "admin material archived"
        )
        _text(item["material_id"], "material id")
        if len(item) > 1:
            if item["archive_mode"] != "admin":
                raise WorkflowContractError("archive mode is unsupported")
            if item["archive_scope"] not in {"learning_item", "material"}:
                raise WorkflowContractError("archive scope is unsupported")
            _text(item["reason"], "archive reason")
            if item["actor"] != "admin_cli":
                raise WorkflowContractError("archive actor is unsupported")
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
