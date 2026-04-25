# Zenie AI — Financial Chat Assistant

A FastAPI-based financial chatbot that takes a natural language query, classifies the intent, and routes it to the right pipeline:
- **READ intents** → date extraction → SQL generation → DB execution
- **WRITE intents** → multi-turn payload filler → confirmation gate → dummy API call
- **NONE** → direct LLM reply (greetings, out-of-scope, clarification)
- **GET_KNOWLEDGEBASE** → stub node (coming soon)

---

## How to Run

```bash
# Activate virtual environment
source venv/bin/activate

# Start server
uvicorn app.main:app --reload --port 8000
```

App available at: `http://localhost:8000`

> Embeddings are built during the `Application startup` phase (before the server accepts requests), so the first user message is never delayed by embedding generation.

> Avoid `--reload` in production — the embedding matrix build adds ~5s on every file-save restart.

---

## Environment Variables

`.env` file in the project root:

```
ANTHROPIC_API_KEY=your_key_here
DATABASE_URL=postgresql://user:password@host:port/dbname
```

---

## Project Structure

```
Zenie_AI/
├── app/
│   ├── main.py                        # FastAPI app entry point, mounts router + static files, tests DB connection
│   ├── controllers/
│   │   └── chat_controller.py         # Receives ChatRequest, calls process_message(), returns ChatResponse
│   ├── routes/
│   │   └── chat.py                    # POST /api/v1/chat/, POST /api/v1/chat/stream, WebSocket /ws
│   ├── schemas/
│   │   └── chat_schema.py             # Pydantic models: ChatRequest, ChatResponse, Message
│   └── static/
│       ├── index.html                 # Web UI: chat, intent/date/write panels, notification banner
│       ├── script.js                  # Frontend logic: SSE streaming, panel rendering, write notification
│       └── style.css                  # Dark theme styles
│
├── services/
│   ├── message_service.py             # Entry point: builds state, runs pipeline, persists active_intent
│   ├── date_extractor_lib.py          # Rule-based financial date extractor (no LLM)
│   ├── db/
│   │   └── db_query_executor.py       # Executes raw SQL via SQLAlchemy; returns JSON-serializable results
│   └── graph/
│       ├── __init__.py
│       ├── state.py                   # GraphState TypedDict (shared state across all nodes)
│       ├── graph.py                   # StateGraph: nodes, conditional routing, compile
│       └── nodes/
│           ├── __init__.py
│           ├── orchestrator.py        # LLM router (Claude Haiku): decides intent_code or NONE each turn
│           ├── intent_classifier.py   # Semantic search: SentenceTransformer + cosine similarity, top-5 candidates
│           ├── date_extractor.py      # Calls date_extractor_lib, serializes result to state
│           ├── sql_generator.py       # MockSQLDatabase + create_sql_query_chain (Claude) + DB execution
│           ├── LLM_payload_filler.py  # Multi-turn field collector + 3-phase confirmation for WRITE intents
│           └── get_knowledgebase.py   # Stub node: returns placeholder reply for GET_KNOWLEDGEBASE routing
│
├── core/
│   ├── config.py                      # Logging config + DUMMY_FIELDS / DUMMY_APIS for CREATE_INVOICE
│   ├── database.py                    # SQLAlchemy engine + session factory (QueuePool, loaded from DATABASE_URL)
│   └── storage.py                     # FieldStorage class: tracks collected/missing fields per session
│
├── data/
│   ├── Intent_file.xlsx               # Intent library — single source of truth for all intents
│   ├── view_metadata.py               # Column metadata for each database view (developer-maintained)
│   └── models/
│       └── all-MiniLM-L6-v2/          # Local sentence transformer model (cached after first download)
│
├── .env                               # API keys and DATABASE_URL (not committed)
├── requirements.txt                   # Python dependencies
└── readme.md                          # This file
```

---

## Pipeline Architecture

