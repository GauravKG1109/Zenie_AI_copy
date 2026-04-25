import asyncio
import json
import logging
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

# ── active_intent persistence ─────────────────────────────────────────────────
# Keyed by session_id. Each value is the slim active_intent dict written by the
# orchestrator at the end of a turn so the next turn can load it back into state.
_active_intent_store: dict = {}

_INITIAL_STATE_TEMPLATE = {
    "intent": {},
    "candidate_intents": [],
    "orchestrator_intent_code": "",
    "orchestrator_reply": "",
    # active_intent is NOT in the template — it is loaded per-session in _build_state
    "date_range": {},
    "intent_logs": [],
    "date_logs": [],
    "orchestrator_logs": [],
    "sql_query": "",
    "query_result": {},
    "response": "",
    "logs": [],
    "current_data": {},
    "payload_logs": [],
    "reply": "",
    "write_notification": None,
}

# Map each node's log key so the frontend receives "logs" consistently
_NODE_LOG_KEYS = {
    "intent_classifier":      "intent_logs",
    "orchestrator":           "orchestrator_logs",
    "end_with_reply":         "orchestrator_logs",
    "get_knowledgebase_node": "payload_logs",
    "date_extractor":         "date_logs",
    "sql_generator":          "logs",
    "payload_filler_node":    "payload_logs",
}


def _build_state(message: str, history: list, metadata: dict) -> dict:
    history_dicts = []
    for m in (history or []):
        if hasattr(m, "model_dump"):
            history_dicts.append(m.model_dump())
        elif hasattr(m, "dict"):
            history_dicts.append(m.dict())
        elif isinstance(m, dict):
            history_dicts.append(m)

    session_id    = str(metadata.get("session_id", ""))
    active_intent = _active_intent_store.get(session_id, {})

    return {
        **_INITIAL_STATE_TEMPLATE,
        "message":      message,
        "history":      history_dicts,
        "company_id":   str(metadata.get("company_id", "1")),
        "session_id":   session_id,
        "active_intent": active_intent,   # loaded from store — not from template
    }


def _persist_active_intent(session_id: str, result_or_updates: dict) -> None:
    """Save or clear the orchestrator's active_intent for this session."""
    new_active = result_or_updates.get("active_intent") or {}
    if new_active.get("intent_code"):
        _active_intent_store[session_id] = new_active
    else:
        _active_intent_store.pop(session_id, None)


def _maybe_clear_write_session(session_id: str) -> None:
    """
    If the orchestrator switched away from a WRITE intent, evict the payload-filler
    session so the user starts fresh on the next WRITE request.
    """
    active = _active_intent_store.get(session_id, {})
    if active.get("action_type", "").upper() != "WRITE":
        try:
            from services.graph.nodes.LLM_payload_filler import clear_write_session
            clear_write_session(session_id)
        except Exception:
            pass


def _trigger_dummy_write_api(write_notif: dict, session_id: str) -> None:
    """
    Simulates a POST to the ERP API when a WRITE intent is confirmed.
    Logs the payload. Replace with a real HTTP call in production.
    """
    logger.info(
        "[WriteAPI] DUMMY API called | session=%s | intent=%s | payload=%s",
        session_id,
        write_notif.get("intent_code"),
        json.dumps(write_notif.get("payload", {}), default=str),
    )


async def process_message(message: str, history: list, metadata: dict) -> dict:
    """Invoke the full pipeline and return results all at once (non-streaming)."""
    from services.graph.graph import pipeline

    state      = _build_state(message, history, metadata)
    session_id = state["session_id"]
    logger.info("[MessageService] Invoking pipeline for: %s", message)

    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, pipeline.invoke, state)

    # Persist active_intent and clean up stale WRITE sessions
    _persist_active_intent(session_id, result)
    _maybe_clear_write_session(session_id)

    # Fire dummy write API if WRITE was confirmed
    write_notif = result.get("write_notification")
    if write_notif:
        _trigger_dummy_write_api(write_notif, session_id)

    # Reply path (WRITE, KB, or NONE via orchestrator)
    reply = result.get("reply", "")
    if reply:
        return {
            "response": reply,
            "data": {
                "intent":             result.get("intent"),
                "current_data":       result.get("current_data", {}),
                "write_notification": write_notif,
                "logs":               result.get("payload_logs", []) or result.get("orchestrator_logs", []),
            },
        }

    # READ path
    sql = result.get("sql_query") or ""
    return {
        "response": sql or "Could not generate SQL for this query.",
        "data": {
            "intent":       result.get("intent"),
            "date_range":   result.get("date_range"),
            "sql_query":    sql,
            "query_result": result.get("query_result", {}),
            "logs":         result.get("logs", []),
        },
    }


async def stream_message(
    message: str, history: list, metadata: dict
) -> AsyncGenerator[dict, None]:
    """
    Stream pipeline progress node-by-node using LangGraph's astream().
    Yields one event dict per node as soon as that node completes.

    Event shape:
        { "node": str, "logs"?: [str], "intent"?: dict,
          "date_range"?: dict, "sql_query"?: str, "query_result"?: dict,
          "reply"?: str, "current_data"?: dict, "write_notification"?: dict }
    """
    from services.graph.graph import pipeline

    state      = _build_state(message, history, metadata)
    session_id = state["session_id"]
    logger.info("[MessageService] Streaming pipeline for: %s", message)

    # Track active_intent updates as they stream in — persisted after loop ends
    latest_active_intent: dict = {}
    write_notif_seen: dict | None = None

    async for chunk in pipeline.astream(state):
        for node_name, updates in chunk.items():
            event: dict = {"node": node_name}

            # Track active_intent for post-stream persistence (not sent to frontend)
            if "active_intent" in updates and updates["active_intent"]:
                latest_active_intent = updates["active_intent"]

            # Logs
            log_key = _NODE_LOG_KEYS.get(node_name)
            if log_key and updates.get(log_key):
                event["logs"] = updates[log_key]

            # Shared payload fields
            if "intent" in updates and updates["intent"]:
                event["intent"] = updates["intent"]
            if "date_range" in updates and updates["date_range"]:
                event["date_range"] = updates["date_range"]
            if "sql_query" in updates and updates["sql_query"]:
                event["sql_query"] = updates["sql_query"]
            if "query_result" in updates and updates["query_result"]:
                event["query_result"] = updates["query_result"]

            # Reply (WRITE path, KB stub, and NONE path via end_with_reply)
            if "reply" in updates and updates["reply"]:
                event["reply"] = updates["reply"]
            if "current_data" in updates and updates["current_data"]:
                event["current_data"] = updates["current_data"]

            # Write notification (WRITE confirmation)
            if "write_notification" in updates and updates["write_notification"]:
                event["write_notification"] = updates["write_notification"]
                write_notif_seen = updates["write_notification"]

            yield event

    # ── Post-stream housekeeping ──────────────────────────────────────────────
    _persist_active_intent(session_id, {"active_intent": latest_active_intent})
    _maybe_clear_write_session(session_id)
    if write_notif_seen:
        _trigger_dummy_write_api(write_notif_seen, session_id)
