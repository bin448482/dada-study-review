"""Pure DTOs for one authorized Dialogue state-machine turn."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


DialogueMode = Literal["start_dialogue", "continue_dialogue", "wrapping_up"]
DialogueEvidence = Literal["exposed", "supported_success", "independent_success", "unable"]
ScenarioAchievement = Literal["none", "basic", "role_play", "reasoned_response"]


@dataclass(frozen=True)
class AuthorizedDialogueIngress:
    message_text: str
    received_at: str
    external_session_ref: str
    start_requested: bool = False


@dataclass
class DialogueTurnDelivery:
    handled: bool
    reply_text: str | None = None
    state_text: str | None = None
    progress_text: str | None = None
    speech_text: str | None = None


@dataclass(frozen=True)
class DialogueMachineTurn:
    task_contract_name: str
    task_contract_version: int
    mode: DialogueMode
    graph_node: str
    workflow_id: str
    unit: dict[str, Any]
    scenario: dict[str, Any]
    focus_target: dict[str, Any]
    difficulty_level: int
    pending_repetition_target_ids: tuple[str, ...]
    subphase: Literal["normal", "wrapping_up"]
    mastery_snapshot: dict[str, Any]
    current_child_message: dict[str, str]
    history: tuple[dict[str, Any], ...]
    history_truncated: bool
    round_plan: dict[str, Any] = field(default_factory=dict)
    active_step: dict[str, Any] = field(default_factory=dict)
    used_question_intents: tuple[str, ...] = field(default_factory=tuple)
    max_capture_candidates: int = 3

    def as_request(self) -> dict[str, Any]:
        return {
            "task_contract_name": self.task_contract_name,
            "task_contract_version": self.task_contract_version,
            "mode": self.mode,
            "active_mode": "dialogue",
            "graph_node": self.graph_node,
            "workflow_id": self.workflow_id,
            "unit": dict(self.unit),
            "scenario": dict(self.scenario),
            "focus_target": dict(self.focus_target),
            "difficulty_level": self.difficulty_level,
            "pending_repetition_target_ids": list(self.pending_repetition_target_ids),
            "subphase": self.subphase,
            "mastery_snapshot": dict(self.mastery_snapshot),
            "current_child_message": dict(self.current_child_message),
            "history": [dict(item) for item in self.history],
            "history_truncated": self.history_truncated,
            "round_plan": dict(self.round_plan),
            "active_step": dict(self.active_step),
            "used_question_intents": list(self.used_question_intents),
            "max_capture_candidates": self.max_capture_candidates,
        }


@dataclass(frozen=True)
class DialogueEvaluation:
    target_evidence: DialogueEvidence
    scenario_achievement: ScenarioAchievement
    grammar_observations: tuple[dict[str, str], ...]
    content_slots_covered: tuple[str, ...] = ()


@dataclass(frozen=True)
class DialogueStateMachineResult:
    assistant_response: str
    level_behavior: Literal["guided_question", "role_play", "reasoned_response"]
    evaluation: DialogueEvaluation
    repetition_outcome: Literal["not_requested", "succeeded", "not_succeeded"]
    capture_candidate_target_ids: tuple[str, ...]
    difficulty_suggestion: Literal["stay", "raise", "lower"]
    requested_transition: Literal["stop_dialogue"] | None
    speech_text: str | None = None


@dataclass(frozen=True)
class PreparedDialogueGatewayRequest:
    provider: str
    model: str
    request: dict[str, Any]


@dataclass(frozen=True)
class DialogueGatewayExecution:
    output: Any
    internal_reasoning: Any | None = None
    internal_reasoning_unavailable_reason: str | None = None
    tool_calls: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    tool_results: tuple[dict[str, Any], ...] = field(default_factory=tuple)
