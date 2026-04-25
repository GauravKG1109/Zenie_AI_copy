"""
get_knowledgebase.py — Stub node for GET_KNOWLEDGEBASE routing.

When the orchestrator determines the user is asking about company policies,
internal procedures, definitions, or anything requiring document lookup,
it routes here. Currently returns a placeholder reply.

Future implementation: integrate a vector store or document retrieval system.
"""
import logging
from services.graph.state import GraphState

logger = logging.getLogger(__name__)


def get_knowledgebase_node(state: GraphState) -> dict:
    message = state.get("message", "")
    logger.info("[KnowledgeBase] Stub node invoked for: %s", message)
    return {
        "reply": (
            "I can see you're asking about our knowledge base. "
            "This feature is currently being set up — please check back soon, "
            "or contact your administrator for policy and procedure questions."
        ),
        "payload_logs": [
            "[KnowledgeBase] Stub invoked — knowledge base integration not yet implemented"
        ],
    }
