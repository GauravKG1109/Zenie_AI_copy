"""
sql_generator.py — LangGraph node: generates validated SQL.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  !! DO NOT replace create_sql_query_chain with llm.invoke() !!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHY create_sql_query_chain (from langchain_classic) MUST be used:

  1. PURPOSE-BUILT — it is LangChain's dedicated SQL-generation primitive.
     It automatically injects table_info (schema) from the database object,
     handles the SQLQuery: stop signal so the LLM outputs raw SQL only, and
     wraps everything in a typed Runnable[SQLInput, str] chain.

  2. SCHEMA INJECTION — the chain calls db.get_table_info() and injects it
     into the prompt automatically. A raw llm.invoke() call would require us
     to manually re-implement this every time, making it fragile.

  3. STOP SEQUENCE — create_sql_query_chain binds stop=["\nSQLResult:"] to
     the LLM, cutting off the model before it hallucinates fake query results.
     A direct llm.invoke() call would not include this stop sequence.

  4. EXTENSIBILITY — swapping the underlying LLM (e.g. GPT → Claude → local)
     only requires changing the llm= argument. The chain interface stays the
     same, so no prompt wiring needs to change.

  5. FUTURE COMPATIBILITY — as LangChain evolves, create_sql_query_chain will
     receive improvements (few-shot, dialect-aware prompts, etc.) for free.

  The MockSQLDatabase provides db.get_table_info() (schema) and db.dialect
  without needing a real DB connection, which is exactly how we feed view
  metadata to the chain without leaking credentials or hitting the database.
"""

import logging
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_classic.chains.sql_database.query import create_sql_query_chain
from langchain_community.utilities import SQLDatabase
from langchain_core.prompts import PromptTemplate

load_dotenv()

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
            raise RuntimeError("ANTHROPIC_API_KEY is not set. Check your .env file.")
        _llm = ChatAnthropic(
            model="claude-sonnet-4-20250514",
            temperature=0,
            api_key=api_key,
        )
    return _llm


# ── MockSQLDatabase ───────────────────────────────────────────────────────────
# Provides schema info to create_sql_query_chain without a real DB connection.
# The chain only calls get_table_info() and dialect — both are implemented here.

class MockSQLDatabase(SQLDatabase):
    def __init__(self, views_meta: dict):
        self._views_meta = views_meta   # { view_name: { description, columns, join_keys, ... } }

    @property
    def dialect(self) -> str:
        return "SQL"

    def get_usable_table_names(self) -> list[str]:
        return list(self._views_meta.keys())

    def get_table_info(self, table_names: list[str] = None) -> str:
        targets = table_names or list(self._views_meta.keys())
        blocks = []
        for view_name in targets:
            meta = self._views_meta.get(view_name)
            if not meta:
                continue
            col_defs = "\n".join(
                f"  {c['name']} ({c['type']}) -- {c['description']}"
                for c in meta["columns"]
            )
            join_keys = ", ".join(meta.get("join_keys", []))
            blocks.append(
                f"VIEW {view_name}\n"
                f"  PURPOSE: {meta.get('description', '')}\n"
                f"  JOIN KEYS (shared with other views): {join_keys or 'none'}\n"
                f"  COLUMNS:\n{col_defs}"
            )
        return "\n\n".join(blocks)

    def run(self, command: str, fetch: str = "all", **kwargs) -> str:
        return "(Read-only mock — query not executed)"

    def run_no_throw(self, command: str, fetch: str = "all", **kwargs) -> str:
        return self.run(command, fetch)

    @property
    def table_info(self) -> str:
        return self.get_table_info()


# ── Prompt template ───────────────────────────────────────────────────────────
# Required variables for create_sql_query_chain: input, top_k, table_info.
# dialect is optional (included here for completeness).
# Our extra variables: intent_name, intent_desc, mandatory_where,
#                      join_guidance, optional_filters.
#
# Note: the chain appends "\nSQLQuery: " to {input} automatically, so the LLM
# outputs only the SQL starting right after that marker.

