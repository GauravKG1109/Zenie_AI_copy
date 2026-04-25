from typing import TypedDict, List, Optional


class GraphState(TypedDict):
    message: str
    history: list
    company_id: str
    session_id: str

    # ── Intent classifier ────────────────────────────────────────────────────
    intent: dict                  # top-1 full dict (set by intent_classifier; updated by orchestrator)
    intent_type: Optional[str]
    candidate_intents: List[dict] # top-5 slim dicts: {intent_code, description, action_type, similarity}

    # ── Orchestrator ─────────────────────────────────────────────────────────
    orchestrator_intent_code: str  # routing decision this turn
    orchestrator_reply: str        # direct reply to user when code == NONE
    active_intent: dict            # slim intent persisted across turns (loaded by message_service)

    # ── Downstream READ pipeline ──────────────────────────────────────────────
    date_range: dict
    sql_query: str
    query_result: dict
    response: str
    logs: List[str]

    # ── Downstream WRITE pipeline ─────────────────────────────────────────────
    current_data: dict
    write_notification: Optional[dict]  # set on WRITE confirmation: {intent_code, intent_name, payload, status}
    reply: str

    # ── Logs per node ─────────────────────────────────────────────────────────
    intent_logs: List[str]
    date_logs: List[str]
    orchestrator_logs: List[str]
    payload_logs: List[str]
