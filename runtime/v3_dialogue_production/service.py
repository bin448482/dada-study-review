"""Production composition root for the offline Dialogue service."""

from __future__ import annotations

from pathlib import Path

from v3_dialogue.service import DialogueTurnService
from v3_dialogue.unit_definition import load_dialogue_policy, load_unit_definition
from v3_workflow.policy.review_schedule import ReviewSchedulePolicy

from .bundle import load_state_machine_definition
from .gateway import ProductionGatewayConfig, ResponsesModelGateway, ResponsesTransport


def build_dialogue_service(
    database_path: Path, definition_directory: Path, definition_digest: str, gateway_config: ProductionGatewayConfig,
    transport: ResponsesTransport, unit_path: Path, policy_path: Path, review_schedule_path: Path,
) -> DialogueTurnService:
    definition = load_state_machine_definition(definition_directory, "dada-dialogue-state-machine", definition_digest)
    unit = load_unit_definition(unit_path)
    return DialogueTurnService(database_path, ResponsesModelGateway(gateway_config, definition, transport), definition.system_prompt, unit, load_dialogue_policy(policy_path), ReviewSchedulePolicy.from_file(review_schedule_path))
