"""
LLM_payload_filler.py — LangGraph node for WRITE intents.

Collects mandatory form fields via multi-turn conversation before making an API call.
Supports three LLMs — change ACTIVE_MODEL below to switch:

  ACTIVE_MODEL = "claude"   # Claude 3 Haiku  (default)
  ACTIVE_MODEL = "gpt"      # GPT-4.1 Nano
  ACTIVE_MODEL = "qwen"     # Qwen 2.5 7B via Ollama (local)

For Qwen: requires `ollama serve` running and `ollama pull qwen2.5:7b` done first.
For local storage of session state, a module-level dict is used (see _session_store).
(In future, replace with Redis or a DB for production use.)
"""

import json
import logging
import re
from typing import Optional

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from services.graph.state import GraphState
from core.config import DUMMY_FIELDS, DUMMY_APIS
from core.storage import FieldStorage

load_dotenv()
logger = logging.getLogger(__name__)

# ── LLM selection — change this to switch models ──────────────────────────────
ACTIVE_MODEL = "claude"   # options: "claude" | "gpt" | "qwen"

# ── Session-scoped FieldStorage ────────────────────────────────────────────────
# Key: (session_id, intent_code) → FieldStorage instance
# Survives across HTTP requests within the same server process.
# Lost on server restart — acceptable for local dev; swap for Redis in production.
_session_store: dict = {}


# ── Qwen 2.5 7B via Ollama ────────────────────────────────────────────────────

