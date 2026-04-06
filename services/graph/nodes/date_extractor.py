import logging
from services.graph.state import GraphState
from services.date_extractor_lib import extract_dates, serialize_date_result

logger = logging.getLogger(__name__)


def date_extractor_node(state: GraphState) -> dict:
    message = state.get("message", "")
    logger.info("[DateExtractor] Extracting dates from: %s", message)

    result = extract_dates(message)
    serialized = serialize_date_result(result)

    if serialized:
        primary = serialized["primary"]
        if serialized["is_comparison"]:
            sec = serialized["secondary"]
            log_line = (
                f"[DateExtractor] Comparison: {primary['label']} ({primary['start']} → {primary['end']}) "
                f"vs {sec['label']} ({sec['start']} → {sec['end']})"
            )
        else:
            log_line = (
                f"[DateExtractor] Period: {primary['label']} "
                f"({primary['start']} → {primary['end']})"
            )
    else:
        log_line = "[DateExtractor] No date found in message"

    logger.info(log_line)
    return {
        "date_range": serialized or {},
        "date_logs": [log_line],
    }
