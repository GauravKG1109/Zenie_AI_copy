import json
import logging
from anthropic import Anthropic
from services.graph.state import GraphState

logger = logging.getLogger(__name__)
client = Anthropic()

# Reserved intent codes that don't exist in the intent table
INTENT_KB_NODE     = "GET_KNOWLEDGEBASE"
INTENT_NONE        = "NONE"

ORCHESTRATOR_SYSTEM_PROMPT = """
You are the routing orchestrator for Zenie AI, a financial assistant for Global FPO (a finance and accounting company).

You will receive:
- current_message: the latest user message
- chat_history: last 6 messages (user + assistant) for context
- active_intent: the intent currently being processed (intent_code + action_type + description), if any
- candidate_intents: 4-6 freshly extracted probable intents, each with intent_code, description, and action_type (READ / WRITE / ANALYSE)

YOUR ONLY JOB: decide which intent_code to route to.

─── DECISION LOGIC ───────────────────────────────────────────────────────────

1. CONTINUE ACTIVE INTENT → return active_intent.intent_code
   When: active_intent exists AND current message is clearly continuing that flow
   Examples:
   - Active = CREATE_INVOICE (WRITE), user provides field values like "UUID: abc123"
   - Active = CREATE_INVOICE (WRITE), user says "status is draft"
   - Active = any intent, user gives a direct answer to the bot's last question

2. SWITCH TO CANDIDATE INTENT → return that candidate's intent_code
   When: current message is clearly a NEW request, different from active intent
   Choose the best matching candidate based on description fit, not just keyword match.
   Prefer the candidate whose description most closely matches what the user is asking.
   Examples:
   - Active = CREATE_INVOICE, user asks "what is the total of all invoices" → pick READ candidate
   - Active = SALES_REPORT, user says "create an invoice for gaurav" → pick WRITE candidate
   - No active intent → always pick best candidate

3. KNOWLEDGE BASE → return "GET_KNOWLEDGEBASE"
   When: user asks about company policies, how the system works, definitions,
         internal procedures, or anything requiring document lookup
   Examples:
   - "what is Global FPO's invoice policy?"
   - "how does the approval process work?"
   - "what are the payment terms?"

4. NO ROUTING → return "NONE"
   When: message needs no financial processing
   Examples:
   - Greetings: "hi", "hello", "good morning"
   - Closings: "thank you", "bye", "ok great"
   - Completely out of scope: cooking, coding, sports
   Provide a short, friendly response in the message field.
   For out-of-scope topics: politely decline and redirect to finance topics.

─── IMPORTANT RULES ──────────────────────────────────────────────────────────

- NEVER hallucinate an intent_code. Use ONLY the codes from active_intent or candidate_intents, or the reserved codes GET_KNOWLEDGEBASE / NONE.
- When switching intents, always pick from candidate_intents — do not invent codes.
- Confidence below 0.5 on all candidates + no clear active continuation → return NONE and ask user to clarify.
- "thank you", "ok", "got it" after a completed flow → always NONE, never re-trigger any node.

─── OUTPUT FORMAT ────────────────────────────────────────────────────────────

Respond with valid JSON only. No markdown, no explanation, no extra text.

{
    "intent_code": "<intent_code> | GET_KNOWLEDGEBASE | NONE",
    "message": "<if intent_code is NONE: your reply to user. Otherwise: empty string>",
    "confidence": <0.0 to 1.0>,
    "reason": "<one sentence explaining your decision>"
}
"""


def _build_orchestrator_prompt(
    current_message: str,
    chat_history: list,
    active_intent: dict | None,
    candidate_intents: list[dict],
) -> str:

    # Last 6 messages
    recent = chat_history[-6:] if len(chat_history) > 6 else chat_history
    history_text = "\n".join(
        f"  {m.get('role', 'user').upper()}: {m.get('content', '')}"
        for m in recent
    ) or "  (no history)"

    # Active intent block
    if active_intent and active_intent.get("intent_code"):
        active_block = (
            f"  intent_code : {active_intent.get('intent_code')}\n"
            f"  action_type : {active_intent.get('action_type', 'unknown')}\n"
            f"  description : {active_intent.get('description', 'n/a')}"
        )
    else:
        active_block = "  (none)"

    # Candidate intents block
    if candidate_intents:
        candidates_block = "\n".join(
            f"  [{i+1}] intent_code : {c.get('intent_code')}\n"
            f"       action_type : {c.get('action_type', 'unknown')}\n"
            f"       description : {c.get('description', 'n/a')}\n"
            f"       similarity  : {c.get('similarity', 0):.2f}"
            for i, c in enumerate(candidate_intents)
        )
    else:
        candidates_block = "  (none available)"

    return f"""
CURRENT MESSAGE:
  {current_message}

CHAT HISTORY (last 6 messages):
{history_text}

ACTIVE INTENT:
{active_block}

CANDIDATE INTENTS (freshly extracted, ranked by similarity):
{candidates_block}

Decide the routing and respond with JSON only.
"""


