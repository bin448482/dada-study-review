"""ID-only persisted review Graph state and call-local context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypedDict

from ..contracts.review_turn import AuthorizedReviewIngress, ReviewTurnDelivery


class ReviewGraphState(TypedDict, total=False):
    workflow_id: str
    node: str
    last_committed_event_id: str | None
    recovery_required: bool
    task_contract_name: str
    task_contract_version: int
    route: Literal["start", "answer", "next", "close", "recover", "terminal"]
    turn_mode: Literal["start_review", "answer_question", "next_question"] | None
    source_child_event_id: str | None


@dataclass
class ReviewDeliveryBuffer:
    handled: bool = False
    reply_text: str | None = None
    no_due_item: bool = False

    def delivery(self) -> ReviewTurnDelivery:
        return ReviewTurnDelivery(self.handled, self.reply_text, self.no_due_item)


@dataclass
class ReviewRuntimeContext:
    ingress: AuthorizedReviewIngress
    system_prompt_snapshot: str
    policy: object
    entry_system_prompt_snapshot: str | None = None
    delivery: ReviewDeliveryBuffer = field(default_factory=ReviewDeliveryBuffer)
    pending_feedback_text: str | None = None
