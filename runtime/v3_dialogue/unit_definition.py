"""Strict loader for minimal, versioned Dialogue Unit definitions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from v3_workflow.review_context import ReviewContextError, validate_review_context


class UnitDefinitionError(ValueError):
    """A Unit package is malformed or is outside the fixed Dialogue boundary."""


_V1_BASE_FIELDS = {"unit_id", "version", "title", "source_pages", "targets", "scenarios", "completion"}
_V3_BASE_FIELDS = _V1_BASE_FIELDS | {"definition_schema_version", "question_intents"}
_DIALOGUE_EVIDENCE = {
    "understand_and_respond",
    "semantic_expression",
    "initiate_question",
    "interaction",
}
_OUTPUT_REQUIREMENTS = {
    "understand_and_respond": {"respond", "describe"},
    "semantic_expression": {"respond", "describe"},
    "initiate_question": {"ask"},
    "interaction": {"interact"},
}


@dataclass(frozen=True)
class DialoguePolicy:
    policy_id: str
    policy_version: int
    minimum_difficulty: int
    maximum_difficulty: int
    max_round_content_turns: int
    max_candidate_per_turn: int
    history_limit: int
    max_closing_grammar_patterns: int


@dataclass(frozen=True)
class UnitDefinition:
    unit_id: str
    version: int
    title: str
    source_pages: tuple[int, ...]
    targets: tuple[dict[str, Any], ...]
    scenarios: tuple[dict[str, Any], ...]
    completion: dict[str, Any]
    content_hash: str
    question_intents: tuple[dict[str, Any], ...] = ()
    definition_schema_version: int = 1

    @property
    def targets_by_id(self) -> dict[str, dict[str, Any]]:
        return {str(target["target_id"]): dict(target) for target in self.targets}

    @property
    def scenarios_by_id(self) -> dict[str, dict[str, Any]]:
        return {str(scenario["scenario_id"]): dict(scenario) for scenario in self.scenarios}


def load_dialogue_policy(path: Path) -> DialoguePolicy:
    raw = _load_json(path, "dialogue policy")
    _exact(raw, {"policy_id", "policy_version", "difficulty", "maxRoundContentTurns", "maxCandidatePerTurn", "historyLimit", "maxClosingGrammarPatterns"}, "dialogue policy")
    difficulty = raw["difficulty"]
    _exact(difficulty, {"minimum", "maximum"}, "dialogue difficulty")
    policy = DialoguePolicy(
        _text(raw["policy_id"], "policy id"),
        _positive_int(raw["policy_version"], "policy version"),
        _nonnegative_int(difficulty["minimum"], "minimum difficulty"),
        _nonnegative_int(difficulty["maximum"], "maximum difficulty"),
        _positive_int(raw["maxRoundContentTurns"], "maximum round content turns"),
        _positive_int(raw["maxCandidatePerTurn"], "maximum candidates"),
        _positive_int(raw["historyLimit"], "history limit"),
        _positive_int(raw["maxClosingGrammarPatterns"], "maximum closing grammar patterns"),
    )
    if policy.minimum_difficulty != 2 or policy.maximum_difficulty != 4:
        raise UnitDefinitionError("dialogue difficulty range is invalid")
    if policy.max_candidate_per_turn < 1 or policy.max_round_content_turns != 12:
        raise UnitDefinitionError("Dialogue round policy has unsupported fixed bounds")
    return policy


def load_unit_definition(path: Path, expected_unit_id: str | None = None) -> UnitDefinition:
    raw = _load_json(path, "unit definition")
    schema_version = _definition_schema_version(raw)
    if schema_version == 1 and set(raw) not in (_V1_BASE_FIELDS, _V1_BASE_FIELDS | {"question_intents"}):
        raise UnitDefinitionError("unit definition has unknown or missing fields")
    if schema_version == 3 and set(raw) != _V3_BASE_FIELDS:
        raise UnitDefinitionError(f"v{schema_version} unit definition has unknown or missing fields")
    unit_id = _text(raw["unit_id"], "unit id")
    if expected_unit_id is not None and unit_id != expected_unit_id:
        raise UnitDefinitionError("unit id differs from the fixed selection")
    source_pages = _pages(raw["source_pages"], "unit source pages")
    targets = _targets(raw["targets"], source_pages, schema_version, _positive_int(raw["version"], "unit version"))
    scenarios = _scenarios(raw["scenarios"], {str(target["target_id"]) for target in targets})
    _validate_target_scenarios(targets, scenarios)
    target_map = {str(target["target_id"]): target for target in targets}
    scenario_map = {str(scenario["scenario_id"]): scenario for scenario in scenarios}
    question_intents = _question_intents(raw["question_intents"], target_map, scenario_map, schema_version) if "question_intents" in raw else _default_question_intents(targets)
    completion = _completion(raw["completion"], targets, scenarios)
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return UnitDefinition(
        unit_id,
        _positive_int(raw["version"], "unit version"),
        _text(raw["title"], "unit title"),
        source_pages,
        tuple(targets),
        tuple(scenarios),
        completion,
        hashlib.sha256(canonical).hexdigest(),
        tuple(question_intents),
        schema_version,
    )


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UnitDefinitionError(f"{label} is not readable JSON") from exc
    if not isinstance(value, dict):
        raise UnitDefinitionError(f"{label} must be an object")
    return value


def _definition_schema_version(raw: dict[str, Any]) -> int:
    if "definition_schema_version" not in raw:
        return 1
    if raw["definition_schema_version"] != 3:
        raise UnitDefinitionError("unit definition schema version is unsupported")
    return int(raw["definition_schema_version"])


def _targets(value: Any, source_pages: tuple[int, ...], schema_version: int, unit_version: int) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise UnitDefinitionError("targets must be a non-empty array")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_english: set[str] = set()
    for raw in value:
        v1_fields = {"target_id", "target_type", "core", "english", "meaning_zh", "source_pages", "scenario_ids", "reviewable"}
        v3_fields = v1_fields | {"source_refs", "semantic_focus", "dialogue_evidence", "child_output_requirement", "review_design"}
        legacy_v3_fields = v3_fields - {"review_design"}
        if schema_version == 3:
            if set(raw) not in (legacy_v3_fields, v3_fields):
                raise UnitDefinitionError("target has unknown or missing fields")
        else:
            _exact(raw, v1_fields, "target")
        target_id = _text(raw["target_id"], "target id")
        if target_id in seen:
            raise UnitDefinitionError("target ids must be unique")
        seen.add(target_id)
        target_type = raw["target_type"]
        if target_type not in {"word", "phrase", "sentence", "function"}:
            raise UnitDefinitionError("target type is unsupported")
        if type(raw["core"]) is not bool or type(raw["reviewable"]) is not bool:
            raise UnitDefinitionError("target boolean fields are invalid")
        if raw["reviewable"] != (target_type in {"word", "phrase", "sentence"}):
            raise UnitDefinitionError("target reviewable value is inconsistent with target type")
        pages = _pages(raw["source_pages"], "target source pages")
        if not set(pages).issubset(source_pages):
            raise UnitDefinitionError("target references a page outside the unit")
        scenarios = _identifiers(raw["scenario_ids"], "target scenario ids")
        normalized = " ".join(_text(raw["english"], "target english").casefold().split())
        if normalized in seen_english:
            raise UnitDefinitionError("target English texts must be unique")
        seen_english.add(normalized)
        item = {**raw, "source_pages": list(pages), "scenario_ids": list(scenarios)}
        if schema_version == 3:
            evidence = _enum(raw["dialogue_evidence"], _DIALOGUE_EVIDENCE, "target dialogue evidence")
            requirement = _enum(raw["child_output_requirement"], _OUTPUT_REQUIREMENTS[evidence], "target child output requirement")
            item.update({
                "source_refs": _source_refs(raw["source_refs"], pages),
                "semantic_focus": _text(raw["semantic_focus"], "target semantic focus"),
                "dialogue_evidence": evidence,
                "child_output_requirement": requirement,
            })
            if "review_design" in raw:
                if target_type != "phrase":
                    raise UnitDefinitionError("review design is only valid for phrase targets")
                try:
                    item["review_design"] = validate_review_context(raw["review_design"])
                except ReviewContextError as exc:
                    raise UnitDefinitionError(str(exc)) from exc
            elif target_type == "phrase" and unit_version >= 6:
                raise UnitDefinitionError("new phrase targets require review design")
        result.append(item)
    return result


def _source_refs(value: Any, source_pages: tuple[int, ...]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise UnitDefinitionError("target source refs must be a non-empty array")
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for raw in value:
        fields = {"page", "raw_heading", "source_locator"}
        item = _exact(raw, fields, "target source ref")
        page = _positive_int(item["page"], "target source ref page")
        if page not in source_pages:
            raise UnitDefinitionError("target source ref page is outside target pages")
        normalized = (
            str(page),
            _text(item["raw_heading"], "target source raw heading"),
            _bounded_text(item["source_locator"], "target source locator", 240),
        )
        if normalized in seen:
            raise UnitDefinitionError("target source refs must be unique")
        seen.add(normalized)
        result.append({
            "page": page,
            "raw_heading": normalized[1],
            "source_locator": normalized[2],
        })
    return result


def _question_intents(
    value: Any,
    targets: dict[str, dict[str, Any]],
    scenarios: dict[str, dict[str, Any]],
    schema_version: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise UnitDefinitionError("question intents must be a non-empty array")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        v1_fields = {"intent_key", "target_id", "scenario_ids", "purpose"}
        v3_fields = v1_fields | {"semantic_focus", "dialogue_evidence", "prompt_constraint"}
        _exact(raw, v3_fields if schema_version == 3 else v1_fields, "question intent")
        key = _text(raw["intent_key"], "question intent key")
        if key in seen:
            raise UnitDefinitionError("question intent keys must be unique")
        seen.add(key)
        target_id = _text(raw["target_id"], "question intent target id")
        if target_id not in targets:
            raise UnitDefinitionError("question intent references an unknown target")
        ids = _identifiers(raw["scenario_ids"], "question intent scenarios")
        if not ids or not set(ids).issubset(scenarios) or not set(ids).issubset(set(targets[target_id].get("scenario_ids", []))):
            raise UnitDefinitionError("question intent scenario reference is invalid")
        item = {"intent_key": key, "target_id": target_id, "scenario_ids": ids, "purpose": _text(raw["purpose"], "question intent purpose")}
        if schema_version == 3:
            evidence = _enum(raw["dialogue_evidence"], _DIALOGUE_EVIDENCE, "question intent dialogue evidence")
            if evidence != targets[target_id]["dialogue_evidence"]:
                raise UnitDefinitionError("question intent dialogue evidence differs from target")
            item.update({
                "semantic_focus": _text(raw["semantic_focus"], "question intent semantic focus"),
                "dialogue_evidence": evidence,
                "prompt_constraint": _text(raw["prompt_constraint"], "question intent prompt constraint"),
            })
        result.append(item)
    if {str(item["target_id"]) for item in result} != set(targets):
        raise UnitDefinitionError("every target must have a question intent")
    return result


def _default_question_intents(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compatibility intents for the active Unit v1 package."""

    return [{
        "intent_key": f"{target['target_id']}.primary",
        "target_id": str(target["target_id"]),
        "scenario_ids": list(target["scenario_ids"]),
        "purpose": f"引导孩子谈论 {target['english']}",
    } for target in targets]


