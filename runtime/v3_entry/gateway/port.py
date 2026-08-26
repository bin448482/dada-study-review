"""Credential-free Graph-facing port for the fixed entry state-machine task."""

from __future__ import annotations

from typing import Protocol

from ..contracts.entry_turn import EntryMachineTurn, GatewayExecution, PreparedGatewayRequest


class GatewayError(RuntimeError):
    """The provider-facing side could not safely complete a fixed task call."""


class ModelGateway(Protocol):
    """Graph-facing two-step port; implementations never receive a repository."""

    def prepare(self, turn: EntryMachineTurn) -> PreparedGatewayRequest:
        """Build a fully redacted request for the fixed task without executing it."""

    def execute(self, prepared_request: PreparedGatewayRequest) -> GatewayExecution:
        """Execute only the prepared fixed request and return provider facts."""
