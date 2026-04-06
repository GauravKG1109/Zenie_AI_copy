import asyncio
import logging

logger = logging.getLogger(__name__)


async def process_message(message: str, history: list, metadata: dict) -> dict:
    """
    Entry point for message processing.
    Invokes the LangGraph pipeline and returns the result.
    """
    # Import here to avoid circular imports and allow FastAPI to start
    # before the heavy SentenceTransformer model finishes loading
    from services.graph.graph import pipeline

    initial_state = {
        "message": message,
        "history": history,
        "company_id": str(metadata.get("company_id", "1")),
        "session_id": str(metadata.get("session_id", "")),
        "intent": {},
        "date_range": {},
        "intent_logs": [],
        "date_logs": [],
        "orchestrator_logs": [],
        "sql_query": "",
        "response": "",
        "logs": [],
    }

    logger.info("[MessageService] Invoking pipeline for: %s", message)

    # Run the synchronous graph.invoke in a thread so we don't block the event loop
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, pipeline.invoke, initial_state)

    sql = result.get("sql_query") or ""
    response_text = sql if sql else "Could not generate SQL for this query."

    return {
        "response": response_text,
        "data": {
            "intent": result.get("intent"),
            "date_range": result.get("date_range"),
            "sql_query": sql,
            "logs": result.get("logs", []),
        },
    }
