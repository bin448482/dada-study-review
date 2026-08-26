"""The sole production composition root; callers provide static authorization and credentials separately."""

from __future__ import annotations

from pathlib import Path

from v3_entry.service import EntryTurnService
from v3_workflow.policy.review_schedule import ReviewSchedulePolicy

from .bundle import load_state_machine_definition
from .gateway import ProductionGatewayConfig, ResponsesModelGateway, ResponsesTransport


def build_entry_service(
    database_path: Path,
    definition_directory: Path,
    definition_digest: str,
    gateway_config: ProductionGatewayConfig,
    transport: ResponsesTransport,
    *,
    policy: ReviewSchedulePolicy | None = None,
    review_handoff: object | None = None,
) -> EntryTurnService:
    """Build a v3 service from one verified definition and fixed deployment transport."""

    definition = load_state_machine_definition(definition_directory, "dada-entry-state-machine", definition_digest)
    gateway = ResponsesModelGateway(gateway_config, definition, transport)
    return EntryTurnService(database_path, gateway, definition.system_prompt, review_handoff, policy)