```
POST /api/v1/chat/stream
       │
       ▼
 chat_controller.py  →  stream_message()
       │
       ▼
 ┌─── LangGraph StateGraph ──────────────────────────────────────────────────┐
 │                                                                            │
 │   START                                                                    │
 │     │                                                                      │
 │     ▼                                                                      │
 │  [intent_classifier]   semantic search → top-1 intent + top-5 candidates  │
 │     │                                                                      │
 │     ▼                                                                      │
 │  [orchestrator]        LLM router: reads active_intent + candidates        │
 │     │                  decides: continue flow / switch / NONE / KB         │
 │     │                                                                      │
 │     ├─── NONE ──────► [end_with_reply] ──────────────────────────► END    │
 │     ├─── GET_KB ─────► [get_knowledgebase_node] ────────────────── ► END  │
 │     ├─── READ code ──► [date_extractor] ──► [sql_generator] ──────► END   │
 │     └─── WRITE code ─► [payload_filler_node] ──────────────────────► END  │
 │                                                                            │
 └────────────────────────────────────────────────────────────────────────────┘
```

### Nodes

| Node | File | What it does |
|---|---|---|
| `intent_classifier` | `nodes/intent_classifier.py` | Encodes query with `all-MiniLM-L6-v2`, runs cosine similarity against intent embeddings. Returns top-1 `intent` (full dict) and top-5 `candidate_intents` (slim dicts for orchestrator). |
| `orchestrator` | `nodes/orchestrator.py` | LLM router using Claude Haiku. Reads `active_intent` (persisted from last turn), `candidate_intents`, and last 6 messages. Outputs `orchestrator_intent_code` (real code, NONE, or GET_KNOWLEDGEBASE) and updates `active_intent`. |
| `end_with_reply` | `graph.py` | Thin pass-through: copies `orchestrator_reply` → `reply` so NONE responses reach the frontend uniformly. |
| `get_knowledgebase_node` | `nodes/get_knowledgebase.py` | Stub: returns a placeholder reply for KB/policy questions. |
| `date_extractor` | `nodes/date_extractor.py` | Rule-based extraction of single periods and comparison ranges (no LLM). Returns `{ primary, secondary, is_comparison }`. READ path only. |
| `sql_generator` | `nodes/sql_generator.py` | Builds `MockSQLDatabase` from `view_metadata.py`, runs `create_sql_query_chain` with Claude, validates output, executes SQL via `db_query_executor`, returns SQL and results. READ path only. |
| `payload_filler_node` | `nodes/LLM_payload_filler.py` | 3-phase WRITE flow: (0) LLM collects missing fields, (1) shows summary + asks yes/no, (2) on yes fires `write_notification` and clears session. WRITE path only. |

### GraphState keys

| Key | Set by | Description |
|---|---|---|
| `message` | input | User's raw message |
| `history` | input | Conversation history |
| `company_id` | input | Injected into every SQL WHERE clause |
| `session_id` | input | Session identifier |
| `intent` | `intent_classifier` / `orchestrator` | Top matched intent dict (code, name, view, action_type, similarity, etc.) |
| `candidate_intents` | `intent_classifier` | Top-5 slim intent dicts `{ intent_code, description, action_type, similarity }` — consumed by orchestrator |
| `active_intent` | `orchestrator` | Slim intent dict persisted across HTTP turns via `_active_intent_store` in `message_service` |
| `orchestrator_intent_code` | `orchestrator` | Routing decision: real intent code, `NONE`, or `GET_KNOWLEDGEBASE` |
| `orchestrator_reply` | `orchestrator` | Direct reply text when code is `NONE` (copied to `reply` by `end_with_reply`) |
| `date_range` | `date_extractor` | Serialized date result `{ primary, secondary, is_comparison }` — READ only |
| `orchestrator_logs` | `orchestrator` | Log lines from orchestrator |
| `intent_logs` | `intent_classifier` | Log lines from intent classifier |
| `date_logs` | `date_extractor` | Log lines from date extractor |
| `sql_query` | `sql_generator` | Final generated SQL string — READ only |
| `query_result` | `sql_generator` | DB execution result `{ success, data, row_count }` or `{ success, error }` — READ only |
| `logs` | `sql_generator` | Merged logs from all nodes — READ only |
| `current_data` | `payload_filler_node` | Accumulated field values collected during WRITE conversation |
| `payload_logs` | `payload_filler_node` | Log lines from payload filler |
| `reply` | `payload_filler_node` / `end_with_reply` | Natural language response for WRITE or NONE paths |
| `write_notification` | `payload_filler_node` | Set on WRITE confirmation: `{ intent_code, intent_name, payload, status: "ready" }` |

