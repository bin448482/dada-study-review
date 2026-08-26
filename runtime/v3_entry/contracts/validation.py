"""Strict validators for fixed v3 state-machine and audit contracts."""

from __future__ import annotations

from typing import Any, Iterable

from .entry_turn import EntryAudit, EntryMaterial, EntryStateMachineResult, MaterialAudit, ReentryRequest


class ContractError(ValueError):
    """A supplied structured result does not satisfy its declared v3 contract."""


def validate_entry_log_event(event_type: str, data: Any) -> dict[str, Any]:
    """Return the strict `dada.entry_log_event` v1 envelope for one event payload."""

    required: dict[str, set[str]] = {
        "system_prompt": {"text"}, "child_message": {"text"}, "assistant_response": {"text"},
        "llm_request": {"provider", "model", "request"},
        "internal_reasoning": {"text"}, "internal_reasoning_unavailable": {"reason_code"},
        "tool_call": {"tool_name", "arguments"}, "tool_result": {"tool_name", "is_error", "result"},
        "llm_response": {"task_contract_name", "task_contract_version", "output"},
        "state_transition": {"from_state", "to_state", "cause"},
        "reentry_requested": {"guidance", "unit_type"}, "reentry_resolved": {"material_id"},
    }
    if event_type not in required:
        raise ContractError("entry log event type is unsupported")
    item = _exact_mapping(data, required[event_type], f"{event_type} event data")
    if event_type in {"system_prompt", "child_message", "assistant_response", "internal_reasoning"}:
        _required_text(item["text"], f"{event_type} text")
    elif event_type == "llm_request":
        _required_text(item["provider"], "provider")
        _required_text(item["model"], "model")
        if not isinstance(item["request"], dict): raise ContractError("request must be an object")
    elif event_type == "internal_reasoning_unavailable":
        _enum(item["reason_code"], ("provider_not_returned", "provider_unsupported", "not_exposed_by_adapter"), "reason code")
    elif event_type == "tool_call":
        _required_text(item["tool_name"], "tool name")
        if not isinstance(item["arguments"], dict): raise ContractError("tool arguments must be an object")
    elif event_type == "tool_result":
        _required_text(item["tool_name"], "tool result name")
        if not isinstance(item["is_error"], bool): raise ContractError("tool result is_error must be boolean")
    elif event_type == "llm_response":
        if item["task_contract_name"] != "dada.entry_state_machine_turn" or type(item["task_contract_version"]) is not int or item["task_contract_version"] != 1:
            raise ContractError("LLM response task contract is unsupported")
    elif event_type == "state_transition":
        if item["from_state"] not in (None, "entry_collecting", "review_active") or item["to_state"] not in (None, "entry_collecting", "review_active"):
            raise ContractError("state transition state is unsupported")
        _enum(item["cause"], ("start", "recover", "close", "switch"), "state transition cause")
    elif event_type == "reentry_requested":
        _required_text(item["guidance"], "re-entry guidance")
        if item["unit_type"] is not None: _enum(item["unit_type"], ("word", "phrase", "sentence"), "re-entry unit type")
    return {"contract_name": "dada.entry_log_event", "contract_version": 1, "data": item}


