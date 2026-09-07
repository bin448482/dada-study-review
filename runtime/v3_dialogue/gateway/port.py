"""Credential-free port for the fixed Dialogue state-machine task."""

from __future__ import annotations

from typing import Protocol

from ..contracts.dialogue_turn import DialogueGatewayExecution, DialogueMachineTurn, PreparedDialogueGatewayRequest


class DialogueGatewayError(RuntimeError):
    def __init__(self, message: str, reason_code: str = "provider_failed") -> None:
        super().__init__(message)
        self.reason_code = reason_code


class DialogueModelGateway(Protocol):
    def prepare(self, turn: DialogueMachineTurn) -> PreparedDialogueGatewayRequest: ...
    def execute(self, prepared_request: PreparedDialogueGatewayRequest) -> DialogueGatewayExecution: ...
