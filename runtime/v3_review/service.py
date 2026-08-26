"""Single offline entry point for one already-authorized v3 review request."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4
from typing import Callable

from langgraph.checkpoint.sqlite import SqliteSaver

from v3_workflow.persistence.repository import RepositoryError, WorkflowRepository
from v3_workflow.policy.review_schedule import ReviewSchedulePolicy

from .contracts.review_turn import AuthorizedReviewIngress, ReviewTurnDelivery
from .gateway.port import ReviewModelGateway
from .graph.build import build_review_graph
from .graph.state import ReviewDeliveryBuffer, ReviewRuntimeContext
from .handoff import EntryReviewHandoff, EntryReviewHandoffOutcome
from .question_modes import choose_question_mode


class ReviewTurnService:
    def __init__(self, database_path: Path, gateway: ReviewModelGateway, system_prompt_snapshot: str, policy: ReviewSchedulePolicy, entry_system_prompt_snapshot: str | None = None, question_mode_selector: Callable[[str, tuple[str, ...]], str] = choose_question_mode) -> None:
        if not isinstance(system_prompt_snapshot, str) or not system_prompt_snapshot:
            raise ValueError("system_prompt_snapshot must be non-empty")
        self.repository = WorkflowRepository(database_path)
        self.repository.initialize()
        self._checkpoint_connection = sqlite3.connect(database_path, check_same_thread=False)
        self._checkpointer = SqliteSaver(self._checkpoint_connection)
        self._checkpointer.setup()
        self._graph = build_review_graph(self.repository, gateway, self._checkpointer, question_mode_selector)
        self._gateway = gateway
        self._system_prompt_snapshot = system_prompt_snapshot
        self._policy = policy
        self._entry_system_prompt_snapshot = entry_system_prompt_snapshot
        self._question_mode_selector = question_mode_selector
        self._handoff = EntryReviewHandoff(
            self.repository, gateway, system_prompt_snapshot, self._checkpoint_waiting, question_mode_selector
        )

    def close(self) -> None:
        self._checkpoint_connection.close()

    def handle(self, ingress: AuthorizedReviewIngress) -> ReviewTurnDelivery:
        if not isinstance(ingress.message_text, str) or not ingress.message_text or not isinstance(ingress.external_session_ref, str) or not ingress.external_session_ref:
            raise ValueError("review ingress requires non-empty message and external session")
        existing = self.repository.get_active_workflow(ingress.external_session_ref, "review")
        if existing is None and not ingress.start_requested:
            return ReviewTurnDelivery(False)
        if existing is not None and ingress.start_requested:
            return ReviewTurnDelivery(False)
        thread_id = existing["workflow_id"] if existing is not None else str(uuid4())
        initial_state = {"workflow_id": thread_id}
        if existing is None:
            initial_state["node"] = "review_route"
        elif self._checkpointer.get_tuple({"configurable": {"thread_id": thread_id}}) is None:
            initial_state["node"] = "review_recover"
        delivery = ReviewDeliveryBuffer()
        context = ReviewRuntimeContext(ingress, self._system_prompt_snapshot, self._policy, self._entry_system_prompt_snapshot, delivery)
        try:
            self._graph.invoke(initial_state, {"configurable": {"thread_id": thread_id}}, context=context)
        except Exception:
            return ReviewTurnDelivery(delivery.handled)
        return delivery.delivery()

    def start_from_entry(self, entry_workflow_id: str, external_session_ref: str, trigger_message: str, received_at: str) -> EntryReviewHandoffOutcome | None:
        return self._handoff.start_from_entry(entry_workflow_id, external_session_ref, trigger_message, received_at)

    def _checkpoint_waiting(self, workflow_id: str, external_session_ref: str, trigger_message: str, received_at: str) -> bool:
        context = ReviewRuntimeContext(
            AuthorizedReviewIngress(trigger_message, received_at, external_session_ref, True),
            self._system_prompt_snapshot, self._policy, self._entry_system_prompt_snapshot, ReviewDeliveryBuffer(),
        )
        try:
            self._graph.invoke({"workflow_id": workflow_id, "node": "review_waiting_for_child"}, {"configurable": {"thread_id": workflow_id}}, context=context)
        except Exception:
            return False
        return True
