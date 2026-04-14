import asyncio
import logging
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

_INITIAL_STATE_TEMPLATE = {
    "intent": {},
    "date_range": {},
    "intent_logs": [],
    "date_logs": [],
    "orchestrator_logs": [],
    "sql_query": "",
    "response": "",
    "logs": [],
    # WRITE flow fields
    "current_data": {},
    "payload_logs": [],
    "reply": "",
}

# Map each node's log key so the frontend receives "logs" consistently
_NODE_LOG_KEYS = {
    "orchestrator":        "orchestrator_logs",
    "intent_classifier":   "intent_logs",
    "date_extractor":      "date_logs",
    "sql_generator":       "logs",
    "payload_filler_node": "payload_logs",
}


def _build_state(message: str, history: list, metadata: dict) -> dict:
    # Convert Pydantic Message objects to plain dicts so nodes can do msg["role"]
    history_dicts = []
    for m in (history or []):
        if hasattr(m, "model_dump"):
            history_dicts.append(m.model_dump())
        elif hasattr(m, "dict"):
            history_dicts.append(m.dict())
        elif isinstance(m, dict):
            history_dicts.append(m)

    return {
        **_INITIAL_STATE_TEMPLATE,
        "message":    message,
        "history":    history_dicts,
        "company_id": str(metadata.get("company_id", "1")),
        "session_id": str(metadata.get("session_id", "")),
    }


async def process_message(message: str, history: list, metadata: dict) -> dict:
    """Invoke the full pipeline and return results all at once (non-streaming)."""
    from services.graph.graph import pipeline

    state = _build_state(message, history, metadata)
    logger.info("[MessageService] Invoking pipeline for: %s", message)

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, pipeline.invoke, state)

    # WRITE flow: payload_filler_node sets "reply"; return it directly
    reply = result.get("reply", "")
    if reply:
        return {
            "response": reply,
            "data": {
                "intent":       result.get("intent"),
                "current_data": result.get("current_data", {}),
                "logs":         result.get("payload_logs", []),
            },
        }

    # READ flow: sql_generator sets "sql_query"
    sql = result.get("sql_query") or ""
    return {
        "response": sql or "Could not generate SQL for this query.",
        "data": {
            "intent":     result.get("intent"),
            "date_range": result.get("date_range"),
            "sql_query":  sql,
            "logs":       result.get("logs", []),
        },
    }


async def stream_message(
    message: str, history: list, metadata: dict
) -> AsyncGenerator[dict, None]:
    """
    Stream pipeline progress node-by-node using LangGraph's astream().
    Yields one event dict per node as soon as that node completes.

    Event shape:
        { "node": str, "logs": [str], "intent"?: dict,
          "date_range"?: dict, "sql_query"?: str,
          "reply"?: str, "current_data"?: dict }
    """
    from services.graph.graph import pipeline

    state = _build_state(message, history, metadata)
    logger.info("[MessageService] Streaming pipeline for: %s", message)

    async for chunk in pipeline.astream(state):
        for node_name, updates in chunk.items():
            event: dict = {"node": node_name}

            # Attach the logs for this node
            log_key = _NODE_LOG_KEYS.get(node_name)
            if log_key and updates.get(log_key):
                event["logs"] = updates[log_key]

            # Attach node-specific payload
            if "intent" in updates and updates["intent"]:
                event["intent"] = updates["intent"]
            if "date_range" in updates and updates["date_range"]:
                event["date_range"] = updates["date_range"]
            if "sql_query" in updates and updates["sql_query"]:
                event["sql_query"] = updates["sql_query"]
            # WRITE flow fields
            if "reply" in updates and updates["reply"]:
                event["reply"] = updates["reply"]
            if "current_data" in updates and updates["current_data"]:
                event["current_data"] = updates["current_data"]

            yield event
