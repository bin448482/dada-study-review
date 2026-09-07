"""Strict structural validator for `dada.dialogue_state_machine_result v1`."""

from __future__ import annotations

from typing import Any, Iterable

from .dialogue_turn import DialogueEvaluation, DialogueMachineTurn, DialogueStateMachineResult


class DialogueContractError(ValueError):
    """Dialogue model output is outside its fixed contract."""


def validate_dialogue_state_machine_result(value: Any, turn: DialogueMachineTurn) -> DialogueStateMachineResult:
    envelope = _exact(value, {"contract_name", "contract_version", "data"}, "dialogue result envelope")
    if envelope["contract_name"] != "dada.dialogue_state_machine_result" or envelope["contract_version"] != 1:
        raise DialogueContractError("dialogue result contract is unsupported")
    data_fields = {"assistant_response", "level_behavior", "evaluation", "repetition_outcome", "capture_candidate_target_ids", "difficulty_suggestion", "requested_transition"}
    raw_data = envelope["data"]
    if not isinstance(raw_data, dict) or set(raw_data) not in (data_fields, data_fields | {"speech_text"}):
        raise DialogueContractError("dialogue result has unknown or missing fields")
    item = dict(raw_data)
    evaluation = _evaluation(item["evaluation"])
    slot_ids = {
        str(slot.get("slot_id")) for slot in turn.active_step.get("content_slots", [])
        if isinstance(slot, dict) and isinstance(slot.get("slot_id"), str) and slot.get("slot_id")
    }
    if any(slot not in slot_ids for slot in evaluation.content_slots_covered):
        raise DialogueContractError("content slot evidence is outside the frozen step")
    if turn.difficulty_level == 4 and len(slot_ids) < 2:
        raise DialogueContractError("L4 requires at least two content slots")
    if turn.difficulty_level == 4 and evaluation.target_evidence == "independent_success" and set(evaluation.content_slots_covered) != slot_ids:
        raise DialogueContractError("L4 independent success requires all content slots")
    outcome = _enum(item["repetition_outcome"], ("not_requested", "succeeded", "not_succeeded"), "repetition outcome")
    candidate_value = item["capture_candidate_target_ids"]
    if not isinstance(candidate_value, list) or len(candidate_value) > turn.max_capture_candidates:
        raise DialogueContractError("capture candidate target ids are invalid")
    candidate = tuple(_text(value, "capture candidate target id") for value in candidate_value)
    if len(candidate) != len(set(candidate)):
        raise DialogueContractError("capture candidate target ids must be unique")
    pending = tuple(turn.pending_repetition_target_ids)
    if not pending and outcome != "not_requested":
        raise DialogueContractError("repetition outcome requires a pending target")
    if pending and outcome == "not_requested":
        raise DialogueContractError("pending repetition requires an outcome")
    if pending and outcome == "succeeded" and evaluation.target_evidence != "supported_success":
        raise DialogueContractError("successful repetition is supported success, not independent completion")
    if candidate:
        allowed = {str(target.get("target_id")): target for target in turn.unit.get("targets", []) if isinstance(target, dict)}
        if pending:
            if outcome != "succeeded" or set(candidate) != set(pending):
                raise DialogueContractError("capture candidates must match all completed repetitions")
        elif turn.difficulty_level != 4 or outcome != "not_requested" or evaluation.target_evidence not in {"unable", "supported_success"}:
            raise DialogueContractError("capture candidates without pending repetition are invalid")
        for candidate_id in candidate:
            target = allowed.get(candidate_id)
            if target is None or target.get("reviewable") is not True:
                raise DialogueContractError("capture candidate is not an eligible completed repetition")
    elif pending and outcome == "succeeded":
        raise DialogueContractError("successful repetition must provide capture candidates")
    if turn.subphase == "wrapping_up":
        if candidate or outcome != "not_requested":
            raise DialogueContractError("wrapping dialogue may not request repetition or capture")
    transition = item["requested_transition"]
    if transition not in (None, "stop_dialogue"):
        raise DialogueContractError("dialogue transition is unsupported")
    level_behavior = _enum(item["level_behavior"], ("guided_question", "role_play", "reasoned_response"), "level behavior")
    expected_behavior = (None, None, "guided_question", "role_play", "reasoned_response")[turn.difficulty_level]
    if turn.difficulty_level < 2:
        raise DialogueContractError("dialogue difficulty is below L2")
    if level_behavior != expected_behavior:
        raise DialogueContractError("level behavior differs from the program-selected difficulty")
    return DialogueStateMachineResult(
        _text(item["assistant_response"], "dialogue assistant response"), level_behavior, evaluation, outcome, candidate,
        _enum(item["difficulty_suggestion"], ("stay", "raise", "lower"), "difficulty suggestion"), transition,
        _text(item["speech_text"], "dialogue speech text") if "speech_text" in item else None,
    )


def _evaluation(value: Any) -> DialogueEvaluation:
    item = _exact(value, {"target_evidence", "scenario_achievement", "grammar_observations", "content_slots_covered"}, "dialogue evaluation")
    observations = item["grammar_observations"]
    if not isinstance(observations, list):
        raise DialogueContractError("grammar observations must be an array")
    normalized: list[dict[str, str]] = []
    for raw in observations:
        observation = _exact(raw, {"category", "correction"}, "grammar observation")
        normalized.append({
            "category": _enum(observation["category"], ("subject_verb_agreement", "verb_tense", "article", "preposition", "word_order"), "grammar category"),
            "correction": _text(observation["correction"], "grammar correction"),
        })
    covered = item["content_slots_covered"]
    if not isinstance(covered, list) or any(not isinstance(value, str) or not value.strip() for value in covered) or len(covered) != len(set(covered)):
        raise DialogueContractError("content slots covered must be a unique string array")
    return DialogueEvaluation(
        _enum(item["target_evidence"], ("exposed", "supported_success", "independent_success", "unable"), "target evidence"),
        _enum(item["scenario_achievement"], ("none", "basic", "role_play", "reasoned_response"), "scenario achievement"),
        tuple(normalized), tuple(str(value) for value in covered),
    )


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise DialogueContractError(f"{label} has unknown or missing fields")
    return dict(value)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DialogueContractError(f"{label} must be non-empty text")
    return value


def _enum(value: Any, values: Iterable[str], label: str) -> str:
    if value not in values:
        raise DialogueContractError(f"{label} is unsupported")
    return str(value)
