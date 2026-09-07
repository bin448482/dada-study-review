"""Strict schema validation for compatible review state-machine result v1/v2."""

from __future__ import annotations

from typing import Any

from .review_turn import ReviewAssessment, ReviewStateMachineResult


class ReviewContractError(ValueError):
    """Review model output does not satisfy its fixed data contract."""


def validate_review_state_machine_result(value: Any, selected_question_mode: str | None = None) -> ReviewStateMachineResult:
    envelope = _exact(value, {"contract_name", "contract_version", "data"}, "review result envelope")
    if envelope["contract_name"] != "dada.review_state_machine_result" or envelope["contract_version"] not in (1, 2, 3, 4):
        raise ReviewContractError("review result contract is unsupported")
    data = envelope["data"]
    if not isinstance(data, dict):
        raise ReviewContractError("review result data must be an object")
    operation = data.get("next_operation")
    if operation == "ask_question":
        if envelope["contract_version"] in (3, 4):
            item = _exact(data, {"next_operation", "question_mode", "question_json"}, "ask question result")
            question_mode = _text(item["question_mode"], "question mode")
            _require_selected_mode(question_mode, selected_question_mode)
            return ReviewStateMachineResult("ask_question", question_mode=question_mode, question_json=_question_json(item["question_json"], question_mode, envelope["contract_version"]))
        item = _exact(data, {"next_operation", "question_mode", "question_json", "assistant_response"}, "ask question result")
        question_mode = _text(item["question_mode"], "question mode")
        _require_selected_mode(question_mode, selected_question_mode)
        return ReviewStateMachineResult("ask_question", _text(item["assistant_response"], "assistant response"), question_mode, _question_json(item["question_json"]))
    if operation == "continue_locked_question":
        if envelope["contract_version"] not in (3, 4):
            raise ReviewContractError("locked-question guidance requires contract version 3")
        item = _exact(data, {"next_operation", "assistant_response"}, "locked-question guidance result")
        return ReviewStateMachineResult("continue_locked_question", _text(item["assistant_response"], "assistant response"))
    if operation == "complete_assessment":
        item = _exact(data, {"next_operation", "assessment", "assistant_response"}, "assessment result")
        return ReviewStateMachineResult("complete_assessment", _text(item["assistant_response"], "assistant response"), assessment=validate_review_assessment(item["assessment"]))
    if operation == "request_transition":
        if envelope["contract_version"] not in (2, 3, 4):
            raise ReviewContractError("review transition requires contract version 2 or 3")
        item = _exact(data, {"next_operation", "requested_transition", "assistant_response"}, "transition result")
        transition = item["requested_transition"]
        if transition not in {"stop_review", "start_entry"}:
            raise ReviewContractError("review transition is unsupported")
        return ReviewStateMachineResult("request_transition", _text(item["assistant_response"], "assistant response"), requested_transition=transition)
    raise ReviewContractError("review result operation is unsupported")


def validate_review_assessment(value: Any) -> ReviewAssessment:
    envelope = _exact(value, {"contract_name", "contract_version", "data"}, "assessment envelope")
    if envelope["contract_name"] != "dada.review_assessment" or envelope["contract_version"] != 1:
        raise ReviewContractError("assessment contract is unsupported")
    item = _exact(envelope["data"], {"question_sequence", "accuracy", "explanation", "incorrect_words", "feedback_basis"}, "assessment data")
    if type(item["question_sequence"]) is not int or item["question_sequence"] <= 0:
        raise ReviewContractError("assessment question sequence is invalid")
    if type(item["accuracy"]) not in (int, float) or not 0 <= float(item["accuracy"]) <= 1:
        raise ReviewContractError("assessment accuracy must be in [0, 1]")
    words = item["incorrect_words"]
    if not isinstance(words, list):
        raise ReviewContractError("assessment incorrect words must be an array")
    normalized: list[dict[str, str]] = []
    for word in words:
        raw = _exact(word, {"word", "correction"}, "incorrect word")
        normalized.append({"word": _text(raw["word"], "incorrect word"), "correction": _text(raw["correction"], "word correction")})
    return ReviewAssessment(item["question_sequence"], float(item["accuracy"]), _text(item["explanation"], "assessment explanation"), tuple(normalized), _text(item["feedback_basis"], "feedback basis"))


def assessment_data(value: ReviewAssessment) -> dict[str, Any]:
    return {"question_sequence": value.question_sequence, "accuracy": value.accuracy, "explanation": value.explanation, "incorrect_words": [dict(word) for word in value.incorrect_words], "feedback_basis": value.feedback_basis}


def _exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ReviewContractError(f"{label} has unknown or missing fields")
    return dict(value)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReviewContractError(f"{label} must be non-empty text")
    return value


def _require_selected_mode(question_mode: str, selected_question_mode: str | None) -> None:
    if selected_question_mode is not None and question_mode != selected_question_mode:
        raise ReviewContractError("question mode differs from the program selection")


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReviewContractError(f"{label} must be an object")
    return dict(value)


def _question_json(value: Any, question_mode: str | None = None, contract_version: int = 3) -> dict[str, str]:
    item = _object(value, "question json")
    allowed = {"prompt", "instruction"} if contract_version < 4 else {"prompt", "instruction", "speech_text"}
    if set(item) - allowed or "prompt" not in item:
        raise ReviewContractError("question json has unknown or missing fields")
    prompt = _text(item["prompt"], "question prompt")
    if not prompt.strip():
        raise ReviewContractError("question prompt must not be blank")
    result = {"prompt": prompt}
    if "instruction" in item:
        instruction = _text(item["instruction"], "question instruction")
        if not instruction.strip():
            raise ReviewContractError("question instruction must not be blank")
        result["instruction"] = instruction
    speech_text = item.get("speech_text")
    requires_speech_text = question_mode in {"spelling", "sentence_recall"}
    if contract_version >= 4 and requires_speech_text:
        result["speech_text"] = _text(speech_text, "question speech text")
    elif contract_version >= 4 and question_mode is None and speech_text is not None:
        result["speech_text"] = _text(speech_text, "question speech text")
    elif speech_text is not None:
        raise ReviewContractError("question speech text is unsupported for this mode")
    return result


def render_locked_question(question_json: dict[str, Any]) -> str:
    """Return the immutable child-visible transport of an already validated LLM question."""

    question = _question_json(question_json, contract_version=4)
    instruction = question.get("instruction")
    return question["prompt"] if instruction is None else f"{question['prompt']}\n\n{instruction}"
