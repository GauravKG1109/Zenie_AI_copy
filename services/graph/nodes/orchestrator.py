import logging
from services.graph.state import GraphState

logger = logging.getLogger(__name__)


def orchestrator_node(state: GraphState) -> dict:
    msg = state.get("message", "")
    logger.info("[Orchestrator] Received message: %s", msg)
    return {
        "orchestrator_logs": [f"[Orchestrator] Message received: {msg}"],
    }