---

## Key Files

### `data/Intent_file.xlsx`
The single source of truth for all intents. Loaded once at startup; embeddings are cached in `data/embeddings_cache.pkl` (auto-invalidated when the file changes).

Required columns: `Intent_Code`, `Intent_Name`, `Intent_Category`, `Action_Type (READ/WRITE)`, `Description`, `Required_Parameters`, `Optional_Parameters`, `Typical_User_Query`, `View`

**Embedding rules:**
- `READ` intents: embedded only if the `View` column is non-empty (no view = no SQL = not useful)
- `WRITE` intents: always embedded, regardless of `View` (they are routed to `payload_filler_node`, not SQL generation)

**Embedding text:** `Intent_Name + Intent_Category + Description + Typical_User_Query` — including the example query improves match quality for conversational phrasing.

### `data/view_metadata.py`
Maps each database view to its column list and the date column used for filtering. The SQL generator uses only this — no live database introspection.

### Keyword pre-filter (`nodes/intent_classifier.py`)

Before cosine similarity runs, the classifier checks the user message for trigger words using whole-word regex patterns (`\bcreate\b`, not `created` or `creating`). If a trigger word is found, the semantic search is restricted to the matching `action_type` rows only — preventing, for example, a clear WRITE query from accidentally matching a READ intent.

| Flag | Default | Effect |
|---|---|---|
| `KEYWORD_FILTER_ENABLED` | `True` | Set to `False` to disable the filter entirely |

**Current trigger rules:**

| Keyword(s) | Restricts search to | Notes |
|---|---|---|
| `create`, `add`, `insert`, `post` | `WRITE` intents only | Exact whole-word match — `created` / `creat` do NOT trigger |
| `tell`, `show`, `find` | `READ` intents only | Exact whole-word match |
| `explain`, `analyse`, `analyze` | `ANALYSE` intents only | No `ANALYSE` rows in Excel yet — filter gracefully falls back to searching all intents |

**Graceful fallback:** if a keyword maps to an action_type that has zero matching rows, the filter is silently skipped and the full embedding matrix is searched — the server never returns an empty result.

### Orchestrator (`nodes/orchestrator.py`)

LLM router using `claude-haiku-4-5-20251001`. Each turn it receives:
- `active_intent` — the slim intent dict persisted from the last turn (or empty on first message)
- `candidate_intents` — top-5 slim dicts from the classifier
- Last 6 messages of conversation history

It outputs one of:
- A real `intent_code` (e.g. `SALES_BY_CUSTOMER_VIEW`) → graph routes to READ or WRITE pipeline
- `NONE` → graph routes to `end_with_reply` with a direct LLM reply
- `GET_KNOWLEDGEBASE` → graph routes to knowledge base stub

The orchestrator also handles mid-flow intent switches automatically: if a user abandons a WRITE mid-fill and asks a READ question, the orchestrator detects the switch, `message_service` calls `clear_write_session()`, and the WRITE state is discarded.

### `services/message_service.py`

Entry point for both `process_message` (non-streaming) and `stream_message` (SSE streaming). Maintains `_active_intent_store` — a module-level dict keyed by `session_id` that persists the orchestrator's `active_intent` across HTTP requests. This is what allows the orchestrator to "remember" which intent was active last turn.

### `core/database.py`
Configures the SQLAlchemy connection pool (`QueuePool`) from `DATABASE_URL` in `.env`. Exposes `engine`, `SessionLocal`, `Base`, and a `get_db()` dependency.