def _call_orchestrator_llm(
    current_message: str,
    chat_history: list,
    active_intent: dict | None,
    candidate_intents: list[dict],
) -> dict:

    prompt = _build_orchestrator_prompt(
        current_message, chat_history, active_intent, candidate_intents
    )

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        system=ORCHESTRATOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()

    # Strip markdown fences if model wraps output
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        result = json.loads(raw.strip())
    except json.JSONDecodeError:
        logger.warning("[Orchestrator] JSON parse failed. Raw: %s", raw)
        result = {
            "intent_code":  "intent_classifier",
            "message":      "",
            "confidence":   0.4,
            "reason":       "JSON parse failed — falling back to intent_classifier"
        }

    return result


def _resolve_full_intent(
    intent_code: str,
    candidate_intents: list[dict],
    classifier_intent: dict,
) -> dict:
    """
    Given the orchestrator's chosen intent_code, return the full intent dict
    that downstream nodes (sql_generator, payload_filler) expect.

    Fast path: if orchestrator confirmed the classifier's top-1, return it as-is
    (already has views, required_parameters, sql_query_manual, etc.).
    Slow path: look up from the classifier's live dataframe via _find_intent_by_code.
    Fallback: return classifier_intent with a warning.
    """
    if intent_code == classifier_intent.get("intent_code"):
        return classifier_intent

    try:
        from services.graph.nodes.intent_classifier import _find_intent_by_code
        full = _find_intent_by_code(intent_code)
        if full:
            return full
    except Exception as exc:
        logger.warning("[Orchestrator] _find_intent_by_code failed: %s", exc)

    logger.warning(
        "[Orchestrator] Could not resolve full dict for %s — keeping classifier top-1",
        intent_code,
    )
    return classifier_intent


def _validate_intent_code(
    intent_code: str,
    active_intent: dict | None,
    candidate_intents: list[dict],
) -> str:
    """
    Ensure the LLM didn't hallucinate an intent_code.
    Allowed values: active intent code, any candidate code,
    GET_KNOWLEDGEBASE, NONE.
    """
    allowed = {INTENT_KB_NODE, INTENT_NONE}

    if active_intent and active_intent.get("intent_code"):
        allowed.add(active_intent["intent_code"])

    for c in candidate_intents:
        if c.get("intent_code"):
            allowed.add(c["intent_code"])

    if intent_code not in allowed:
        logger.warning(
            "[Orchestrator] Hallucinated intent_code '%s' — falling back to intent_classifier",
            intent_code
        )
        return "intent_classifier"   # triggers fresh classification

    return intent_code


def orchestrator_node(state: GraphState) -> dict:
    msg               = state.get("message", "")
    history           = state.get("history", [])
    # active_intent = persisted slim dict from the PREVIOUS turn (loaded by message_service)
    # classifier_intent = this turn's fresh top-1 (set by intent_classifier just before us)
    active_intent     = state.get("active_intent") or {}
    classifier_intent = state.get("intent") or {}
    candidate_intents = state.get("candidate_intents") or []

    logger.info(
        "[Orchestrator] message=%s | active=%s | fresh_top1=%s | candidates=%d",
        msg,
        active_intent.get("intent_code", "None"),
        classifier_intent.get("intent_code", "None"),
        len(candidate_intents),
    )

    # Pass active_intent (not classifier_intent) so the LLM sees what session is in progress
    decision = _call_orchestrator_llm(msg, history, active_intent, candidate_intents)

    raw_code   = decision.get("intent_code", INTENT_NONE)
    reply_msg  = decision.get("message", "")
    confidence = decision.get("confidence", 0.0)
    reason     = decision.get("reason", "")

    intent_code = _validate_intent_code(raw_code, active_intent, candidate_intents)

    logger.info(
        "[Orchestrator] intent_code=%s | confidence=%.2f | reason=%s",
        intent_code, confidence, reason,
    )

    base_logs = [
        f"[Orchestrator] intent_code={intent_code} confidence={confidence:.2f}",
        f"[Orchestrator] reason={reason}",
    ]

    # ── NONE / GET_KNOWLEDGEBASE — no intent update ───────────────────────────
    if intent_code in (INTENT_NONE, INTENT_KB_NODE):
        return {
            "orchestrator_intent_code": intent_code,
            "orchestrator_reply":       reply_msg,
            "orchestrator_logs":        base_logs,
        }

    # ── Real intent code — resolve full dict and update state ─────────────────
    resolved = _resolve_full_intent(intent_code, candidate_intents, classifier_intent)

    new_active_intent = {
        "intent_code": intent_code,
        "intent_name": resolved.get("intent_name", ""),
        "action_type": resolved.get("action_type", ""),
        "description": resolved.get("description", ""),
    }
    base_logs.append(
        f"[Orchestrator] active_intent updated → {intent_code} ({new_active_intent['action_type']})"
    )

    return {
        "orchestrator_intent_code": intent_code,
        "orchestrator_reply":       "",
        "intent":                   resolved,          # downstream nodes (sql_generator, filler) use this
        "active_intent":            new_active_intent, # persisted by message_service for next turn
        "orchestrator_logs":        base_logs,
    }