"""The sole production composition root for ReviewTurnService."""

from __future__ import annotations
from pathlib import Path
from typing import Callable
from v3_review.service import ReviewTurnService
from v3_workflow.policy.review_schedule import ReviewSchedulePolicy
from .bundle import load_state_machine_definition
from .gateway import ProductionGatewayConfig, ResponsesModelGateway, ResponsesTransport


def build_review_service(database_path: Path, definition_directory: Path, definition_digest: str, gateway_config: ProductionGatewayConfig, transport: ResponsesTransport, policy: ReviewSchedulePolicy, entry_system_prompt_snapshot: str | None = None, question_mode_selector: Callable[[str, tuple[str, ...]], str] | None = None) -> ReviewTurnService:
    definition = load_state_machine_definition(definition_directory, "dada-review-state-machine", definition_digest)
    if question_mode_selector is None:
        return ReviewTurnService(database_path, ResponsesModelGateway(gateway_config, definition, transport), definition.system_prompt, policy, entry_system_prompt_snapshot)
    return ReviewTurnService(database_path, ResponsesModelGateway(gateway_config, definition, transport), definition.system_prompt, policy, entry_system_prompt_snapshot, question_mode_selector)
