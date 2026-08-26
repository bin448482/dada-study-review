"""LangGraph construction for v3 entry flow with injected dependencies."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..gateway.port import ModelGateway
from ..persistence.repository import EntryRepository
from .nodes import entry_closing, entry_process_turn, entry_recover, entry_route
from .state import EntryGraphState, EntryRuntimeContext


def build_entry_graph(repository: EntryRepository, gateway: ModelGateway, checkpointer):
    """Compile the no-body entry graph; no provider or file-system side effects occur here."""

    graph = StateGraph(EntryGraphState, context_schema=EntryRuntimeContext)
    graph.add_node("entry_route", entry_route(repository))
    graph.add_node("entry_process_turn", entry_process_turn(repository, gateway))
    graph.add_node("entry_closing", entry_closing(repository))
    graph.add_node("entry_recover", entry_recover(repository))
    graph.add_edge(START, "entry_route")
    graph.add_conditional_edges(
        "entry_route",
        lambda state: state["route"],
        {
            "process": "entry_process_turn",
            "close": "entry_closing",
            "recover": "entry_recover",
            "terminal": END,
        },
    )
    graph.add_edge("entry_process_turn", END)
    graph.add_edge("entry_closing", END)
    graph.add_conditional_edges("entry_recover", lambda state: state["route"], {"process": "entry_process_turn", "terminal": END})
    return graph.compile(checkpointer=checkpointer)