### `services/db/db_query_executor.py`
Single function `execute_query(sql: str) -> dict`. Runs the SQL against the live database via `engine.connect()` and returns a JSON-safe dict:
- Success: `{ "success": True, "data": [{"col": value, ...}], "row_count": N }`
- Failure: `{ "success": False, "error": "..." }`

`Decimal` values from PostgreSQL are automatically converted to `float` so the result is directly JSON-serializable.

### `core/config.py`
Holds `DUMMY_FIELDS` and `DUMMY_APIS` — the field definitions for the `CREATE_INVOICE` WRITE intent. These are currently hardcoded as a placeholder; in production they will be loaded from the Excel file per intent.

### `core/storage.py`
`FieldStorage` class: initialised with a list of required field names, tracks which are filled vs. missing, and provides `update_field()`, `get_missing_fields()`, and `is_complete()`.

### `services/date_extractor_lib.py`
Pure rule-based NLP. No LLM. Handles: financial years, quarters, half-years, months, relative expressions (`last 3 months`, `this week`), YTD/MTD/FYTD, explicit ranges, and comparison queries (`Q1 vs Q2`, `this year vs last year`).

Financial year start month: `FY_START_MONTH = 4` (April, India/UK default).

---

## WRITE Intent Flow (Payload Filler)

When the orchestrator routes to a WRITE intent, `payload_filler_node` handles a 3-phase conversation:

**Phase 0 — Field collection:**
1. First turn: LLM extracts any fields present in the message, asks for missing ones
2. Subsequent turns: session store retains already-collected fields, LLM only asks for what's still missing

**Phase 1 — Confirmation:**
3. Once all mandatory fields are filled, the bot shows a summary and asks "Shall I proceed? (yes / no)"

**Phase 2 — Submission:**
4. User says "yes" → `write_notification` is set, dummy API is called, frontend shows banner + Write Result panel
5. User says "no" → session cleared, cancellation reply sent

**Session storage:** An in-memory dict `_session_store` keyed by `(session_id, intent_code)` persists `FieldStorage` across HTTP requests. *(For production, replace with Redis.)*

**Example exchange:**
```
User:  "Create an invoice for 5000 rupees for Dhoni"
Bot:   "Got it! I have customer_name and total_amount. Could you provide invoice_number, posting_date, status, subtotal, and product?"

User:  "INV-001, 2026-04-14, draft, 4500, bat"
Bot:   "I have all the required information:
         customer_name: Dhoni
         invoice_number: INV-001
         ...
       Shall I proceed with Create Invoice? (yes / no)"

User:  "yes"
Bot:   "Done! Your Create Invoice has been submitted successfully."
       [Frontend: notification banner appears, Write Result panel populated]
```

### Switching the LLM for WRITE flow

Change the `ACTIVE_MODEL` constant at the top of `services/graph/nodes/LLM_payload_filler.py`:

```python
ACTIVE_MODEL = "claude"   # Claude Haiku 4.5 — default
ACTIVE_MODEL = "gpt"      # GPT-4.1 Nano
ACTIVE_MODEL = "qwen"     # Qwen 2.5 7B (local via Ollama)
```

**For Qwen 2.5 7B:**
1. Install Ollama: https://ollama.com/download
2. Pull the model: `ollama pull qwen2.5:7b`
3. Start the server: `ollama serve`
4. Set `ACTIVE_MODEL = "qwen"` in `LLM_payload_filler.py`
5. Restart the FastAPI server

> The `ollama` import is deferred — the server starts fine even if Ollama is not installed.

---

## Web UI

The frontend (`app/static/`) has five sections:

| Section | Description |
|---|---|
| **Notification banner** | Auto-dismissing banner (4s) that appears on successful WRITE submission |
| **Chat window** | Conversation history — user messages, formatted DB results (READ), NL replies (WRITE/NONE) |
| **Intent panel** | Shows matched intent: code, name, category, view, similarity %, action_type |
| **Date panel** | Shows extracted date range(s) — READ flow only |
| **Write Result panel** | Shows submitted payload fields + status after WRITE confirmation |
| **SQL panel** | Generated SQL with Copy button — READ flow only |
| **Logs** | Pipeline step logs from all nodes; clears on Reset |

