"""Credential-free port for the fixed review state-machine task."""

from __future__ import annotations

from typing import Protocol

from ..contracts.review_turn import PreparedReviewGatewayRequest, ReviewGatewayExecution, ReviewMachineTurn


class GatewayError(RuntimeError):
    pass


class ReviewModelGateway(Protocol):
    def prepare(self, turn: ReviewMachineTurn) -> PreparedReviewGatewayRequest: ...
    def execute(self, prepared_request: PreparedReviewGatewayRequest) -> ReviewGatewayExecution: ...
