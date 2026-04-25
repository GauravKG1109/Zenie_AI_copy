import logging
from langgraph.graph import StateGraph, START, END

from services.graph.state import GraphState
from services.graph.nodes.orchestrator import orchestrator_node, INTENT_NONE, INTENT_KB_NODE
from services.graph.nodes.intent_classifier import intent_classifier_node
from services.graph.nodes.date_extractor import date_extractor_node
from services.graph.nodes.sql_generator import sql_generator_node
from services.graph.nodes.LLM_payload_filler import payload_filler_node
from services.graph.nodes.get_knowledgebase import get_knowledgebase_node

logger = logging.getLogger(__name__)


# ── Thin pass-through node for NONE path ─────────────────────────────────────
# Copies orchestrator_reply → reply so message_service handles it the same way
# as the WRITE path (both produce a "reply" field).

def _end_with_reply(state: GraphState) -> dict:
    return {"reply": state.get("orchestrator_reply", "")}


# ── Routing after orchestrator ────────────────────────────────────────────────

def _route_from_orchestrator(state: GraphState) -> str:
    """
    Routes based on the orchestrator's intent_code decision.

    - NONE            → end_with_reply (direct bot reply, no pipeline)
    - GET_KNOWLEDGEBASE → get_knowledgebase_node (stub)
    - real WRITE code → payload_filler_node
    - real READ code  → date_extractor → sql_generator
    - "intent_classifier" fallback → use top-1 from state["intent"] (action_type check)
    """
    code = state.get("orchestrator_intent_code", INTENT_NONE)

    if code == INTENT_NONE:
        logger.info("[Graph] Orchestrator → NONE — ending with direct reply")
        return "end_with_reply"

    if code == INTENT_KB_NODE:
        logger.info("[Graph] Orchestrator → GET_KNOWLEDGEBASE")
        return "get_knowledgebase_node"

    # Real intent code (or "intent_classifier" hallucination fallback).
    # Orchestrator has already updated state["intent"] to the chosen candidate,
    # so action_type here is authoritative.
    action_type = state.get("intent", {}).get("action_type", "").strip().upper()
    if action_type == "WRITE":
        logger.info("[Graph] Orchestrator → WRITE intent — payload_filler_node")
        return "payload_filler_node"

    logger.info("[Graph] Orchestrator → READ/fallback intent — date_extractor")
    return "date_extractor"


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_graph():
    g = StateGraph(GraphState)

    # Register nodes
    g.add_node("intent_classifier",     intent_classifier_node)
    g.add_node("orchestrator",          orchestrator_node)
    g.add_node("end_with_reply",        _end_with_reply)
    g.add_node("get_knowledgebase_node", get_knowledgebase_node)
    g.add_node("date_extractor",        date_extractor_node)
    g.add_node("sql_generator",         sql_generator_node)
    g.add_node("payload_filler_node",   payload_filler_node)

    # Sequential entry: classifier always feeds candidates to orchestrator
    g.add_edge(START, "intent_classifier")
    g.add_edge("intent_classifier", "orchestrator")

    # Orchestrator decides the route
    g.add_conditional_edges(
        "orchestrator",
        _route_from_orchestrator,
        {
            "end_with_reply":         "end_with_reply",
            "get_knowledgebase_node": "get_knowledgebase_node",
            "date_extractor":         "date_extractor",
            "payload_filler_node":    "payload_filler_node",
        },
    )

    # NONE path
    g.add_edge("end_with_reply", END)

    # KB stub path
    g.add_edge("get_knowledgebase_node", END)

    # READ path
    g.add_edge("date_extractor",  "sql_generator")
    g.add_edge("sql_generator",   END)

    # WRITE path
    g.add_edge("payload_filler_node", END)

    compiled = g.compile()
    logger.info("[Graph] LangGraph pipeline compiled successfully.")
    return compiled


# Singleton — compiled once at import time
pipeline = build_graph()


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))
    graph = build_graph()
    try:
        img_bytes = graph.get_graph().draw_mermaid_png()
        with open("graph_diagram.png", "wb") as f:
            f.write(img_bytes)
        print("Saved graph_diagram.png")
    except Exception as e:
        print(f"PNG failed ({e}), mermaid text:\n")
        print(graph.get_graph().draw_mermaid())
