"""Dialogue-specific validation and atomic repository commit boundary."""

from __future__ import annotations

import json
from typing import Any

from v3_workflow.model_turn.contracts import FailureKind, ModelTurnCommit
from v3_workflow.model_turn.pipeline import ModelTurnContractError
from v3_workflow.persistence.repository import RepositoryError, WorkflowRepository

from ..contracts.validation import DialogueContractError, validate_dialogue_state_machine_result
from ..gateway.port import DialogueModelGateway
from ..presentation import active_dialogue_failure_text, compose_dialogue_presentation
from ..unit_definition import DialoguePolicy, UnitDefinition
from .selection import course_passes


class DialogueModelTurnAdapter:
    def __init__(
        self, repository: WorkflowRepository, gateway: DialogueModelGateway, workflow_id: str, created_at: str,
        context: Any, state: dict[str, Any], turn: Any,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._workflow_id = workflow_id
        self._created_at = created_at
        self._context = context
        self._state = state
        self._turn = turn

    def prepare(self, turn: Any) -> Any:
        return self._gateway.prepare(turn)

    def execute(self, prepared: Any) -> Any:
        return self._gateway.execute(prepared)

    def append_log_event(self, event_type: str, payload: dict[str, Any], created_at: str) -> str:
        return self._repository.append_log_event(self._workflow_id, event_type, payload, created_at, require_active_type="dialogue")

    def validate(self, output: Any, turn: Any) -> Any:
        try:
            return validate_dialogue_state_machine_result(output, turn)
        except DialogueContractError as exc:
            raise ModelTurnContractError(str(exc)) from exc

    def commit(self, result: Any, source_llm_response_event_id: str) -> ModelTurnCommit:
        unit: UnitDefinition = self._context.unit
        policy: DialoguePolicy = self._context.policy
        focus = self._turn.focus_target
        scenario = self._turn.scenario
        plan = self._state.get("round_plan_json") or "{}"
        try:
            plan_data = json.loads(plan) if isinstance(plan, str) else dict(plan)
        except (TypeError, ValueError):
            plan_data = {}
        steps = list(plan_data.get("steps", []))
        step_index = int(self._state.get("current_step_index", 0))
        current_step = steps[step_index] if 0 <= step_index < len(steps) else None
        independent = result.evaluation.target_evidence == "independent_success"
        repetition_completed = bool(self._turn.pending_repetition_target_ids and result.repetition_outcome == "succeeded")
        # A successful repetition finishes this round's practice step, but it
        # is deliberately not independent mastery.  Its target/intent stays
        # retryable for a later round; do not ask the same question again in
        # the current round merely to establish independence.
        step_completed = independent or repetition_completed
        next_step_index = step_index + 1 if step_completed and current_step is not None else step_index
        auto_complete = bool(current_step is not None and step_completed and next_step_index >= len(steps))
        next_content_turn_count = int(self._state.get("content_turn_count", 0)) + (0 if self._turn.pending_repetition_target_ids else 1)
        auto_limit = bool(current_step is not None and next_content_turn_count >= self._context.policy.max_round_content_turns and not auto_complete)
        if current_step is not None and step_completed and not auto_complete and next_step_index < len(steps):
            next_step = steps[next_step_index]
            target_id, scenario_id = str(next_step["target_id"]), str(next_step["scenario_id"])
            planned_difficulty = int(next_step.get("entry_difficulty", self._turn.difficulty_level))
            # Only independent mastery raises the next step's interaction
            # level.  A correct repetition moves the current round forward
            # at the next step's planned starting level, while its target
            # remains available as retryable carry-over next round.
            difficulty = (
                max(planned_difficulty, min(4, self._turn.difficulty_level + 1))
                if independent else planned_difficulty
            )
        else:
            target_id, scenario_id = focus["target_id"], scenario["scenario_id"]
            suggestion = result.difficulty_suggestion
            if result.evaluation.target_evidence == "unable" and suggestion == "raise":
                suggestion = "stay"
            difficulty = max(2, min(4, self._turn.difficulty_level + {"raise": 1, "lower": -1, "stay": 0}[suggestion]))
        pending_ids = self._pending_repetition(result, focus)
        pending = self._encode_pending_ids(pending_ids)
        capture_targets = []
        capture_ids = result.capture_candidate_target_ids if self._turn.pending_repetition_target_ids and result.repetition_outcome == "succeeded" else ()
        for candidate_id in capture_ids:
            raw = unit.targets_by_id[candidate_id]
            capture_target = {
                "target_id": raw["target_id"], "target_type": raw["target_type"], "english": raw["english"],
                "meaning_zh": raw["meaning_zh"], "reviewable": raw["reviewable"],
            }
            if raw["target_type"] == "phrase" and raw.get("review_design") is not None:
                capture_target["review_design"] = raw.get("review_design")
            capture_targets.append(capture_target)
        evaluation = {
            "source_child_event_id": self._turn.current_child_message["event_id"],
            "unit_id": unit.unit_id, "unit_version": unit.version,
            "scenario_id": scenario["scenario_id"], "target_id": focus["target_id"],
            "target_evidence": result.evaluation.target_evidence,
            "scenario_achievement": result.evaluation.scenario_achievement,
            "grammar_observations": [dict(item) for item in result.evaluation.grammar_observations],
            "content_slots_covered": list(result.evaluation.content_slots_covered),
        }
        question_intent = {
            "source_child_event_id": self._turn.current_child_message["event_id"],
            "step_id": str(current_step.get("step_id", f"step-{step_index + 1}")) if current_step else f"step-{step_index + 1}",
            "target_id": focus["target_id"], "scenario_id": scenario["scenario_id"],
            "question_intent_key": str(current_step.get("question_intent_key", f"{focus['target_id']}.primary")) if current_step else f"{focus['target_id']}.primary",
            # Only an independent, semantic answer completes an intent.  A
            # target word or phrase is the teaching focus, not a required
            # substring in the child's sentence; that judgement belongs to
            # the state-machine LLM.  Every other evidence state remains
            # carry-over work, including ``exposed``: the child has not yet
            # answered the current question well enough to complete it.
            "result": "completed" if independent else "retryable",
            "unit_id": unit.unit_id, "unit_version": unit.version,
        }
        course_passed = course_passes(
            unit, self._turn.mastery_snapshot, scenario["scenario_id"], focus["target_id"],
            result.evaluation.target_evidence, result.evaluation.scenario_achievement,
        )
        progress_checkpoint = self._progress_checkpoint(evaluation, course_passed)
        target_progress = dict(self._turn.mastery_snapshot.get("target_progress", {}))
        unit_completed_targets = sum(
            1
            for target in unit.targets
            if int(dict(target_progress.get(str(target["target_id"]), {})).get("independent_success", 0)) > 0
        )
        presentation = None

        def visible_response(captured_before: int, captured_after: int, _wrapping_up: bool) -> str:
            nonlocal presentation
            presentation = compose_dialogue_presentation(
                result.assistant_response,
                mode=self._turn.mode,
                current_difficulty=self._turn.difficulty_level,
                next_difficulty=difficulty,
                captured_before=captured_before,
                captured_after=captured_after,
                closing=result.requested_transition == "stop_dialogue" or auto_complete or auto_limit,
                round_completion_reason="completed" if auto_complete else ("closed_without_completion" if auto_limit else None),
                unit_completed_targets=unit_completed_targets,
                unit_total_targets=len(unit.targets),
                round_plan=self._turn.round_plan,
                unit=self._context.unit,
                speech_text=result.speech_text,
            )
            return presentation.reply_text

        outcome = self._repository.commit_dialogue_turn(
            self._workflow_id, self._turn.current_child_message["event_id"], source_llm_response_event_id,
            evaluation, result.assistant_response, self._created_at,
            next_scenario_id=scenario_id, next_target_id=target_id, next_difficulty_level=difficulty,
            pending_repetition_target_id=pending, capture_targets=capture_targets,
            initial_review_stage=self._context.review_policy.initial_stage,
            initial_due_at=self._context.review_policy.initial_due_at(self._created_at),
            close_after_turn=result.requested_transition == "stop_dialogue",
            unit_course_passed=course_passed,
            assistant_response_factory=visible_response,
            next_step_index=next_step_index,
            content_turn_count=next_content_turn_count,
            round_plan_status="completed" if auto_complete else ("closed_without_completion" if auto_limit else None),
            question_intent=question_intent,
            progress_checkpoint=progress_checkpoint,
            round_completion_reason="completed" if auto_complete else ("closed_without_completion" if auto_limit else None),
        )
        if presentation is None:
            raise RepositoryError("dialogue presentation was not constructed")
        self._context.delivery.state_text = presentation.state_text
        self._context.delivery.progress_text = presentation.progress_text
        self._context.delivery.speech_text = presentation.speech_text
        return ModelTurnCommit(outcome.last_event_id, presentation.reply_text, {"closed": outcome.closed})

    def _progress_checkpoint(self, evaluation: dict[str, Any], course_passed: bool) -> dict[str, Any]:
        snapshot = {key: dict(value) for key, value in dict(self._turn.mastery_snapshot.get("target_progress", {})).items()}
        target = snapshot.setdefault(str(evaluation["target_id"]), {"exposed": 0, "supported_success": 0, "independent_success": 0, "unable": 0})
        target[str(evaluation["target_evidence"])] = int(target.get(str(evaluation["target_evidence"]), 0)) + 1
        targets = []
        for raw in self._turn.unit.get("targets", []):
            target_id = str(raw["target_id"])
            facts = snapshot.get(target_id, {})
            targets.append({"target_id": target_id, "status": "completed" if int(facts.get("independent_success", 0)) > 0 else ("supported" if int(facts.get("supported_success", 0)) > 0 else ("exposed" if int(facts.get("exposed", 0)) > 0 else "not_started")), **{key: int(facts.get(key, 0)) for key in ("exposed", "supported_success", "independent_success", "unable")}})
        scenarios = dict(self._turn.mastery_snapshot.get("scenario_progress", {}))
        ranks = {"none": 0, "basic": 1, "role_play": 2, "reasoned_response": 2}
        current = str(scenarios.get(str(evaluation["scenario_id"]), "none"))
        if ranks.get(evaluation["scenario_achievement"], 0) >= ranks.get(current, 0):
            scenarios[str(evaluation["scenario_id"])] = evaluation["scenario_achievement"]
        return {"source_child_event_id": evaluation["source_child_event_id"], "unit_id": evaluation["unit_id"], "unit_version": evaluation["unit_version"], "targets": targets, "scenarios": [{"scenario_id": key, "achievement": value} for key, value in scenarios.items()], "unit_course_passed": bool(self._turn.mastery_snapshot.get("unit_course_passed", False) or course_passed)}

    def commit_terminal_failure(self, failure_kind: FailureKind) -> ModelTurnCommit:
        del failure_kind
        text = active_dialogue_failure_text()
        event_id = self._repository.append_log_event(
            self._workflow_id, "assistant_response", {"text": text}, self._created_at, require_active_type="dialogue"
        )
        return ModelTurnCommit(event_id, text)

    def _pending_repetition(self, result: Any, focus: dict[str, Any]) -> str | None:
        pending = tuple(self._turn.pending_repetition_target_ids)
        if pending:
            return () if result.repetition_outcome == "succeeded" else pending
        if result.capture_candidate_target_ids:
            return tuple(result.capture_candidate_target_ids)
        if result.evaluation.target_evidence == "unable" and focus.get("reviewable") is True:
            return (str(focus["target_id"]),)
        return ()

    @staticmethod
    def _encode_pending_ids(values: tuple[str, ...]) -> str | None:
        if not values:
            return None
        if len(values) == 1:
            return values[0]
        return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))
