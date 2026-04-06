# Zenie AI — Financial Chat Assistant

A FastAPI-based financial chatbot that takes a natural language query, classifies the intent, extracts date ranges, and generates a validated SQL query against the matched database view — all via a LangGraph pipeline.

---

## How to Run

```bash
# Activate virtual environment
source venv/bin/activate

# Start server
uvicorn app.main:app --reload --port 8001
```

App available at: `http://localhost:8001`

> Avoid `--reload` if you want to skip reloading on every file save (the embedding matrix build adds ~5s on restart).

---

## Environment Variables

`.env` file in the project root:

```
ANTHROPIC_API_KEY=your_key_here
COMPANY_ID=1
```
---

## Project Structure

```
Zenie_AI/
├── app/
│   ├── main.py                        # FastAPI app entry point, mounts router + static files
│   ├── controllers/
│   │   └── chat_controller.py         # Receives ChatRequest, calls process_message(), returns ChatResponse
│   ├── routes/
│   │   └── chat.py                    # POST /api/v1/chat/ and WebSocket /api/v1/chat/ws endpoints
│   ├── schemas/
│   │   └── chat_schema.py             # Pydantic models: ChatRequest, ChatResponse, Message
│   └── static/
│       ├── index.html                 # Web UI
│       ├── script.js                  # Frontend logic: send message, render panels, copy SQL
│       └── style.css                  # Dark theme styles
│
├── services/
│   ├── message_service.py             # Entry point: builds initial state, invokes LangGraph pipeline
│   ├── date_extractor_lib.py          # Rule-based financial date extractor (no LLM)
│   └── graph/
│       ├── __init__.py
│       ├── state.py                   # GraphState TypedDict (shared state across all nodes)
│       ├── graph.py                   # StateGraph definition: nodes, edges, fan-out/fan-in, compile
│       └── nodes/
│           ├── __init__.py
│           ├── orchestrator.py        # Passthrough node — entry point of the graph
│           ├── intent_classifier.py   # Semantic search: SentenceTransformer + cosine similarity
│           ├── date_extractor.py      # Calls date_extractor_lib, serializes result to state
│           └── sql_generator.py       # MockSQLDatabase + create_sql_agent (ReAct, Claude)
│
├── data/
│   ├── Intent_file.xlsx               # Intent library — single source of truth for active intents
│   └── view_metadata.py               # Column metadata for each database view (developer-maintained)
│
├── core/
│   └── cofig.py                       # Logging configuration
│
├── .env                               # API keys (not committed)
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
 ┌─── LangGraph StateGraph ─────────────────────────────────────┐
 │                                                               │
 │   START                                                       │
 │     │                                                         │
 │     ▼                                                         │
 │  [orchestrator]          passthrough, logs entry              │
 │     │                                                         │
 │     ├──────────────────────┐                                  │
 │     ▼                      ▼                                  │
 │  [intent_classifier]  [date_extractor]   ← parallel fan-out  │
 │     └──────────────────────┘                                  │
 │                  │  (fan-in join)                             │
 │                  ▼                                            │
 │          [sql_generator]             current final node       │
 │                  │                                            │
 │                END                                            │
 └───────────────────────────────────────────────────────────────┘
       │
       ▼
 ChatResponse { status, response (SQL), data { intent, date_range, sql_query, logs } }
```

### Nodes

| Node | File | What it does |
|---|---|---|
| `orchestrator` | `nodes/orchestrator.py` | Passthrough. Logs message receipt. Placeholder for future preprocessing. |
| `intent_classifier` | `nodes/intent_classifier.py` | Encodes query with `all-MiniLM-L6-v2`, runs cosine similarity against intent embeddings, returns top match including mapped view name. |
| `date_extractor` | `nodes/date_extractor.py` | Rule-based extraction of single periods and comparison ranges (no LLM). Returns `{ primary, secondary, is_comparison }`. |
| `sql_generator` | `nodes/sql_generator.py` | Builds `MockSQLDatabase` from `view_metadata.py`, runs `create_sql_agent` (ReAct/zero-shot) with Claude, validates output, returns SQL. |

### GraphState keys