class SLMModel:
    """
    Wrapper around the Ollama `chat` API for Qwen 2.5 7B (or any Ollama model).
    Adapted from data/reference_codes/model.py.

    The ollama import is deferred so the server starts fine even if Ollama
    is not installed — it only fails when model_name == "qwen" is actually used.
    """
    def __init__(self, model_name: str = "qwen2.5:7b", temperature: float = 0.3):
        self.model_name    = model_name
        self.temperature   = temperature
        self.last_response: Optional[str] = None
        self.logs: list = []

    def chat_with_system_prompt(self, user_message: str, system_prompt: str) -> str:
        """
        Sends a message with a system prompt to Ollama (no chat history —
        history is flattened into user_message by the caller).
        Returns the response text as a string.
        """
        from ollama import chat  # deferred — only fails if ollama not installed
        import time

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ]
        start = time.time()
        response = chat(model=self.model_name, messages=messages, stream=False)
        total_time = time.time() - start

        self.last_response = response["message"]["content"]
        self.logs.append(f"[SLMModel] RESPONSE TIME: {total_time:.2f}s")
        return self.last_response

    def parse_last_response(self) -> Optional[dict]:
        """Extracts JSON from the last response using regex fallback."""
        if self.last_response is None:
            return None
        match = re.search(r"\{.*\}", self.last_response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return None

    def get_logs(self) -> list:
        return self.logs


# ── LLM factory ───────────────────────────────────────────────────────────────

def get_llm(model_name: str):
    """Returns the LLM instance for the given model name."""
    if model_name == "claude":
        return ChatAnthropic(model="claude-haiku-4-5-20251001", temperature=0.3)
    elif model_name == "gpt":
        return ChatOpenAI(model="gpt-4.1-nano", temperature=0.3)
    elif model_name == "qwen":
        return SLMModel(model_name="qwen2.5:7b", temperature=0.3)
    raise ValueError(f"Unknown model: {model_name!r}. Valid options: 'claude', 'gpt', 'qwen'.")


# ── System prompt builder ──────────────────────────────────────────────────────

def build_system_prompt(
    intent_name: str,
    mandatory_fields: dict,
    optional_fields: Optional[dict],
    current_data: dict,
) -> str:
    filled_mandatory  = []
    missing_mandatory = []

    for field in mandatory_fields:
        val = current_data.get(field)
        if val is not None:
            filled_mandatory.append(f"{field}: {val}")
        else:
            missing_mandatory.append(field)

    filled_block  = "\n".join(filled_mandatory)  if filled_mandatory  else "No mandatory fields filled yet."
    missing_block = "\n".join(missing_mandatory) if missing_mandatory else "All mandatory fields are filled. Ask for FINAL CONFIRMATION."

    return f"""You are a helpful assistant for filling the payload for API call based on the user message and the intent.
The intent of the user is: {intent_name}
The mandatory fields data collected till now is:
{filled_block}
The mandatory fields still missing are:
{missing_block}

___Response Format___
You must respond ONLY with a valid JSON. No markdown, no code blocks, no text outside JSON.
JSON STRUCTURE (exactly as shown):
{{
    "extracted_fields": {{
        "field_name1": "extracted_value1",
        "field_name2": "extracted_value2"
    }},
    "reply": "Your natural language reply to the user (ask for missing fields, or confirm if all filled)"
}}

==== EXAMPLES ====

Example 1 — Missing Fields: customer_name, invoice_number, posting_date
User: "Create an invoice for Aman"
Response:
{{
    "extracted_fields": {{
        "customer_name": "Aman"
    }},
    "reply": "Got it! Could you provide the invoice number and posting date?"
}}

Example 2 — Missing Fields: quantity, amount
User: "I want 5 units at $20 each"
Response:
{{
    "extracted_fields": {{
        "quantity": "5",
        "amount": "20"
    }},
    "reply": "Noted! I have the quantity and amount. Could you also provide the invoice number and posting date?"
}}

Remember: extracted_fields is always an OBJECT with key:value pairs, NEVER a list."""


# ── Robust JSON extraction ─────────────────────────────────────────────────────

def _extract_json(text: str) -> Optional[dict]:
    """
    Tries strict json.loads first, then strips markdown fences, then regex.
    Handles LLMs that wrap JSON in code blocks or add surrounding prose.
    """
    # 1. Direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # 2. Strip markdown fences then retry
    cleaned = re.sub(r"```[a-zA-Z]*\n?", "", text).strip()
    cleaned = re.sub(r"\n?```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        pass

    # 3. Regex: grab first {...} block (may span multiple lines)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, TypeError):
            pass

    return None


# ── Core message processor ────────────────────────────────────────────────────

def process_message(
    message: str,
    intent_name: str,
    chat_history: list = None,
    current_data: dict = None,
) -> str:
    """
    Calls the active LLM with the system prompt and conversation history.
    Always returns a plain string (the model's text output).
    """
    if current_data is None:
        current_data = {}
    if chat_history is None:
        chat_history = []

    system_prompt = build_system_prompt(
        intent_name=intent_name,
        mandatory_fields=DUMMY_FIELDS,
        optional_fields=None,
        current_data=current_data,
    )

    llm = get_llm(ACTIVE_MODEL)

    # ── Qwen path (SLMModel — Ollama, no LangChain message objects) ────────────
    if isinstance(llm, SLMModel):
        # Flatten history into a single user message string (Ollama doesn't need
        # the same message-list format for our field-collection use case)
        history_text = ""
        for msg in chat_history:
            role    = msg.get("role", "") if isinstance(msg, dict) else getattr(msg, "role", "")
            content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
            if role == "user":
                history_text += f"User: {content}\n"
            elif role == "assistant":
                history_text += f"Assistant: {content}\n"
        full_user_message = (history_text + f"User: {message}").strip()
        raw = llm.chat_with_system_prompt(full_user_message, system_prompt)
        return raw

    # ── LangChain path (Claude / GPT) ─────────────────────────────────────────
    messages = [SystemMessage(content=system_prompt)]
    for msg in chat_history:
        role    = msg.get("role", "") if isinstance(msg, dict) else getattr(msg, "role", "")
        content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
    messages.append(HumanMessage(content=message))

    response = llm.invoke(messages)
    # BUG FIX: llm.invoke() returns an AIMessage object — extract .content
    return response.content


# ── LangGraph node entrypoint ─────────────────────────────────────────────────

def payload_filler_node(state: GraphState) -> dict:
    """
    LangGraph node for WRITE intents.
    Signature must be exactly (state: GraphState) -> dict — LangGraph passes only state.

    Collects form fields across multiple conversation turns using session-scoped
    FieldStorage (_session_store keyed by (session_id, intent_code)).
    """
    # BUG FIX: removed extra params (session_id, company_id, message, intent) —
    # LangGraph only passes `state`; extra params cause a TypeError at graph compile time.
    message    = state.get("message", "")
    session_id = state.get("session_id", "default")

    # BUG FIX: intent is a dict, not a string
    intent      = state.get("intent", {})
    intent_name = intent.get("intent_name", "unknown")
    intent_code = intent.get("intent_code", "unknown")

    # BUG FIX: correct state key is "history", not "chathistory"
    chat_history = state.get("history", [])

    # ── Session-scoped FieldStorage ───────────────────────────────────────────
    store_key = (session_id, intent_code)
    if store_key not in _session_store:
        _session_store[store_key] = FieldStorage(required_fields=list(DUMMY_FIELDS.keys()))
        logger.info("[PayloadFiller] New session: session_id=%s intent=%s", session_id, intent_code)

    storage = _session_store[store_key]

    # Merge any previously collected data passed through state (multi-turn continuity)
    for field, value in state.get("current_data", {}).items():
        if value is not None:
            storage.set_data(field, value)

    current_data = storage.get_data()

    # ── Call LLM ──────────────────────────────────────────────────────────────
    raw_response = process_message(
        message=message,
        intent_name=intent_name,
        chat_history=chat_history,
        current_data=current_data,
    )
    logger.debug("[PayloadFiller] Raw LLM response: %.300s", raw_response)

    # ── Parse JSON with robust fallback ───────────────────────────────────────
    # BUG FIX: raw_response is now a string; old code did json.loads(AIMessage_obj)
    parsed = _extract_json(raw_response)

    if parsed:
        extracted_fields = parsed.get("extracted_fields", {})
        reply = parsed.get("reply", "")
    else:
        extracted_fields = {}
        reply = "I had trouble parsing that response. Could you please try again?"
        logger.warning("[PayloadFiller] Could not extract JSON from response: %.200s", raw_response)

    # ── Update storage ────────────────────────────────────────────────────────
    new_logs = []
    for field, value in extracted_fields.items():
        _, msg = storage.update_field(field, value)
        new_logs.append(f"[PayloadFiller] {msg}: {field} = {value}")

    # Evict session when all fields are complete
    if storage.is_complete():
        logger.info("[PayloadFiller] All fields complete — clearing session store for %s/%s", session_id, intent_code)
        del _session_store[store_key]

    log_line = (
        f"[PayloadFiller] intent={intent_name} | "
        f"extracted={list(extracted_fields.keys())} | "
        f"missing={storage.get_missing_fields()}"
    )
    logger.info(log_line)

    return {
        "current_data": storage.get_data(),
        "reply":        reply,
        "payload_logs": [log_line] + new_logs,
    }
