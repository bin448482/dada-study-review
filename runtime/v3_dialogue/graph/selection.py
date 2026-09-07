"""Program-owned Dialogue round planning, target and difficulty selection."""

from __future__ import annotations

from ..unit_definition import UnitDefinition
from uuid import uuid4


def initial_focus(unit: UnitDefinition) -> tuple[str, str]:
    scenario = next(item for item in unit.scenarios if item["required"])
    return str(scenario["scenario_id"]), str(scenario["allowed_target_ids"][0])


def build_round_plan(
    unit: UnitDefinition,
    snapshot: dict[str, object],
    retryable_targets: set[str] | None = None,
    used_question_intents: set[str] | None = None,
    retryable_question_intents: set[str] | None = None,
    *,
    max_steps: int = 12,
) -> dict[str, object]:
    """Choose a bounded program-owned plan from persisted Unit facts.

    A target with any independent success is complete and is never scheduled
    again. Retryable facts only carry unfinished targets across rounds. The
    Unit loader supplies one or more stable intent keys; the first eligible
    key is used for the current bounded implementation.
    """

    if type(max_steps) is not int or max_steps <= 0:
        raise ValueError("round plan bounds are invalid")
    retryable = set(retryable_targets or set())
    used_intents = set(used_question_intents or set())
    retryable_intents = set(retryable_question_intents or set())
    progress = dict(snapshot.get("target_progress", {}))
    reopened_targets = {str(target_id) for target_id in snapshot.get("reopened_target_ids", [])}
    intents_by_target: dict[str, list[dict[str, object]]] = {}
    for intent in unit.question_intents:
        intents_by_target.setdefault(str(intent["target_id"]), []).append(dict(intent))
    ranked: list[tuple[int, int, dict[str, object]]] = []
    for index, target in enumerate(unit.targets):
        target_id = str(target["target_id"])
        facts = dict(progress.get(target_id, {}))
        independent = int(facts.get("independent_success", 0))
        supported = int(facts.get("supported_success", 0))
        exposed = int(facts.get("exposed", 0))
        # Target completion is stronger than an older retryable/intent fact:
        # once the child has independently completed a target, no later round
        # should schedule it again, regardless of question-intent history.
        if independent > 0:
            continue
        if target_id in retryable:
            rank = 0
        elif supported > 0 or exposed > 0:
            rank = 1
        else:
            rank = 0
        target_intents = intents_by_target.get(target_id, [])
        has_retryable = target_id in retryable or any(str(intent["intent_key"]) in retryable_intents for intent in target_intents)
        has_unused_intent = any(str(intent["intent_key"]) not in used_intents for intent in target_intents)
        if rank == 3 and not has_retryable and not has_unused_intent:
            continue
        if not has_retryable and not has_unused_intent and target_id not in reopened_targets:
            continue
        ranked.append((rank, index, target))
    ranked.sort(key=lambda item: (item[0], item[1]))
    selected = [item[2] for item in ranked[:max_steps]]
    scenario_ids: list[str] = []
    steps: list[dict[str, object]] = []
    for target in selected:
        target_id = str(target["target_id"])
        candidates = [str(value) for value in target.get("scenario_ids", [])]
        scenario_id = next((value for value in candidates if value in scenario_ids), None)
        if scenario_id is None:
            scenario_id = next((value for value in candidates if value not in scenario_ids), None)
        if scenario_id is None:
            continue
        if scenario_id not in scenario_ids:
            scenario_ids.append(scenario_id)
        facts = dict(progress.get(target_id, {}))
        # Every Dialogue round starts at L2. Higher levels are introduced by
        # the program after successful target boundaries or explicit task
        # requirements; L0/L1 are no longer runtime difficulty levels.
        entry_difficulty = 2
        intent = next((item for item in intents_by_target.get(target_id, []) if str(item["intent_key"]) not in used_intents), None)
        if intent is None and target_id in reopened_targets:
            intent = intents_by_target.get(target_id, [None])[0]
        target_has_retryable = target_id in retryable or any(
            str(item["intent_key"]) in retryable_intents
            for item in intents_by_target.get(target_id, [])
        )
        if target_has_retryable:
            intent = next((item for item in intents_by_target.get(target_id, []) if str(item["intent_key"]) in retryable_intents), None)
            intent = intent or intents_by_target.get(target_id, [None])[0]
        if intent is None:
            continue
        step = {
            "step_id": f"step-{len(steps) + 1}",
            "scenario_id": scenario_id,
            "target_id": target_id,
            "question_intent_key": str(intent["intent_key"]),
            "entry_difficulty": entry_difficulty,
            "content_slots": _content_slots(scenario_id, target_id),
        }
        if unit.definition_schema_version == 3:
            frozen_intent = dict(intent)
            frozen_intent["scenario_ids"] = list(intent["scenario_ids"])
            step["question_intent"] = frozen_intent
        steps.append(step)
    if not steps:
        raise ValueError("Unit has no eligible round steps")
    return {"plan_id": str(uuid4()), "unit_id": unit.unit_id, "unit_version": unit.version,
            "unit_content_hash": unit.content_hash, "scenario_ids": scenario_ids, "steps": steps}


