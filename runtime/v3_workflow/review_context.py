"""Strict, credential-free review context metadata for phrase learning items."""

from __future__ import annotations

from typing import Any
import re


class ReviewContextError(ValueError):
    """A phrase review context is malformed or outside the fixed boundary."""


_CONTEXT_KIND = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_SLOT_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_MAX_TEXT = 1000
_MAX_EXAMPLES = 12


def validate_review_context(value: Any) -> dict[str, Any]:
    """Validate and return one frozen phrase review-design object.

    This validator checks structure and bounded metadata only. It never judges
    whether an English expression is semantically correct.
    """

    if not isinstance(value, dict):
        raise ReviewContextError("review context must be an object")
    fields = {
        "schema_version", "purpose_zh", "context_kind", "information_slots",
        "prompt_constraint_zh", "accepted_expressions", "semantic_alternatives",
    }
    if set(value) != fields:
        raise ReviewContextError("review context has unknown or missing fields")
    if value["schema_version"] != 1:
        raise ReviewContextError("review context schema version is unsupported")
    purpose = _text(value["purpose_zh"], "review context purpose")
    context_kind = _text(value["context_kind"], "review context kind")
    if not _CONTEXT_KIND.fullmatch(context_kind):
        raise ReviewContextError("review context kind is invalid")
    slots = _identifiers(value["information_slots"], "review context information slots", _SLOT_ID)
    prompt_constraint = _text(value["prompt_constraint_zh"], "review context prompt constraint")
    accepted = _expressions(value["accepted_expressions"], "accepted expressions", required=True, semantic=False)
    alternatives = _expressions(value["semantic_alternatives"], "semantic alternatives", required=False, semantic=True)
    if len(accepted) > _MAX_EXAMPLES or len(alternatives) > _MAX_EXAMPLES:
        raise ReviewContextError("review context contains too many examples")
    return {
        "schema_version": 1,
        "purpose_zh": purpose,
        "context_kind": context_kind,
        "information_slots": slots,
        "prompt_constraint_zh": prompt_constraint,
        "accepted_expressions": accepted,
        "semantic_alternatives": alternatives,
    }


def _expressions(value: Any, label: str, *, required: bool, semantic: bool) -> list[dict[str, str]]:
    if not isinstance(value, list) or (required and not value):
        raise ReviewContextError(f"{label} must be a non-empty array" if required else f"{label} must be an array")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    expected = {"text", "note_zh", "kind"} if not semantic else {"text", "note_zh", "use"}
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != expected:
            raise ReviewContextError(f"{label} entry has unknown or missing fields")
        text = _text(raw["text"], f"{label} text")
        normalized = " ".join(text.casefold().split())
        if normalized in seen:
            raise ReviewContextError(f"{label} entries must be unique")
        seen.add(normalized)
        note = _text(raw["note_zh"], f"{label} note")
        if semantic:
            use = _text(raw["use"], f"{label} use")
            if use not in {"feedback_only"}:
                raise ReviewContextError("semantic alternative use is unsupported")
            result.append({"text": text, "use": use, "note_zh": note})
        else:
            kind = _text(raw["kind"], f"{label} kind")
            if kind != "target_form":
                raise ReviewContextError("accepted expression kind is unsupported")
            result.append({"kind": kind, "text": text, "note_zh": note})
    return result


def _identifiers(value: Any, label: str, pattern: re.Pattern[str]) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ReviewContextError(f"{label} must be a non-empty array")
    result = [_text(item, label) for item in value]
    if len(result) != len(set(result)) or any(not pattern.fullmatch(item) for item in result):
        raise ReviewContextError(f"{label} are invalid")
    return result


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
        raise ReviewContextError(f"{label} must be bounded non-empty text")
    return value
