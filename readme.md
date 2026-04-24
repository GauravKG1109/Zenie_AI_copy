# Zenie AI — Financial Chat Assistant

A FastAPI-based financial chatbot that takes a natural language query, classifies the intent, and routes it to the right pipeline:
- **READ intents** → date extraction → SQL generation (existing flow)
- **WRITE intents** → multi-turn payload filler → collects form fields conversationally

---

## How to Run

```bash
# Activate virtual environment
source venv/bin/activate

# Start server
uvicorn app.main:app --reload --port 8002
```

App available at: `http://localhost:8001`

> Embeddings are now built during the `Application startup` phase (before the server accepts requests), so the first user message is never delayed by embedding generation.

> Avoid `--reload` if you want to skip reloading on every file save (the embedding matrix build adds ~5s on restart).

---

## Environment Variables

`.env` file in the project root:

```
ANTHROPIC_API_KEY=your_key_here
COMPANY_ID=1
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
│   │   └── chat.py                    # POST /api/v1/chat/ and WebSocket /api/v1/chat/ws endpoints
│   ├── schemas/
│   │   └── chat_schema.py             # Pydantic models: ChatRequest, ChatResponse, Message
│   └── static/
│       ├── index.html                 # Web UI
│       ├── script.js                  # Frontend logic: send message, render panels, display DB results in chat
│       └── style.css                  # Dark theme styles
│
├── services/
│   ├── message_service.py             # Entry point: builds initial state, invokes LangGraph pipeline
│   ├── date_extractor_lib.py          # Rule-based financial date extractor (no LLM)
│   ├── db/
│   │   └── db_query_executor.py       # Executes raw SQL via SQLAlchemy; returns JSON-serializable results
│   └── graph/
│       ├── __init__.py
│       ├── state.py                   # GraphState TypedDict (shared state across all nodes)
│       ├── graph.py                   # StateGraph: nodes, conditional routing, compile
│       └── nodes/
│           ├── __init__.py
│           ├── orchestrator.py        # Passthrough node — entry point of the graph
│           ├── intent_classifier.py   # Semantic search: SentenceTransformer + cosine similarity
│           ├── date_extractor.py      # Calls date_extractor_lib, serializes result to state
│           ├── sql_generator.py       # MockSQLDatabase + create_sql_query_chain (Claude) + DB execution
│           └── LLM_payload_filler.py  # Multi-turn field collector for WRITE intents
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
POST /api/v1/chat/
       │
       ▼
 chat_controller.py  →  process_message()
       │
       ▼
 ┌─── LangGraph StateGraph ──────────────────────────────────────────────┐
 │                                                                        │
 │   START                                                                │
 │     │                                                                  │
 │     ▼                                                                  │
 │  [orchestrator]          passthrough, logs entry                       │
 │     │                                                                  │
 │     ▼                                                                  │
 │  [intent_classifier]     semantic search → returns intent + action_type│
 │     │                                                                  │
 │     │  action_type == WRITE?                                           │
 │     ├─── YES ──► [payload_filler_node] ──► END                        │
 │     │                                                                  │
 │     └─── NO (READ) ──► [date_extractor] ──► [sql_generator] ──► END   │
 │                                                                        │
 └────────────────────────────────────────────────────────────────────────┘
       │
       ▼
 READ:  ChatResponse { response: SQL,   data: { intent, date_range, sql_query, query_result, logs } }
 WRITE: ChatResponse { response: reply, data: { intent, current_data, logs } }
```

### Nodes