_SQL_PROMPT = PromptTemplate(
    input_variables=[
        "input", "top_k", "table_info", "dialect",
        "intent_name", "intent_desc",
        "mandatory_where", "join_guidance", "optional_filters",
    ],
    template="""\
You are an expert {dialect} developer for a financial ERP system.
Your task: write a single correct SQL SELECT statement.

━━━ INTENT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Name:        {intent_name}
Description: {intent_desc}

━━━ AVAILABLE VIEWS (use ONLY these) ━━━━━━━━━━━━━━━━━━━━━━━━━━━
{table_info}

{join_guidance}

━━━ MANDATORY WHERE CLAUSES ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Always include ALL of these (qualify with alias when JOINing):
{mandatory_where}

{optional_filters}

━━━ RULES ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Output ONLY a single SQL SELECT statement ending with a semicolon.
   No markdown, no explanation, no comments — just the SQL.
2. Use ONLY column names listed in the views above.
3. Always include all MANDATORY WHERE CLAUSES.
4. Use GROUP BY, ORDER BY, HAVING, LIMIT, and window functions as needed.
5. For top-N queries use LIMIT {top_k} or ROW_NUMBER() as appropriate.
6. JOIN views only when the query genuinely needs columns from both.
   If one view is sufficient, do not force a JOIN.
7. If a user filter (e.g. "overdue", "not due") has no exact column,
   use the closest date column and add a SQL comment explaining the mapping.

{input}""",
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_join_guidance(views_meta: dict) -> str:
    if len(views_meta) < 2:
        return ""
    view_names = list(views_meta.keys())
    all_keys = set()
    for meta in views_meta.values():
        all_keys.update(meta.get("join_keys", []))
    shared = [
        k for k in all_keys
        if sum(1 for m in views_meta.values() if any(c["name"] == k for c in m["columns"])) > 1
    ]
    if shared:
        v0, v1 = view_names[0], view_names[1]
        on_clause = " AND ".join(f"{v0}.{k} = {v1}.{k}" for k in shared)
        return (
            f"━━━ JOIN GUIDANCE ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  Shared join keys: {', '.join(shared)}\n"
            f"  Example: FROM {v0} hdr\n"
            f"           JOIN {v1} ln ON {on_clause}"
        )
    return (
        "━━━ JOIN GUIDANCE ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  Multiple views available. Inspect columns above for shared keys."
    )


def _build_optional_filter_instruction(optional_params: str) -> str:
    raw = (optional_params or "").strip()
    if not raw or raw.lower() in ("none", "nan", ""):
        return ""
    params = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    if not params:
        return ""
    param_list = ", ".join(f'"{p}"' for p in params)
    return (
        f"OPTIONAL FILTERS: Supported filters are {param_list}.\n"
        "  Add a WHERE/HAVING clause only if the user mentioned a specific value.\n"
        "  If not mentioned, omit the filter — no placeholders."
    )


def _build_mandatory_where(views_meta: dict, company_id: str,
                            date_from: str, date_to: str) -> str:
    lines = [f"  company_id = {company_id}"]
    if date_from and date_to:
        date_cols = list(dict.fromkeys(
            m["date_column"] for m in views_meta.values() if m.get("date_column")
        ))
        for col in date_cols:
            lines.append(f"  {col} BETWEEN '{date_from}' AND '{date_to}'")
    else:
        lines.append("  (No date range — omit date filter unless the question implies one)")
    return "\n".join(lines)


def _validate_sql(sql: str, views: list[str]) -> tuple[bool, str | None]:
    clean = sql.strip().rstrip(";").strip()
    if not clean.upper().startswith("SELECT"):
        return False, "Query does not start with SELECT"
    if not any(v.lower() in clean.lower() for v in views):
        return False, f"None of the expected views {views} found in query"
    for kw in ["DROP", "DELETE", "INSERT", "UPDATE", "TRUNCATE", "ALTER", "CREATE"]:
        if re.search(rf"\b{kw}\b", clean.upper()):
            return False, f"Forbidden keyword: {kw}"
    return True, None


def _clean_sql(raw: str) -> str:
    """Strip markdown fences and extract the SELECT statement."""
    raw = re.sub(r"```[a-zA-Z]*\n?", "", raw).strip()
    raw = re.sub(r"\n?```$", "", raw).strip()
    # Strip "SQLQuery:" prefix if the chain leaks it
    raw = re.sub(r"^SQLQuery\s*:\s*", "", raw, flags=re.IGNORECASE).strip()
    m = re.search(r"(SELECT\b.+?;)", raw, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r"(SELECT\b.+)", raw, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return raw


# ── Node ──────────────────────────────────────────────────────────────────────

def sql_generator_node(state: GraphState) -> dict:
    intent     = state.get("intent", {})
    date_range = state.get("date_range", {})
    company_id = state.get("company_id", "1")
    message    = state.get("message", "")

    merged_logs = (
        state.get("orchestrator_logs", [])
        + state.get("intent_logs", [])
        + state.get("date_logs", [])
    )

    # ── Pre-written SQL — skip generation entirely ────────────────────────────
    sql_query_manual = intent.get("sql_query_manual", "").strip()
    if sql_query_manual:
        log_line = "[SQLGenerator] Using pre-written SQL from Intent_file.xlsx"
        logger.info(log_line)
        return {
            "sql_query": sql_query_manual,
            "response":  sql_query_manual,
            "logs":      merged_logs + [log_line],
        }

    # ── Resolve views ─────────────────────────────────────────────────────────
    views: list[str] = intent.get("views", [])
    intent_code = intent.get("intent_code", "UNKNOWN")

    if not views:
        err = f"[SQLGenerator] No views mapped for intent {intent_code}"
        logger.warning(err)
        return {"sql_query": "", "response": err, "logs": merged_logs + [err]}

    views_meta: dict = {}
    missing: list   = []
    for v in views:
        meta = VIEW_METADATA.get(v)
        if meta:
            views_meta[v] = meta
        else:
            missing.append(v)

    if missing:
        logger.warning("[SQLGenerator] Views not in view_metadata.py: %s", missing)
    if not views_meta:
        err = f"[SQLGenerator] No metadata found for any of {views}"
        logger.warning(err)
        return {"sql_query": "", "response": err, "logs": merged_logs + [err]}

    # ── Build prompt variables ────────────────────────────────────────────────
    date_from = date_to = None
    if date_range and date_range.get("primary"):
        date_from = date_range["primary"]["start"]
        date_to   = date_range["primary"]["end"]

    mandatory_where = _build_mandatory_where(views_meta, company_id, date_from, date_to)
    join_guidance   = _build_join_guidance(views_meta)
    optional_filter = _build_optional_filter_instruction(intent.get("optional_parameters", ""))

    logger.info(
        "[SQLGenerator] create_sql_query_chain for views: %s", list(views_meta.keys())
    )

    # ── create_sql_query_chain — the only permitted SQL generation method ─────
    # See module docstring for why this must not be replaced with llm.invoke().
    try:
        db    = MockSQLDatabase(views_meta)
        chain = create_sql_query_chain(llm=_get_llm(), db=db, prompt=_SQL_PROMPT, k=10)
        raw   = chain.invoke({
            "question":        message,
            "intent_name":     intent.get("intent_name", ""),
            "intent_desc":     intent.get("description", ""),
            "mandatory_where": mandatory_where,
            "join_guidance":   join_guidance,
            "optional_filters": optional_filter,
        })
        logger.debug("[SQLGenerator] Raw chain output: %r", str(raw)[:400])
    except Exception as exc:
        err = f"[SQLGenerator] Chain error: {exc}"
        logger.error(err)
        return {"sql_query": "", "response": err, "logs": merged_logs + [err]}

    sql = _clean_sql(str(raw))

    valid, reason = _validate_sql(sql, list(views_meta.keys()))
    if not valid:
        err = f"[SQLGenerator] Validation failed: {reason}. Raw: {str(raw)[:200]}"
        logger.warning(err)
        return {"sql_query": "", "response": err, "logs": merged_logs + [err]}

    log_line = (
        f"[SQLGenerator] SQL generated via create_sql_query_chain "
        f"for views: [{', '.join(views_meta.keys())}]"
    )
    logger.info(log_line)
    return {
        "sql_query": sql,
        "response":  sql,
        "logs":      merged_logs + [log_line],
    }
