"""Pure v3 entry turn data types shared across the isolated runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


EntryOperation = Literal["reply_only", "record_entry_audit"]
EntryMode = Literal["start_entry", "collect_message"]


@dataclass(frozen=True)
class AuthorizedEntryIngress:
    """A single message already authorized by the outer static mapping."""

    message_text: str
    received_at: str
    external_session_ref: str | None = None
    start_requested: bool = False


@dataclass
class EntryTurnDelivery:
    """An in-memory result for the outer adapter; it is never checkpointed."""

    handled: bool
    reply_text: str | None = None


@dataclass(frozen=True)
class EntryMachineTurn:
    task_contract_name: str
    task_contract_version: int
    mode: EntryMode
    active_mode: Literal["entry"]
    graph_node: str
    event_id: str
    message_text: str
    pending_reentry_requests: tuple[dict[str, str | None], ...] = ()

    def as_request(self) -> dict[str, Any]:
        return {
            "task_contract_name": self.task_contract_name,
            "task_contract_version": self.task_contract_version,
            "mode": self.mode,
            "active_mode": self.active_mode,
            "graph_node": self.graph_node,
            "current_message": {"event_id": self.event_id, "text": self.message_text},
            "pending_reentry_requests": [dict(item) for item in self.pending_reentry_requests],
        }


@dataclass(frozen=True)
class MaterialAudit:
    audit_confidence: str
    needs_parent_review: bool
    audit_changes: tuple[dict[str, str], ...]
    semantic_duplicate_review: dict[str, str]

    def as_envelope(self) -> dict[str, Any]:
        return {
            "contract_name": "dada.material_audit_result",
            "contract_version": 1,
            "data": {
                "audit_confidence": self.audit_confidence,
                "needs_parent_review": self.needs_parent_review,
                "audit_changes": [dict(change) for change in self.audit_changes],
                "semantic_duplicate_review": dict(self.semantic_duplicate_review),
            },
        }


@dataclass(frozen=True)
class EntryMaterial:
    title: str
    language: Literal["en"]
    unit_type: Literal["word", "phrase", "sentence"]
    reference_text: str
    needs_parent_review: bool
    audit_result: MaterialAudit
    items: tuple[dict[str, str | None], ...]


@dataclass(frozen=True)
class ReentryRequest:
    guidance: str
    unit_type: Literal["word", "phrase", "sentence"] | None


@dataclass(frozen=True)
class EntryAudit:
    materials: tuple[EntryMaterial, ...]
    reentry_requests: tuple[ReentryRequest, ...]
    resolved_reentry_request_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntryStateMachineResult:
    assistant_response: str
    next_operation: EntryOperation
    entry_audit: EntryAudit | None = None
    guidance_kind: Literal["entry_guidance"] | None = None
    requested_transition: Literal["finish_entry", "start_review"] | None = None


@dataclass(frozen=True)
class PreparedGatewayRequest:
    provider: str
    model: str
    request: dict[str, Any]


@dataclass(frozen=True)
class GatewayExecution:
    output: Any
    internal_reasoning: Any | None = None
    internal_reasoning_unavailable_reason: str | None = None
    tool_calls: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    tool_results: tuple[dict[str, Any], ...] = field(default_factory=tuple)