| Node | File | What it does |
|---|---|---|
| `orchestrator` | `nodes/orchestrator.py` | Passthrough. Logs message receipt. Placeholder for future preprocessing. |
| `intent_classifier` | `nodes/intent_classifier.py` | Encodes query with `all-MiniLM-L6-v2`, runs cosine similarity against intent embeddings, returns top match including `action_type`. |
| `date_extractor` | `nodes/date_extractor.py` | Rule-based extraction of single periods and comparison ranges (no LLM). Returns `{ primary, secondary, is_comparison }`. READ path only. |
| `sql_generator` | `nodes/sql_generator.py` | Builds `MockSQLDatabase` from `view_metadata.py`, runs `create_sql_query_chain` with Claude, validates output, executes SQL via `db_query_executor`, returns both SQL and results. READ path only. |
| `payload_filler_node` | `nodes/LLM_payload_filler.py` | Multi-turn field collector for WRITE intents. Calls LLM with a dynamic system prompt, parses JSON response, updates session-scoped `FieldStorage`. WRITE path only. |

### GraphState keys

| Key | Set by | Description |
|---|---|---|
| `message` | input | User's raw message |
| `history` | input | Conversation history |
| `company_id` | input | Injected into every SQL WHERE clause |
| `session_id` | input | Session identifier |
| `intent` | `intent_classifier` | Top matched intent dict (code, name, view, action_type, similarity, etc.) |
| `date_range` | `date_extractor` | Serialized date result `{ primary, secondary, is_comparison }` — READ only |
| `orchestrator_logs` | `orchestrator` | Log lines from orchestrator |
| `intent_logs` | `intent_classifier` | Log lines from intent classifier |
| `date_logs` | `date_extractor` | Log lines from date extractor |
| `sql_query` | `sql_generator` | Final generated SQL string — READ only |
| `query_result` | `sql_generator` | DB execution result `{ success, data, row_count }` or `{ success, error }` — READ only |
| `response` | `sql_generator` | Response text (same as sql_query on success) — READ only |
| `logs` | `sql_generator` | Merged logs from all nodes — READ only |
| `current_data` | `payload_filler_node` | Accumulated field values collected during WRITE conversation |
| `payload_logs` | `payload_filler_node` | Log lines from payload filler |
| `reply` | `payload_filler_node` | Natural language response sent back to user for WRITE intents |

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

**Graceful fallback:** if a keyword maps to an action_type that has zero matching rows in the intent library (e.g. `ANALYSE` before those intents are added), the filter is silently skipped and the full embedding matrix is searched — so the server never returns an empty result.

To add more trigger words, extend `_KEYWORD_RULES` in `intent_classifier.py`:
```python
_KEYWORD_RULES: list[tuple[re.Pattern, str]] = [
    # WRITE triggers
    (re.compile(r'\bcreate\b', re.IGNORECASE), "WRITE"),
    (re.compile(r'\badd\b',    re.IGNORECASE), "WRITE"),
    (re.compile(r'\binsert\b', re.IGNORECASE), "WRITE"),
    (re.compile(r'\bpost\b',   re.IGNORECASE), "WRITE"),
    # READ triggers
    (re.compile(r'\btell\b',   re.IGNORECASE), "READ"),
    (re.compile(r'\bshow\b',   re.IGNORECASE), "READ"),
    (re.compile(r'\bfind\b',   re.IGNORECASE), "READ"),
    # ANALYSE triggers (future)
    (re.compile(r'\bexplain\b', re.IGNORECASE), "ANALYSE"),
    (re.compile(r'\banalyse\b', re.IGNORECASE), "ANALYSE"),
    (re.compile(r'\banalyze\b', re.IGNORECASE), "ANALYSE"),
    # add more here ...
]
```

When a new `action_type` is introduced (e.g. `ANALYSE`), add its rows to `Intent_file.xlsx` with that value in the `Action_Type (READ/WRITE)` column — the mask is built automatically at startup from the live data, so no code change is needed beyond the Excel update.

### `core/database.py`
Configures the SQLAlchemy connection pool (`QueuePool`) from `DATABASE_URL` in `.env`. Exposes `engine`, `SessionLocal`, `Base`, and a `get_db()` dependency. The pool is shared across the entire process — `sql_generator_node` and `db_query_executor` both reuse these connections.

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

