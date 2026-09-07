"""ID-only Dialogue Graph state and call-local runtime context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypedDict

from ..contracts.dialogue_turn import AuthorizedDialogueIngress, DialogueTurnDelivery
from ..unit_definition import DialoguePolicy, UnitDefinition


class DialogueGraphState(TypedDict, total=False):
    workflow_id: str
    node: str
    last_committed_event_id: str | None
    source_child_event_id: str | None
    task_contract_name: str
    task_contract_version: int
    route: Literal["start", "continue", "wrapping", "recover", "terminal"]
    turn_mode: Literal["start_dialogue", "continue_dialogue", "wrapping_up"] | None


@dataclass
class DialogueDeliveryBuffer:
    handled: bool = False
    reply_text: str | None = None
    state_text: str | None = None
    progress_text: str | None = None
    speech_text: str | None = None

    def delivery(self) -> DialogueTurnDelivery:
        return DialogueTurnDelivery(self.handled, self.reply_text, self.state_text, self.progress_text, self.speech_text)


@dataclass
class DialogueRuntimeContext:
    ingress: AuthorizedDialogueIngress
    system_prompt_snapshot: str
    unit: UnitDefinition
    policy: DialoguePolicy
    review_policy: object
    delivery: DialogueDeliveryBuffer = field(default_factory=DialogueDeliveryBuffer)
