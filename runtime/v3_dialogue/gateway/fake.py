"""Deterministic no-credential Dialogue gateway for offline tests."""

from __future__ import annotations

from collections import deque
from typing import Any, Iterable

from ..contracts.dialogue_turn import DialogueGatewayExecution, DialogueMachineTurn, PreparedDialogueGatewayRequest
from .port import DialogueGatewayError


class FakeDialogueModelGateway:
    def __init__(self, executions: Iterable[DialogueGatewayExecution | Exception]) -> None:
        self._executions = deque(executions)
        self.prepared_turns: list[DialogueMachineTurn] = []

    def prepare(self, turn: DialogueMachineTurn) -> PreparedDialogueGatewayRequest:
        self.prepared_turns.append(turn)
        return PreparedDialogueGatewayRequest("fake", "fake-dialogue-state-machine", {"task": turn.as_request(), "tools": []})

    def execute(self, prepared_request: PreparedDialogueGatewayRequest) -> DialogueGatewayExecution:
        if not self._executions:
            raise DialogueGatewayError("fake gateway has no scripted execution")
        value = self._executions.popleft()
        if isinstance(value, Exception):
            raise value
        if value.tool_calls or value.tool_results:
            raise DialogueGatewayError("dialogue state-machine task has no tools")
        return value


def dialogue_result(
    text: str, *, evidence: str = "exposed", scenario: str = "none", grammar: list[dict[str, str]] | None = None,
    repetition_outcome: str = "not_requested", capture_target_id: str | None = None,
    capture_target_ids: list[str] | None = None, content_slots_covered: list[str] | None = None,
    difficulty: str = "stay", transition: str | None = None, level_behavior: str = "guided_question",
    speech_text: str | None = None,
) -> DialogueGatewayExecution:
    data = {
        "assistant_response": text,
        "level_behavior": level_behavior,
        "evaluation": {"target_evidence": evidence, "scenario_achievement": scenario, "grammar_observations": grammar or [], "content_slots_covered": content_slots_covered or []},
        "repetition_outcome": repetition_outcome,
        "capture_candidate_target_ids": capture_target_ids if capture_target_ids is not None else ([capture_target_id] if capture_target_id is not None else []),
        "difficulty_suggestion": difficulty,
        "requested_transition": transition,
    }
    if speech_text is not None:
        data["speech_text"] = speech_text
    return DialogueGatewayExecution({
        "contract_name": "dada.dialogue_state_machine_result", "contract_version": 1,
        "data": data,
    })
