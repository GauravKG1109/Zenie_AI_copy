from typing import TypedDict, List, Optional


class GraphState(TypedDict):
    message: str
    history: list
    company_id: str
    session_id: str
    intent: dict          # set by intent_classifier
    intent_type: Optional[str]  # extracted intent type (read, analyse, report, fill, etc.)
    date_range: dict      # set by date_extractor
    intent_logs: List[str]
    date_logs: List[str]
    orchestrator_logs: List[str]
    sql_query: str        # set by sql_generator
    query_result: dict    # set by sql_generator after executing the SQL query
    response: str         # final response text
    logs: List[str]       # merged logs assembled in sql_generator
    current_data: dict    # collected field values for WRITE flow (payload_filler_node)
    payload_logs: List[str]  # logs from payload_filler_node
    reply: str            # NL reply from payload_filler_node to send back to user