**Controls:** Company ID input (defaults to `019cfba4-f83a-7fd9-80e3-cddca906e7db`), Session ID (auto-generated, persisted in `localStorage`), Reset button (clears all panels and starts a new session).

---

## API

### `POST /api/v1/chat/stream` (SSE streaming — recommended)

Request:
```json
{
  "company_id": "019cfba4-f83a-7fd9-80e3-cddca906e7db",
  "session_id": "uuid",
  "message": "show me sales by customer this month",
  "history": [],
  "metadata": { "company_id": "...", "session_id": "..." }
}
```

Each SSE event is a `data: {JSON}\n\n` line. Node events include:

| Node | Key fields in event |
|---|---|
| `intent_classifier` | `intent`, `logs` |
| `orchestrator` | `intent`, `logs` (active_intent update) |
| `end_with_reply` | `reply` |
| `date_extractor` | `date_range`, `logs` |
| `sql_generator` | `sql_query`, `query_result`, `logs` |
| `payload_filler_node` | `reply`, `current_data`, `write_notification` (on confirmation), `logs` |
| `get_knowledgebase_node` | `reply`, `logs` |
| `__done__` | (final sentinel) |

### `POST /api/v1/chat/`

Non-streaming version. Returns full result after pipeline completes.

READ response:
```json
{
  "response": "SELECT customer_name, SUM(total_amount) ...",
  "data": {
    "intent": { "intent_code": "SALES_BY_CUSTOMER_VIEW", "action_type": "READ", ... },
    "date_range": { "primary": { "start": "2026-04-01", "end": "2026-04-30" }, "is_comparison": false },
    "sql_query": "SELECT ...",
    "query_result": { "success": true, "data": [...], "row_count": 5 },
    "logs": [...]
  }
}
```

WRITE response (field collection turn):
```json
{
  "response": "Got it! I have customer_name. Could you provide invoice_number and posting_date?",
  "data": {
    "intent": { "intent_code": "CREATE_INVOICE", "action_type": "WRITE", ... },
    "current_data": { "customer_name": "Aman", "invoice_number": null, ... },
    "write_notification": null,
    "logs": [...]
  }
}
```

WRITE response (confirmed):
```json
{
  "response": "Done! Your Create Invoice has been submitted successfully.",
  "data": {
    "intent": { "intent_code": "CREATE_INVOICE", ... },
    "current_data": { "customer_name": "Aman", "invoice_number": "INV-001", ... },
    "write_notification": { "intent_code": "CREATE_INVOICE", "intent_name": "Create Invoice", "payload": {...}, "status": "ready" },
    "logs": [...]
  }
}
```

### `WebSocket /api/v1/chat/ws`
Same payload and response shape as the POST endpoint.

---

## Dependencies (key packages)

| Package | Purpose |
|---|---|
| `fastapi` + `uvicorn` | Web server |
| `langgraph` | Pipeline graph with conditional routing |
| `langchain-community` | `create_sql_query_chain`, SQL tooling |
| `langchain-anthropic` | Claude LLM for SQL generation, orchestrator, and payload filling |
| `langchain-openai` | GPT models (optional, for payload filling) |
| `ollama` | Qwen 2.5 7B via local Ollama server (optional, for payload filling) |
| `sentence-transformers` | `all-MiniLM-L6-v2` for intent embeddings |
| `scikit-learn` | Cosine similarity |
| `pandas` + `openpyxl` | Reading and cleaning `Intent_file.xlsx` |
| `dateparser` + `python-dateutil` | Date parsing fallback in `date_extractor_lib.py` |
| `sqlalchemy` + `psycopg2-binary` | PostgreSQL connection pool and query execution |
| `anthropic` | Anthropic Claude API client |
| `python-dotenv` | Loads `.env` |
