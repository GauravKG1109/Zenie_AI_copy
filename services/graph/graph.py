import logging
from langgraph.graph import StateGraph, START, END

from services.graph.state import GraphState
from services.graph.nodes.orchestrator import orchestrator_node
from services.graph.nodes.intent_classifier import intent_classifier_node
from services.graph.nodes.date_extractor import date_extractor_node
from services.graph.nodes.sql_generator import sql_generator_node
from services.graph.nodes.LLM_payload_filler import payload_filler_node, get_active_write_intent

logger = logging.getLogger(__name__)


def _route_from_orchestrator(state: GraphState) -> str:
    """
    First routing decision — runs immediately after orchestrator.

    If the session already has an in-progress WRITE conversation (i.e. the user
    is answering follow-up questions for field collection), skip intent_classifier
    entirely and go straight to payload_filler_node.  This prevents follow-up
    messages like "DRAFT" or "INV-001" from being re-classified as new intents.

    Otherwise run the normal intent_classifier → conditional-branch path.
    """
    session_id = state.get("session_id", "")
    if get_active_write_intent(session_id):
        logger.info(
            "[Graph] Active WRITE session for session_id=%s — bypassing intent_classifier",
            session_id,
        )
        return "payload_filler_node"
    return "intent_classifier"


def _route_after_intent(state: GraphState) -> str:
    """
    Second routing decision — runs after intent_classifier (first message only).
    Routes WRITE intents to payload_filler_node; everything else to date_extractor.
    """
    action_type = state.get("intent", {}).get("action_type", "").strip().upper()
    if action_type == "WRITE":
        logger.info("[Graph] WRITE intent detected — routing to payload_filler_node")
        return "payload_filler_node"
    logger.info("[Graph] READ intent — routing to date_extractor")
    return "date_extractor"


def build_graph():
    g = StateGraph(GraphState)

    # Register nodes
    g.add_node("orchestrator",        orchestrator_node)
    g.add_node("intent_classifier",   intent_classifier_node)
    g.add_node("date_extractor",      date_extractor_node)
    g.add_node("sql_generator",       sql_generator_node)
    g.add_node("payload_filler_node", payload_filler_node)

    g.add_edge(START, "orchestrator")

    # After orchestrator: resume active WRITE session OR run intent_classifier
    g.add_conditional_edges(
        "orchestrator",
        _route_from_orchestrator,
        {
            "payload_filler_node": "payload_filler_node",  # resume in-progress WRITE
            "intent_classifier":   "intent_classifier",    # fresh classification
        },
    )

    # After intent_classifier (first message only): branch on action_type
    g.add_conditional_edges(
        "intent_classifier",
        _route_after_intent,
        {
            "payload_filler_node": "payload_filler_node",
            "date_extractor":      "date_extractor",
        },
    )

    # READ path: date_extractor → sql_generator → END
    g.add_edge("date_extractor",      "sql_generator")
    g.add_edge("sql_generator",       END)

    # WRITE path: payload_filler_node → END (multi-turn, no SQL)
    g.add_edge("payload_filler_node", END)

    compiled = g.compile()
    logger.info("[Graph] LangGraph pipeline compiled successfully.")
    return compiled


# Singleton — compiled once at import time
pipeline = build_graph()