When a user sends a message that matches a `WRITE` intent (e.g. "Create an invoice for Aman"), the graph routes to `payload_filler_node` instead of SQL generation.

**Multi-turn field collection:**
1. First turn: LLM extracts any fields present in the message, asks for missing ones
2. Subsequent turns: session store retains already-collected fields, LLM only asks for what's still missing
3. When all mandatory fields are filled, the session store is cleared (ready for API call)

**Session storage:** An in-memory dict `_session_store` keyed by `(session_id, intent_code)` persists `FieldStorage` across HTTP requests within the same server process. *(For production, replace with Redis.)*

**Example exchange:**
```
User:  "Create an invoice for 100 dollars by the name Aman"
Bot:   "Got it! I have customer_name and total_amount. Could you provide invoice_number, posting_date, status, and subtotal?"

User:  "Invoice number INV-001, posting date 2026-04-14, status draft"
Bot:   "Great! Still need subtotal and product details."
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

> The `ollama` import is deferred — the server starts fine even if Ollama is not installed. The error only occurs when a WRITE query arrives with `ACTIVE_MODEL = "qwen"`.

---

## Web UI

The frontend (`app/static/`) has four sections:

| Section | Description |
|---|---|
| **Chat window** | Conversation history — user messages and formatted DB query results (READ) or NL replies (WRITE) |
| **Intent panel** | Shows matched intent: code, name, category, view, similarity %, action_type |
| **Date panel** | Shows extracted date range(s) — READ flow only |
| **SQL panel** | Generated SQL with Copy button — READ flow only |
| **Logs** | Pipeline step logs from all nodes; clears on Reset |

**Controls:** Company ID input, Session ID (auto-generated, persisted in `localStorage`), Reset button (clears all panels and starts a new session).

---

## API

### `POST /api/v1/chat/`

Request:
```json
{
  "company_id": "1",
  "session_id": "uuid",
  "message": "Create an invoice for 100 dollars by the name Aman",
  "history": []
}
```

READ response:
```json
{
  "status": "success",
  "response": "SELECT customer_name, SUM(total_amount) ...",
  "data": {
    "intent": { "intent_code": "TOP_CUSTOMERS_REPORT_VIEW", "action_type": "READ", ... },
    "date_range": { "primary": { "start": "2026-01-01", "end": "2026-12-31" }, "is_comparison": false },
    "sql_query": "SELECT ...",
    "query_result": { "success": true, "data": [{"customer_name": "Dhoni", "total_transactions": 30}], "row_count": 1 },
    "logs": ["[Orchestrator] ...", "[IntentClassifier] ...", "[DateExtractor] ...", "[SQLGenerator] ...", "[SQLGenerator] Query executed: 1 rows returned"]
  }
}
```

WRITE response:
```json
{
  "status": "success",
  "response": "Got it! I have customer_name. Could you provide invoice_number and posting_date?",
  "data": {
    "intent": { "intent_code": "CREATE_INVOICE", "action_type": "WRITE", ... },
    "current_data": { "customer_name": "Aman", "invoice_number": null, ... },
    "logs": ["[PayloadFiller] intent=Create Invoice | extracted=[...] | missing=[...]"]
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
| `langchain-anthropic` | Claude LLM for SQL generation and payload filling |
| `langchain-openai` | GPT models (optional, for payload filling) |
| `ollama` | Qwen 2.5 7B via local Ollama server (optional, for payload filling) |
| `sentence-transformers` | `all-MiniLM-L6-v2` for intent embeddings |
| `scikit-learn` | Cosine similarity |
| `pandas` + `openpyxl` | Reading and cleaning `Intent_file.xlsx` |
| `dateparser` + `python-dateutil` | Date parsing fallback in `date_extractor_lib.py` |
| `anthropic` | Anthropic Claude API client |
| `python-dotenv` | Loads `.env` |
