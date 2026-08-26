"""Build the injected SQLite-checkpointed v3 review Graph."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from v3_workflow.persistence.repository import WorkflowRepository

from ..gateway.port import ReviewModelGateway
from .nodes import review_model_turn, review_recover, review_route
from .state import ReviewGraphState, ReviewRuntimeContext


def build_review_graph(repository: WorkflowRepository, gateway: ReviewModelGateway, checkpointer, question_mode_selector):
    graph = StateGraph(ReviewGraphState, context_schema=ReviewRuntimeContext)
    graph.add_node("review_route", review_route(repository))
    graph.add_node("review_starting", review_model_turn(repository, gateway, question_mode_selector))
    graph.add_node("review_process_answer", review_model_turn(repository, gateway, question_mode_selector))
    graph.add_node("review_next_question", review_model_turn(repository, gateway, question_mode_selector))
    graph.add_node("review_recover", review_recover(repository))
    graph.add_edge(START, "review_route")
    graph.add_conditional_edges("review_route", lambda state: state["route"], {"start": "review_starting", "answer": "review_process_answer", "next": "review_next_question", "recover": "review_recover", "terminal": END})
    graph.add_edge("review_starting", END)
    graph.add_conditional_edges("review_process_answer", lambda state: state["route"], {"next": "review_next_question", "terminal": END})
    graph.add_edge("review_next_question", END)
    graph.add_edge("review_recover", END)
    return graph.compile(checkpointer=checkpointer)
