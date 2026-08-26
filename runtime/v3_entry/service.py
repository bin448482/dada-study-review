"""Single in-process entry point for one already-authorized v3 child message."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver

from .contracts.entry_turn import AuthorizedEntryIngress, EntryTurnDelivery
from .gateway.port import ModelGateway
from .graph.build import build_entry_graph
from .graph.state import DeliveryBuffer, EntryRuntimeContext
from .persistence.repository import EntryRepository
from v3_workflow.policy.review_schedule import ReviewSchedulePolicy


class EntryTurnService:
    """Composes injected boundaries without authorizing, routing, or delivering messages."""

    def __init__(
        self,
        database_path: Path,
        gateway: ModelGateway,
        system_prompt_snapshot: str,
        review_handoff: object | None = None,
        policy: ReviewSchedulePolicy | None = None,
    ) -> None:
        if not isinstance(system_prompt_snapshot, str) or not system_prompt_snapshot:
            raise ValueError("system_prompt_snapshot must be non-empty")
        self.repository = EntryRepository(database_path, policy)
        self.repository.initialize()
        self._checkpoint_connection = sqlite3.connect(database_path, check_same_thread=False)
        self._checkpointer = SqliteSaver(self._checkpoint_connection)
        self._checkpointer.setup()
        self._graph = build_entry_graph(self.repository, gateway, self._checkpointer)
        self._system_prompt_snapshot = system_prompt_snapshot
        self._review_handoff = review_handoff

    def close(self) -> None:
        self._checkpoint_connection.close()

    def handle(self, ingress: AuthorizedEntryIngress) -> EntryTurnDelivery:
        """Run one graph turn and return only a reply whose final checkpoint succeeded."""

        if not isinstance(ingress.message_text, str) or not ingress.message_text:
            raise ValueError("message_text must be non-empty")
        if not isinstance(ingress.external_session_ref, str) or not ingress.external_session_ref:
            raise ValueError("v3 entry ingress requires an external session reference")
        existing = self.repository.get_collecting_entry(ingress.external_session_ref)
        if existing is None and not ingress.start_requested:
            return EntryTurnDelivery(handled=False, reply_text=None)
        if existing is not None and ingress.start_requested:
            # The fixed start tool can race with another accepted start. Do not
            # reinterpret its internal control text as a child recording line.
            return EntryTurnDelivery(handled=False, reply_text=None)
        thread_id = existing["entry_workflow_id"] if existing is not None else str(uuid4())
        initial_state = {"entry_workflow_id": thread_id}
        if existing is None:
            initial_state["node"] = "entry_route"
        elif self._checkpointer.get_tuple({"configurable": {"thread_id": thread_id}}) is None and not self.repository.entry_handoff_has_initial_reply(thread_id):
            # Business facts exist but the private projection does not: recover before accepting new content.
            initial_state["node"] = "entry_recover"
        delivery = DeliveryBuffer()
        context = EntryRuntimeContext(ingress=ingress, system_prompt_snapshot=self._system_prompt_snapshot, review_handoff=self._review_handoff, delivery=delivery)
        try:
            self._graph.invoke(initial_state, {"configurable": {"thread_id": thread_id}}, context=context)
        except Exception:
            # Any failed checkpoint or incomplete node must suppress this turn's reply.
            return EntryTurnDelivery(handled=delivery.handled, reply_text=None)
        return delivery.delivery()