| Key | Set by | Description |
|---|---|---|
| `message` | input | User's raw message |
| `history` | input | Conversation history (last 8 messages) |
| `company_id` | input | Injected into every SQL WHERE clause |
| `session_id` | input | Session identifier |
| `intent` | `intent_classifier` | Top matched intent dict (code, name, view, similarity, etc.) |
| `date_range` | `date_extractor` | Serialized date result `{ primary, secondary, is_comparison }` |
| `orchestrator_logs` | `orchestrator` | Log lines from orchestrator |
| `intent_logs` | `intent_classifier` | Log lines from intent classifier |
| `date_logs` | `date_extractor` | Log lines from date extractor |
| `sql_query` | `sql_generator` | Final generated SQL string |
| `response` | `sql_generator` | Response text (same as sql_query on success) |
| `logs` | `sql_generator` | Merged logs from all nodes, sent to frontend |

---

## Key Files

### `data/Intent_file.xlsx`
The single source of truth for active intents. Loaded once at startup.

Required columns: `Intent_Code`, `Intent_Name`, `Intent_Category`, `Description`, `Required_Parameters`, `Optional_Parameters`, `View`

Only intents with a non-empty `View` column are embedded and searchable. Leave `View` blank to exclude an intent.

### `data/view_metadata.py`
Maps each database view to its column list and the date column used for filtering. The SQL generator uses only this — no database introspection happens.

To add a new view:
1. Add an entry in `view_metadata.py`
2. Set the `View` column in `Intent_file.xlsx` for the relevant intents

### `services/date_extractor_lib.py`
Pure rule-based NLP. No LLM. Handles: financial years, quarters, half-years, months, relative expressions (`last 3 months`, `this week`), YTD/MTD/FYTD, explicit ranges, and comparison queries (`Q1 vs Q2`, `this year vs last year`).

Financial year start month is configurable: `FY_START_MONTH = 4` (April, India/UK default).

### `services/graph/nodes/sql_generator.py`
Uses `MockSQLDatabase` — a subclass of LangChain's `SQLDatabase` that returns schema info from `view_metadata.py` without a real DB connection. The `create_sql_agent` runs in `zero-shot-react-description` mode (ReAct), which is compatible with Claude (avoids the OpenAI function-call format that Claude rejects).

---

## Web UI

The frontend (`app/static/`) has four sections:

| Section | Description |
|---|---|
| **Chat window** | Conversation history — user messages and bot SQL responses |
| **Intent panel** | Shows matched intent: code, name, category, view, similarity %, description |
| **Date panel** | Shows extracted date range(s); shows both periods for comparison queries |
| **SQL panel** | Generated SQL with Copy button |
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
  "message": "Show top customers this year",
  "history": [],
  "metadata": { "company_id": "1", "session_id": "uuid" }
}
```

Response:
```json
{
  "status": "success",
  "response": "SELECT customer_name, SUM(total_amount) ...",
  "data": {
    "intent": { "intent_code": "TOP_CUSTOMERS_REPORT_VIEW", "view": "vw_ai_sales_invoice", ... },
    "date_range": { "primary": { "start": "2026-01-01", "end": "2026-12-31", "label": "2026" }, "is_comparison": false },
    "sql_query": "SELECT customer_name, SUM(total_amount) AS total_revenue FROM vw_ai_sales_invoice WHERE company_id = 1 AND invoice_date BETWEEN '2026-01-01' AND '2026-12-31' GROUP BY customer_name ORDER BY total_revenue DESC;",
    "logs": ["[Orchestrator] ...", "[IntentClassifier] ...", "[DateExtractor] ...", "[SQLGenerator] ..."]
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
| `langgraph` | Pipeline graph (fan-out/fan-in, node orchestration) |
| `langchain-community` | `create_sql_agent`, `SQLDatabaseToolkit` |
| `langchain-anthropic` | Claude LLM for SQL generation |
| `sentence-transformers` | `all-MiniLM-L6-v2` for intent embeddings |
| `scikit-learn` | Cosine similarity |
| `pandas` + `openpyxl` | Reading and cleaning `Intent_file.xlsx` |
| `dateparser` + `python-dateutil` | Date parsing fallback in `date_extractor_lib.py` |
| `anthropic` | Anthropic Claude API client |
| `python-dotenv` | Loads `.env` |

---

## Future Nodes (planned)

The graph is designed to be extended. Add new nodes by:
1. Creating `services/graph/nodes/<node_name>.py`
2. Adding keys to `GraphState` in `state.py`
3. Wiring edges in `graph.py`

Planned additions:
- **Field extractor** — extracts filter values (product name, region, customer) from the message
- **Payload generator** — packages intent + dates + filters into a structured API payload
- **Orchestrator logic** — real preprocessing / routing based on intent category or confidence threshold