def _content_slots(scenario_id: str, target_id: str) -> list[dict[str, str]]:
    """Return deterministic L4 information slots for a planned target."""

    presets = {
        "school-introduction": (("target_expression", target_id), ("school_detail", "school_detail")),
        "club-enquiry": (("target_expression", target_id), ("club_detail", "club_detail")),
        "school-day": (("target_expression", target_id), ("school_day_detail", "school_day_detail")),
        "school-safety-etiquette": (("target_expression", target_id), ("rule_reason_or_action", "rule_reason_or_action")),
        "dream-school": (("target_expression", target_id), ("choice_reason", "choice_reason")),
        "compare-schools": (("similarity_or_difference", target_id), ("contrast_or_similarity", "contrast_or_similarity")),
    }
    return [{"slot_id": slot_id, "target_id": slot_target, "purpose": slot_id} for slot_id, slot_target in presets.get(
        scenario_id, (("target_expression", target_id), ("supporting_detail", "supporting_detail"))
    )]


def select_next_focus(
    unit: UnitDefinition, current_scenario_id: str, current_target_id: str, current_difficulty: int,
    evidence: str, suggestion: str, scenario_achievement: str = "none",
    scenario_progress: dict[str, object] | None = None,
) -> tuple[str, str, int]:
    """Advance target lists, and cross a scenario boundary only when its basic task is met."""

    if scenario_achievement not in {"none", "basic", "role_play", "reasoned_response"}:
        raise ValueError("scenario achievement is invalid")

    difficulty = max(2, min(4, current_difficulty + ({"raise": 1, "lower": -1, "stay": 0}[suggestion])))
    scenario_ids = [str(item["scenario_id"]) for item in unit.scenarios]
    scenario_index = scenario_ids.index(current_scenario_id)
    scenario = unit.scenarios_by_id[current_scenario_id]
    target_ids = [str(item) for item in scenario["allowed_target_ids"]]
    target_index = target_ids.index(current_target_id)
    if evidence != "independent_success":
        return current_target_id, current_scenario_id, difficulty
    if target_index + 1 < len(target_ids):
        return target_ids[target_index + 1], current_scenario_id, difficulty
    ranks = {"none": 0, "basic": 1, "role_play": 2, "reasoned_response": 2}
    prior = dict(scenario_progress or {}).get(current_scenario_id, "none")
    if ranks.get(str(prior), 0) < 1 and ranks[scenario_achievement] < 1:
        return current_target_id, current_scenario_id, difficulty
    if scenario_index + 1 >= len(scenario_ids):
        return current_target_id, current_scenario_id, difficulty
    next_scenario = unit.scenarios_by_id[scenario_ids[scenario_index + 1]]
    return str(next_scenario["allowed_target_ids"][0]), str(next_scenario["scenario_id"]), difficulty


def course_passes(
    unit: UnitDefinition, snapshot: dict[str, object], scenario_id: str, target_id: str,
    evidence: str, achievement: str,
) -> bool:
    """Apply only the Unit's configured completion rule to committed facts plus one turn."""

    if snapshot.get("unit_course_passed") is True:
        return False
    target_progress = {key: dict(value) for key, value in dict(snapshot.get("target_progress", {})).items()}
    target = target_progress.setdefault(target_id, {"exposed": 0, "supported_success": 0, "independent_success": 0, "unable": 0})
    target[evidence] = int(target.get(evidence, 0)) + 1
    scenarios = dict(snapshot.get("scenario_progress", {}))
    rank = {"none": 0, "basic": 1, "role_play": 2, "reasoned_response": 2}
    if rank[achievement] >= rank.get(str(scenarios.get(scenario_id, "none")), 0):
        scenarios[scenario_id] = achievement
    core = [item for item in unit.targets if item["core"]]
    if not core or unit.completion["all_core_targets_covered"] is not True:
        return False
    if any(int(target_progress.get(str(item["target_id"]), {}).get("exposed", 0)) <= 0 for item in core):
        return False
    independent = sum(1 for item in core if int(target_progress.get(str(item["target_id"]), {}).get("independent_success", 0)) >= 2)
    if independent / len(core) < float(unit.completion["minimum_independent_ratio"]):
        return False
    required = list(unit.completion["required_scenarios"])
    if any(rank.get(str(scenarios.get(item, "none")), 0) < 1 for item in required):
        return False
    advanced = sum(1 for item in required if rank.get(str(scenarios.get(item, "none")), 0) >= 2)
    return advanced >= int(unit.completion["minimum_advanced_scenarios"])
