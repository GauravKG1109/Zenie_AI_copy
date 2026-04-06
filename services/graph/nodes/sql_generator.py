"""
sql_generator.py — LangGraph node that uses create_sql_agent with a MockSQLDatabase
backed by view_metadata.py, so no real DB connection is needed.

The intent_classifier has already resolved which view to use from Intent_file.xlsx.
We feed only that view's column metadata to the agent, keeping token usage minimal.
"""

import logging
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_community.utilities import SQLDatabase

load_dotenv()

# ── Import view metadata ──────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
from data.view_metadata import VIEW_METADATA

from services.graph.state import GraphState

logger = logging.getLogger(__name__)

_llm = None


def _get_llm() -> ChatAnthropic:
    global _llm
    if _llm is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Check your .env file."
            )
        _llm = ChatAnthropic(
            model="claude-sonnet-4-20250514",
            temperature=0,
            api_key=api_key,
        )
    return _llm


# ── MockSQLDatabase ───────────────────────────────────────────────────────────

class MockSQLDatabase(SQLDatabase):
    """
    A minimal SQLDatabase stand-in backed by view_metadata.py.
    Provides schema info to create_sql_agent without a real DB connection.
    """

    def __init__(self, view_name: str, meta: dict):
        # Do NOT call super().__init__() — it requires a DB URL.
        # We override every method the toolkit queries.
        self._view_name = view_name
        self._meta = meta

    @property
    def dialect(self) -> str:
        return "sql"

    def get_usable_table_names(self) -> list[str]:
        return [self._view_name]

    def get_table_info(self, table_names: list[str] = None) -> str:
        cols = self._meta["columns"]
        col_defs = ",\n  ".join(
            f"{c['name']} {c['type']}  -- {c['description']}"
            for c in cols
        )
        return f"CREATE VIEW {self._view_name} (\n  {col_defs}\n);"

    def run(self, command: str, fetch: str = "all", **kwargs) -> str:
        # Agent may attempt to run the query; we return a placeholder.
        return "(Read-only mock: SQL generation only — query not executed)"

    def run_no_throw(self, command: str, fetch: str = "all", **kwargs) -> str:
        return self.run(command, fetch)

    @property
    def table_info(self) -> str:
        return self.get_table_info()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_optional_filter_instruction(optional_params: str) -> str:
    raw = (optional_params or "").strip()
    if not raw or raw.lower() in ("none", "nan", ""):
        return ""
    params = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    if not params:
        return ""
    param_list = ", ".join(f'"{p}"' for p in params)
    return (
        f"OPTIONAL FILTERS: The intent supports optional filter(s): {param_list}. "
        "If the user's message contains a specific value for any of these "
        "(e.g., a customer name, product name, or region), add a corresponding "
        "WHERE clause using the most relevant column. "
        "If the user has not specified a value, do NOT include that filter."
    )


def _validate_sql(sql: str, view_name: str) -> tuple[bool, str | None]:
    clean = sql.strip().rstrip(";").strip()
    if not clean.upper().startswith("SELECT"):
        return False, "Query does not start with SELECT"
    if view_name.lower() not in clean.lower():
        return False, f"Expected view '{view_name}' not found in query"
    forbidden = ["DROP", "DELETE", "INSERT", "UPDATE", "TRUNCATE", "ALTER", "CREATE"]
    for kw in forbidden:
        if re.search(rf"\b{kw}\b", clean.upper()):
            return False, f"Forbidden keyword detected: {kw}"
    return True, None


def _extract_sql_from_response(text: str) -> str:
    """Pull the SELECT statement out of agent output."""
    # Try to find a SELECT ... ; block
    m = re.search(r'(SELECT\b.+?;)', text, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    # Fallback: everything from SELECT to end
    m = re.search(r'(SELECT\b.+)', text, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


# ── Node ──────────────────────────────────────────────────────────────────────

def sql_generator_node(state: GraphState) -> dict:
    intent = state.get("intent", {})
    date_range = state.get("date_range", {})
    company_id = state.get("company_id", "1")
    message = state.get("message", "")

    # Merge logs from parallel branches
    merged_logs = (
        state.get("orchestrator_logs", [])
        + state.get("intent_logs", [])
        + state.get("date_logs", [])
    )

    intent_code = intent.get("intent_code", "UNKNOWN")
    view_name = (intent.get("view") or "").strip()

    if not view_name:
        err = f"[SQLGenerator] No view mapped for intent {intent_code}"
        logger.warning(err)
        return {
            "sql_query": "",
            "response": "Could not generate SQL: no view mapped for this intent.",
            "logs": merged_logs + [err],
        }

    meta = VIEW_METADATA.get(view_name)
    if not meta:
        err = f"[SQLGenerator] View '{view_name}' not found in view_metadata.py"
        logger.warning(err)
        return {
            "sql_query": "",
            "response": f"Could not generate SQL: view '{view_name}' metadata missing.",
            "logs": merged_logs + [err],
        }

    # Date range
    date_from = date_to = None
    if date_range and date_range.get("primary"):
        date_from = date_range["primary"]["start"]
        date_to   = date_range["primary"]["end"]

    date_column = meta.get("date_column", "")

    # Build mandatory context for the agent prefix
    mandatory_where = [f"company_id = {company_id}"]
    if date_from and date_to and date_column:
        mandatory_where.append(f"{date_column} BETWEEN '{date_from}' AND '{date_to}'")

    optional_instruction = _build_optional_filter_instruction(
        intent.get("optional_parameters", "")
    )

    prefix = f"""You are a SQL query generator for a financial reporting system.

INTENT: {intent.get('intent_name', '')}
DESCRIPTION: {intent.get('description', '')}

You MUST query ONLY the view: {view_name}
Do NOT use any other table or view.

MANDATORY WHERE CLAUSES (always include ALL of these):
{chr(10).join(f'  - {w}' for w in mandatory_where)}

{optional_instruction}

RULES:
1. Output ONLY a single valid SQL SELECT statement ending with a semicolon.
2. Use ONLY column names available in the view schema provided.
3. Always include ALL mandatory WHERE clauses.
4. Apply GROUP BY and ORDER BY as appropriate.
5. Do not JOIN other tables or views.
6. No markdown, no explanation — just the SQL.
"""

    logger.info("[SQLGenerator] Building agent for view: %s", view_name)

    try:
        db = MockSQLDatabase(view_name, meta)
        toolkit = SQLDatabaseToolkit(db=db, llm=_get_llm())
        from langchain_community.agent_toolkits import create_sql_agent
        agent = create_sql_agent(
            llm=_get_llm(),
            toolkit=toolkit,
            agent_type="zero-shot-react-description",  # ReAct: no API-level tool calls, compatible with Claude
            prefix=prefix,
            verbose=False,
            handle_parsing_errors=True,
        )
        raw_response = agent.run(message)
        sql = _extract_sql_from_response(raw_response)
    except Exception as exc:
        err = f"[SQLGenerator] Agent error: {exc}"
        logger.error(err)
        return {
            "sql_query": "",
            "response": f"SQL generation failed: {exc}",
            "logs": merged_logs + [err],
        }

    valid, reason = _validate_sql(sql, view_name)
    if not valid:
        err = f"[SQLGenerator] Validation failed: {reason}"
        logger.warning(err)
        return {
            "sql_query": "",
            "response": f"SQL validation failed: {reason}",
            "logs": merged_logs + [err],
        }

    log_line = f"[SQLGenerator] SQL generated successfully for view: {view_name}"
    logger.info(log_line)

    return {
        "sql_query": sql,
        "response": sql,
        "logs": merged_logs + [log_line],
    }
