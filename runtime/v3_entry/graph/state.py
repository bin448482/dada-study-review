"""Persisted no-body Graph state and ephemeral LangGraph runtime context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypedDict

from ..contracts.entry_turn import AuthorizedEntryIngress, EntryTurnDelivery


class EntryGraphState(TypedDict, total=False):
    entry_workflow_id: str
    node: str
    last_committed_event_id: str | None
    recovery_required: bool
    task_contract_name: str
    task_contract_version: int
    route: Literal["process", "close", "switch", "recover", "terminal"]
    turn_mode: Literal["start_entry", "collect_message"] | None


@dataclass
class DeliveryBuffer:
    handled: bool = False
    reply_text: str | None = None

    def delivery(self) -> EntryTurnDelivery:
        return EntryTurnDelivery(handled=self.handled, reply_text=self.reply_text)


@dataclass
class EntryRuntimeContext:
    """Call-local values intentionally excluded from checkpoint persistence."""

    ingress: AuthorizedEntryIngress
    system_prompt_snapshot: str
    review_handoff: object | None = None
    delivery: DeliveryBuffer = field(default_factory=DeliveryBuffer)
