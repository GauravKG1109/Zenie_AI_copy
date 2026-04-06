import logging
from langgraph.graph import StateGraph, START, END

from services.graph.state import GraphState
from services.graph.nodes.orchestrator import orchestrator_node
from services.graph.nodes.intent_classifier import intent_classifier_node
from services.graph.nodes.date_extractor import date_extractor_node
from services.graph.nodes.sql_generator import sql_generator_node

logger = logging.getLogger(__name__)


def build_graph():
    g = StateGraph(GraphState)

    g.add_node("orchestrator", orchestrator_node)
    g.add_node("intent_classifier", intent_classifier_node)
    g.add_node("date_extractor", date_extractor_node)
    g.add_node("sql_generator", sql_generator_node)

    g.add_edge(START, "orchestrator")

    # Fan-out: both run in parallel after orchestrator
    g.add_edge("orchestrator", "intent_classifier")
    g.add_edge("orchestrator", "date_extractor")

    # Fan-in: sql_generator waits for both branches
    g.add_edge("intent_classifier", "sql_generator")
    g.add_edge("date_extractor", "sql_generator")

    g.add_edge("sql_generator", END)

    compiled = g.compile()
    logger.info("[Graph] LangGraph pipeline compiled successfully.")
    return compiled


# Singleton — compiled once at import time
pipeline = build_graph()