def _scenarios(value: Any, target_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise UnitDefinitionError("scenarios must be a non-empty array")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        _exact(raw, {"scenario_id", "required", "goal", "allowed_target_ids", "basic_task", "advanced_modes"}, "scenario")
        scenario_id = _text(raw["scenario_id"], "scenario id")
        if scenario_id in seen:
            raise UnitDefinitionError("scenario ids must be unique")
        seen.add(scenario_id)
        if type(raw["required"]) is not bool:
            raise UnitDefinitionError("scenario required must be boolean")
        allowed = _identifiers(raw["allowed_target_ids"], "scenario target ids")
        if not set(allowed).issubset(target_ids):
            raise UnitDefinitionError("scenario references an unknown target")
        modes = _identifiers(raw["advanced_modes"], "scenario advanced modes")
        if set(modes) != {"role_play", "reasoned_response"}:
            raise UnitDefinitionError("scenario advanced modes are invalid")
        result.append({**raw, "allowed_target_ids": list(allowed), "advanced_modes": list(modes)})
    return result


def _validate_target_scenarios(targets: list[dict[str, Any]], scenarios: list[dict[str, Any]]) -> None:
    scenario_ids = {str(scenario["scenario_id"]) for scenario in scenarios}
    allowed_by_scenario = {str(scenario["scenario_id"]): set(scenario["allowed_target_ids"]) for scenario in scenarios}
    for target in targets:
        ids = set(target["scenario_ids"])
        if not ids or not ids.issubset(scenario_ids):
            raise UnitDefinitionError("target references an unknown scenario")
        if any(str(target["target_id"]) not in allowed_by_scenario[scenario_id] for scenario_id in ids):
            raise UnitDefinitionError("target/scenario relation is not symmetric")


def _completion(value: Any, targets: list[dict[str, Any]], scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    _exact(value, {"all_core_targets_covered", "minimum_independent_ratio", "required_scenarios", "minimum_advanced_scenarios"}, "completion")
    if value["all_core_targets_covered"] is not True:
        raise UnitDefinitionError("completion must require all core targets")
    ratio = value["minimum_independent_ratio"]
    if type(ratio) not in (int, float) or not 0 < float(ratio) <= 1:
        raise UnitDefinitionError("independent ratio is invalid")
    required = _identifiers(value["required_scenarios"], "completion required scenarios")
    scenario_map = {str(scenario["scenario_id"]): scenario for scenario in scenarios}
    if not set(required).issubset(scenario_map) or any(not scenario_map[item]["required"] for item in required):
        raise UnitDefinitionError("completion required scenarios are invalid")
    configured_required = {str(scenario["scenario_id"]) for scenario in scenarios if scenario["required"]}
    if set(required) != configured_required:
        raise UnitDefinitionError("completion must name every required scenario")
    advanced = _positive_int(value["minimum_advanced_scenarios"], "minimum advanced scenarios")
    if advanced > len(required):
        raise UnitDefinitionError("minimum advanced scenarios exceeds required scenarios")
    if not any(target["core"] for target in targets):
        raise UnitDefinitionError("unit must contain a core target")
    return {"all_core_targets_covered": True, "minimum_independent_ratio": float(ratio), "required_scenarios": list(required), "minimum_advanced_scenarios": advanced}


def _pages(value: Any, label: str) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise UnitDefinitionError(f"{label} must be a non-empty array")
    result = tuple(_positive_int(item, label) for item in value)
    if len(result) != len(set(result)):
        raise UnitDefinitionError(f"{label} must be unique")
    return result


def _identifiers(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise UnitDefinitionError(f"{label} must be a non-empty array")
    result = tuple(_text(item, label) for item in value)
    if len(result) != len(set(result)):
        raise UnitDefinitionError(f"{label} must be unique")
    return result


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise UnitDefinitionError(f"{label} has unknown or missing fields")
    return dict(value)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UnitDefinitionError(f"{label} must be non-empty text")
    return value


def _bounded_text(value: Any, label: str, maximum: int) -> str:
    text = _text(value, label)
    if len(text) > maximum:
        raise UnitDefinitionError(f"{label} exceeds maximum length")
    return text


def _enum(value: Any, values: set[str], label: str) -> str:
    if value not in values:
        raise UnitDefinitionError(f"{label} is unsupported")
    return str(value)


def _positive_int(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise UnitDefinitionError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise UnitDefinitionError(f"{label} must be a non-negative integer")
    return value
