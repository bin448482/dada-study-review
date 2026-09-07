"""Single offline entry point for one already-authorized Dialogue request."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver

from v3_workflow.persistence.repository import WorkflowRepository

from .contracts.dialogue_turn import AuthorizedDialogueIngress, DialogueTurnDelivery
from .gateway.port import DialogueModelGateway
from .graph.build import build_dialogue_graph
from .graph.state import DialogueDeliveryBuffer, DialogueRuntimeContext
from .unit_definition import DialoguePolicy, UnitDefinition


class DialogueTurnService:
    def __init__(
        self, database_path: Path, gateway: DialogueModelGateway, system_prompt_snapshot: str,
        unit: UnitDefinition, policy: DialoguePolicy, review_policy: object,
    ) -> None:
        if not isinstance(system_prompt_snapshot, str) or not system_prompt_snapshot:
            raise ValueError("dialogue system prompt snapshot must be non-empty")
        self.repository = WorkflowRepository(database_path)
        self.repository.initialize()
        self._checkpoint_connection = sqlite3.connect(database_path, check_same_thread=False)
        self._checkpointer = SqliteSaver(self._checkpoint_connection)
        self._checkpointer.setup()
        self._graph = build_dialogue_graph(self.repository, gateway, self._checkpointer)
        self._system_prompt_snapshot = system_prompt_snapshot
        self._unit = unit
        self._policy = policy
        self._review_policy = review_policy

    def close(self) -> None:
        self._checkpoint_connection.close()

    def handle(self, ingress: AuthorizedDialogueIngress) -> DialogueTurnDelivery:
        if not isinstance(ingress.message_text, str) or not ingress.message_text or not isinstance(ingress.external_session_ref, str) or not ingress.external_session_ref:
            raise ValueError("dialogue ingress requires non-empty message and external session")
        existing = self.repository.get_active_workflow(ingress.external_session_ref)
        if existing is None and not ingress.start_requested:
            return DialogueTurnDelivery(False)
        if existing is not None and existing["workflow_type"] != "dialogue":
            return DialogueTurnDelivery(False)
        if existing is not None and ingress.start_requested:
            return DialogueTurnDelivery(False)
        thread_id = str(existing["workflow_id"]) if existing is not None else str(uuid4())
        initial_state: dict[str, object] = {"workflow_id": thread_id}
        if existing is None:
            initial_state["node"] = "dialogue_route"
        elif self._checkpointer.get_tuple({"configurable": {"thread_id": thread_id}}) is None:
            initial_state["node"] = "dialogue_recover"
        delivery = DialogueDeliveryBuffer()
        context = DialogueRuntimeContext(ingress, self._system_prompt_snapshot, self._unit, self._policy, self._review_policy, delivery)
        try:
            self._graph.invoke(initial_state, {"configurable": {"thread_id": thread_id}}, context=context)
        except Exception:
            return DialogueTurnDelivery(delivery.handled)
        return delivery.delivery()
