"""Build the injected SQLite-checkpointed Dialogue Graph."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from v3_workflow.persistence.repository import WorkflowRepository

from ..gateway.port import DialogueModelGateway
from .nodes import dialogue_model_turn, dialogue_recover, dialogue_route
from .state import DialogueGraphState, DialogueRuntimeContext


def build_dialogue_graph(repository: WorkflowRepository, gateway: DialogueModelGateway, checkpointer):
    graph = StateGraph(DialogueGraphState, context_schema=DialogueRuntimeContext)
    graph.add_node("dialogue_route", dialogue_route(repository))
    graph.add_node("dialogue_starting", dialogue_model_turn(repository, gateway))
    graph.add_node("dialogue_continuing", dialogue_model_turn(repository, gateway))
    graph.add_node("dialogue_wrapping", dialogue_model_turn(repository, gateway))
    graph.add_node("dialogue_recover", dialogue_recover(repository))
    graph.add_edge(START, "dialogue_route")
    graph.add_conditional_edges(
        "dialogue_route", lambda state: state["route"],
        {"start": "dialogue_starting", "continue": "dialogue_continuing", "wrapping": "dialogue_wrapping", "recover": "dialogue_recover", "terminal": END},
    )
    graph.add_edge("dialogue_starting", END)
    graph.add_edge("dialogue_continuing", END)
    graph.add_edge("dialogue_wrapping", END)
    graph.add_edge("dialogue_recover", END)
    return graph.compile(checkpointer=checkpointer)
