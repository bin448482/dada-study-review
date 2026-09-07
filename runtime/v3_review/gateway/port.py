"""Credential-free port for the fixed review state-machine task."""

from __future__ import annotations

from typing import Protocol

from ..contracts.review_turn import PreparedReviewGatewayRequest, ReviewGatewayExecution, ReviewMachineTurn


class GatewayError(RuntimeError):
    """A fixed provider call failed without exposing provider response content."""

    def __init__(self, message: str, reason_code: str = "provider_failed") -> None:
        super().__init__(message)
        self.reason_code = reason_code


class ReviewModelGateway(Protocol):
    def prepare(self, turn: ReviewMachineTurn) -> PreparedReviewGatewayRequest: ...
    def execute(self, prepared_request: PreparedReviewGatewayRequest) -> ReviewGatewayExecution: ...
