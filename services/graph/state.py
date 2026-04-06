from typing import TypedDict, List, Optional


class GraphState(TypedDict):
    message: str
    history: list
    company_id: str
    session_id: str
    intent: dict          # set by intent_classifier
    date_range: dict      # set by date_extractor
    intent_logs: List[str]
    date_logs: List[str]
    orchestrator_logs: List[str]
    sql_query: str        # set by sql_generator
    response: str         # final response text
    logs: List[str]       # merged logs assembled in sql_generator
