"""In-process contracts for one frozen state-machine model turn.

These DTOs deliberately carry no provider configuration or business meaning.
Stage adapters own the turn shape, semantic validator and atomic commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


FailureKind = Literal["gateway", "contract", "commit", "checkpoint"]


@dataclass(frozen=True)
class ModelTurnSpec:
    """The immutable input identity used across both permitted attempts."""

    workflow_id: str
    source_child_event_id: str
    created_at: str
    task_contract_name: str
    task_contract_version: int
    turn: Any


@dataclass(frozen=True)
class ModelTurnCommit:
    """A stage-owned, already persisted child-visible outcome."""

    last_event_id: str
    reply_text: str
    metadata: Any = None


@dataclass(frozen=True)
class ModelTurnOutcome:
    """Pipeline result; callers decide only Graph routing from `metadata`."""

    success: bool
    terminal_failure: bool
    infrastructure_failed: bool
    last_event_id: str | None
    reply_text: str | None
    result: Any = None
    metadata: Any = None