def _exact_mapping(value: Any, allowed: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != allowed:
        raise ContractError(f"{label} has unknown or missing fields")
    return value


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{label} must be a non-empty string")
    return value


def _enum(value: Any, values: Iterable[str], label: str) -> str:
    if value not in values:
        raise ContractError(f"{label} has an unsupported value")
    return str(value)


def _material_audit(value: Any, needs_parent_review: bool) -> MaterialAudit:
    envelope = _exact_mapping(value, {"contract_name", "contract_version", "data"}, "material audit envelope")
    if envelope["contract_name"] != "dada.material_audit_result" or envelope["contract_version"] != 1:
        raise ContractError("material audit contract is unsupported")
    data = _exact_mapping(
        envelope["data"],
        {"audit_confidence", "needs_parent_review", "audit_changes", "semantic_duplicate_review"},
        "material audit data",
    )
    if not isinstance(data["needs_parent_review"], bool) or data["needs_parent_review"] != needs_parent_review:
        raise ContractError("material audit parent-review value does not match material")
    confidence = _enum(data["audit_confidence"], ("high", "medium", "low"), "audit confidence")
    if not isinstance(data["audit_changes"], list):
        raise ContractError("audit changes must be an array")
    changes: list[dict[str, str]] = []
    for change in data["audit_changes"]:
        item = _exact_mapping(change, {"field", "reason"}, "audit change")
        changes.append({"field": _required_text(item["field"], "audit change field"), "reason": _required_text(item["reason"], "audit change reason")})
    semantic = _exact_mapping(data["semantic_duplicate_review"], {"source", "decision", "reason"}, "semantic duplicate review")
    if semantic["source"] != "llm_semantic_review":
        raise ContractError("semantic duplicate source is unsupported")
    decision = _enum(semantic["decision"], ("no_semantic_duplicate", "merged", "kept_distinct", "needs_parent_review"), "semantic duplicate decision")
    if decision == "needs_parent_review" and not needs_parent_review:
        raise ContractError("semantic duplicate review requires parent review")
    return MaterialAudit(
        audit_confidence=confidence,
        needs_parent_review=needs_parent_review,
        audit_changes=tuple(changes),
        semantic_duplicate_review={"source": "llm_semantic_review", "decision": decision, "reason": _required_text(semantic["reason"], "semantic duplicate reason")},
    )


def _entry_audit(value: Any, result_version: int) -> EntryAudit:
    envelope = _exact_mapping(value, {"contract_name", "contract_version", "data"}, "entry audit envelope")
    if envelope["contract_name"] != "dada.entry_audit" or envelope["contract_version"] != result_version:
        raise ContractError("entry audit contract is unsupported")
    allowed = {"materials", "reentry_requests"} if result_version == 1 else {"materials", "reentry_requests", "resolved_reentry_request_ids"}
    data = _exact_mapping(envelope["data"], allowed, "entry audit data")
    if not isinstance(data["materials"], list) or not isinstance(data["reentry_requests"], list):
        raise ContractError("entry audit collections must be arrays")
    if not data["materials"] and not data["reentry_requests"]:
        raise ContractError("entry audit must contain a material or re-entry request")
    materials: list[EntryMaterial] = []
    for raw_material in data["materials"]:
        material = _exact_mapping(raw_material, {"title", "language", "unit_type", "reference_text", "needs_parent_review", "audit_result", "items"}, "entry material")
        if material["language"] != "en":
            raise ContractError("entry material language is unsupported")
        unit_type = _enum(material["unit_type"], ("word", "phrase", "sentence"), "entry material unit type")
        if not isinstance(material["needs_parent_review"], bool):
            raise ContractError("entry material needs_parent_review must be boolean")
        if not isinstance(material["items"], list) or not material["items"]:
            raise ContractError("entry material must contain learning items")
        items: list[dict[str, str | None]] = []
        for raw_item in material["items"]:
            item = _exact_mapping(raw_item, {"reference_text", "meaning_zh"}, "learning item")
            meaning = item["meaning_zh"]
            if meaning is not None:
                meaning = _required_text(meaning, "learning item meaning_zh")
            items.append({"reference_text": _required_text(item["reference_text"], "learning item reference_text"), "meaning_zh": meaning})
        needs_parent_review = material["needs_parent_review"]
        materials.append(
            EntryMaterial(
                title=_required_text(material["title"], "entry material title"),
                language="en",
                unit_type=unit_type,  # type: ignore[arg-type]
                reference_text=_required_text(material["reference_text"], "entry material reference_text"),
                needs_parent_review=needs_parent_review,
                audit_result=_material_audit(material["audit_result"], needs_parent_review),
                items=tuple(items),
            )
        )
    reentry_requests: list[ReentryRequest] = []
    for raw_request in data["reentry_requests"]:
        request = _exact_mapping(raw_request, {"guidance", "unit_type"}, "re-entry request")
        unit_type = request["unit_type"]
        if unit_type is not None:
            unit_type = _enum(unit_type, ("word", "phrase", "sentence"), "re-entry unit type")
        reentry_requests.append(ReentryRequest(guidance=_required_text(request["guidance"], "re-entry guidance"), unit_type=unit_type))
    resolved_ids: tuple[str, ...] = ()
    if result_version == 2:
        raw_ids = data["resolved_reentry_request_ids"]
        if not isinstance(raw_ids, list):
            raise ContractError("resolved re-entry request ids must be an array")
        values = tuple(_required_text(value, "resolved re-entry request id") for value in raw_ids)
        if len(values) != len(set(values)):
            raise ContractError("resolved re-entry request ids must be unique")
        if values and not materials:
            raise ContractError("re-entry resolution requires a replacement material")
        resolved_ids = values
    return EntryAudit(materials=tuple(materials), reentry_requests=tuple(reentry_requests), resolved_reentry_request_ids=resolved_ids)


def validate_entry_state_machine_result(value: Any) -> EntryStateMachineResult:
    """Validate compatible v1/v2 results without making an English decision."""

    envelope = _exact_mapping(value, {"contract_name", "contract_version", "data"}, "state machine result envelope")
    if envelope["contract_name"] != "dada.entry_state_machine_result" or envelope["contract_version"] not in (1, 2):
        raise ContractError("state machine result contract is unsupported")
    version = envelope["contract_version"]
    data = envelope["data"]
    if not isinstance(data, dict):
        raise ContractError("state machine result data must be an object")
    operation = data.get("next_operation")
    if operation == "reply_only":
        allowed = {"assistant_response", "next_operation"} if version == 1 else {"assistant_response", "next_operation", "guidance_kind", "requested_transition"}
        item = _exact_mapping(data, allowed, "reply-only result")
        if version == 1:
            return EntryStateMachineResult(assistant_response=_required_text(item["assistant_response"], "assistant response"), next_operation="reply_only")
        guidance = item["guidance_kind"]
        transition = item["requested_transition"]
        if guidance is not None and guidance != "entry_guidance":
            raise ContractError("guidance kind is unsupported")
        if transition is not None and transition not in {"finish_entry", "start_review"}:
            raise ContractError("requested transition is unsupported")
        if guidance is not None and transition is not None:
            raise ContractError("guidance and requested transition cannot both be set")
        return EntryStateMachineResult(
            assistant_response=_required_text(item["assistant_response"], "assistant response"),
            next_operation="reply_only",
            guidance_kind=guidance,
            requested_transition=transition,
        )
    if operation == "record_entry_audit":
        # A deployed v2 definition used the common reply-only null control
        # fields in this branch as well. They have no audit semantics, so
        # accept only their explicitly null form before the strict validator.
        allowed = {"assistant_response", "next_operation", "entry_audit"}
        compatibility = {"guidance_kind", "requested_transition"}
        if set(data) - allowed <= compatibility:
            unexpected = set(data) - allowed - compatibility
            if unexpected or any(data[key] is not None for key in compatibility if key in data):
                raise ContractError("audit result has unknown or non-null compatibility fields")
            data = {key: value for key, value in data.items() if key in allowed}
        item = _exact_mapping(data, allowed, "audit result")
        return EntryStateMachineResult(
            assistant_response=_required_text(item["assistant_response"], "assistant response"),
            next_operation="record_entry_audit",
            entry_audit=_entry_audit(item["entry_audit"], version),
        )
    raise ContractError("state machine next_operation is unsupported")
