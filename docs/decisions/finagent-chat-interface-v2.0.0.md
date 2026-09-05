# FinAgent Chat Interface — Technical Design Specification (v2)

> **Version:** v2.0.0  
> **Date:** May 17, 2026  
> **Supersedes:** [FinAgent Chat Interface](finagent-chat-interface.md) (v1, Go-flavored)  
> **Depends on:** [Language Python over Go](language-python-over-go.md), [Agent Framework PydanticAI](agent-framework-pydanticai.md)  
> **Stack:** Python 3.12+ / FastAPI / PydanticAI / mem0 / signalbot / SSE  
> **Constraint:** All processing local on Asus TUF A15. Zero raw financial data leaves the home network.

---

## 1. Executive Summary

v2 rebuilds the chat interface against the Python/PydanticAI stack while **preserving the architectural patterns from v1** (Ports & Adapters, stable API contract, SSE streaming, security sanitizer, file processing pipeline).

**Key shifts from v1:**

| Concern | v1 (Go) | v2 (Python) |
|---|---|---|
| Stack | Go + LangChainGo | FastAPI + PydanticAI |
| Memory | Ad-hoc context from Postgres | Postgres (verbatim + structured) + mem0 (distilled facts) |
| Upload UX | Sync wait, SSE progress | Background job + Signal notify on completion |
| `chat_messages` schema | text-only | + tool_calls, memory_refs, tokens_used, embedding |
| Streaming | Token-only SSE | Hybrid: prose tokens stream; tool calls + status + memory hits as discrete events |
| Multi-channel | Documented as future | Shared core processor + thin per-channel adapters from day 1 |
| Concurrency | Not addressed | Per-session message queue (serial processing per channel) |
| Error model | Generic | Tiered: validation inline, agent-tier as friendly text, infra-tier as banner |

---

## 2. Architecture Overview

Same Ports & Adapters principle as v1 — chat UI is a **swappable adapter behind a stable contract**. v2 makes this explicit with a single channel-agnostic core processor.

```
┌──────────────────────────────────────────────────────────────┐
│                   CORE (Python / FastAPI)                     │
│                                                                │
│  ┌─────────────────┐  ┌──────────────┐  ┌─────────────────┐  │
│  │  Financial      │  │  PydanticAI  │  │   PostgreSQL    │  │
│  │  Engine         │  │  Agent       │  │   ledger        │  │
│  │  (sim, ledger,  │  │  + tools     │  │   + chat_msgs   │  │
│  │   tax shadow)   │  │              │  │   + pgvector    │  │
│  └─────────────────┘  └──────┬───────┘  └─────────────────┘  │
│                              │                                 │
│                       ┌──────▼───────┐                        │
│                       │  mem0        │                        │
│                       │  (in-process │                        │
│                       │  library, PG │                        │
│                       │  backend)    │                        │
│                       └──────────────┘                        │
│                              │                                 │
│         ┌────────────────────▼─────────────────────┐          │
│         │  process_message(text, session_id)       │          │
│         │  → AsyncIterator[ChatEvent]              │          │
│         └────────────────────┬─────────────────────┘          │
└──────────────────────────────┼─────────────────────────────────┘
                               │  (Channel Adapters)
            ┌──────────────────┼────────────────────┐
            │                  │                    │
     ┌──────▼──────┐    ┌──────▼──────┐    ┌──────▼──────┐
     │  Web SSE    │    │  Signal     │    │  Future:    │
     │/api/v1/chat │    │ (signalbot) │    │  Mobile, etc│
     └─────────────┘    └─────────────┘    └─────────────┘
```

The web adapter wraps the event stream as SSE. The Signal adapter consumes the same event stream and collapses tokens into a single message (Signal has no per-message streaming UX inside a chat), drops status/memory_hit events, and surfaces tool_call events as part of the response only when relevant. See [Security v1.0.0](../security/v1.0.0.md) for the rationale behind choosing Signal over Telegram.

---

## 3. Module Boundary Contract

### 3.1 API Endpoints

| Endpoint | Method | Purpose | Request | Response |
|---|---|---|---|---|
| `/api/v1/chat` | `POST` | Send a text message | `{"message": str, "session_id": str}` | SSE stream of `ChatEvent` |
| `/api/v1/upload` | `POST` | Upload PDF/image | `multipart/form-data` with `file` + optional `password` | `{"job_id": str, "status": "queued"}` |
| `/api/v1/jobs/{job_id}` | `GET` | Poll an upload job | — | `{"status": str, "progress": str, "result": ...}` |
| `/api/v1/history` | `GET` | Fetch past messages | `?session_id=&limit=50&before=ts` | `{"messages": [...]}` |
| `/api/v1/health` | `GET` | Readiness | — | `{"app": "ok", "ollama": "ok", "db": "ok", "mem0": "ok"}` |

### 3.2 PostgreSQL Schema (chat-interface tables)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE chat_messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content         TEXT NOT NULL,
    media_type      TEXT,                      -- 'image', 'pdf', or NULL
    media_ref       TEXT,                      -- path to processed artifact
    tool_calls      JSONB,                     -- [{name, args, result}, ...]
    memory_refs     JSONB,                     -- {"mem0": [...], "chat_search": [...]}
    tokens_used     INTEGER,
    embedding       vector(384),               -- semantic search via pgvector
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_chat_messages_session ON chat_messages(session_id, created_at DESC);
CREATE INDEX idx_chat_messages_embedding ON chat_messages USING hnsw (embedding vector_cosine_ops);

CREATE TABLE upload_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('queued', 'processing', 'done', 'failed')),
    progress        TEXT,                      -- human-readable: "Extracting page 3..."
    file_type       TEXT NOT NULL,
    file_path       TEXT,                      -- temp encrypted blob
    result          JSONB,                     -- {"transactions_logged": 47}
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);
CREATE INDEX idx_upload_jobs_session ON upload_jobs(session_id, created_at DESC);

-- Append-only audit log (per Security v1.0.0 §9)
CREATE TABLE audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind            TEXT NOT NULL CHECK (kind IN ('tool_call', 'mem0_write', 'auth')),
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor           TEXT NOT NULL,            -- session_id or 'system'
    payload         JSONB NOT NULL,           -- shape depends on kind
    reason          TEXT
);
CREATE INDEX idx_audit_log_kind_ts ON audit_log(kind, timestamp DESC);

-- Enforce append-only via trigger
CREATE OR REPLACE FUNCTION prevent_audit_modification()
    RETURNS TRIGGER AS $$ BEGIN RAISE EXCEPTION 'audit_log is append-only'; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER no_update_audit BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();
```

mem0 owns its own tables in the same Postgres instance via its pgvector backend — no separate database. **Sensitive columns** (`chat_messages.content`, mem0 fact values) are encrypted via the `pgcrypto` extension; see [Security v1.0.0](../security/v1.0.0.md) §6.

### 3.3 SSE Event Types (the wire protocol)

Events are a **discriminated union** keyed on `type`. Frontend dispatches per type.

```python
from pydantic import BaseModel
from typing import Any, Literal, Optional

class TokenEvent(BaseModel):
    type: Literal["token"] = "token"
    token: str

class StatusEvent(BaseModel):
    type: Literal["status"] = "status"
    message: str                                # "Checking your goals..."

class MemoryHitEvent(BaseModel):
    type: Literal["memory_hit"] = "memory_hit"
    summary: str                                # "Remembered: Goa trip planned for May 20"

class ToolCallEvent(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    name: str
    args: dict
    result: Optional[Any]                       # None until tool returns

class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    full_response: str
    tokens_used: int

class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    tier: Literal["validation", "agent", "infra"]
    code: str
    message: str
    retry_hint: Optional[str] = None
```

**Wire format** (one event per SSE message):

```
event: status
data: {"type": "status", "message": "Checking your goals..."}

event: memory_hit
data: {"type": "memory_hit", "summary": "Remembered: emergency fund 80% funded"}

event: tool_call
data: {"type": "tool_call", "name": "simulate_purchase", "args": {"amount": 60000}, "result": null}

event: tool_call
data: {"type": "tool_call", "name": "simulate_purchase", "args": {"amount": 60000}, "result": {"verdict": "yes_but", "tradeoff": "Goa +3w"}}

event: token
data: {"type": "token", "token": "Yeah"}

event: token
data: {"type": "token", "token": " you"}

event: done
data: {"type": "done", "full_response": "Yeah you can swing it...", "tokens_used": 423}
```

---

## 4. Module Structure

```
finagent/
├── pyproject.toml              # uv-managed
├── docker-compose.yml          # app + postgres + ollama
├── src/finagent/
│   ├── main.py                 # FastAPI entrypoint, lifespan setup
│   ├── config.py               # pydantic-settings from env
│   ├── api/
│   │   ├── chat.py             # /api/v1/chat handler
│   │   ├── upload.py           # /api/v1/upload + /api/v1/jobs/{id}
│   │   ├── history.py
│   │   └── health.py
│   ├── core/
│   │   ├── processor.py        # process_message() — channel-agnostic
│   │   ├── events.py           # ChatEvent classes
│   │   ├── context.py          # build_context() — profile+goals+history+mem0
│   │   └── concurrency.py      # per-session asyncio.Lock + queue
│   ├── agent/
│   │   ├── runner.py           # PydanticAI Agent definition; AgentRunner protocol
│   │   ├── tools.py            # @agent.tool functions
│   │   └── prompts.py          # system prompt templates
│   ├── domain/
│   │   ├── ledger.py           # transactions, balances, goals
│   │   ├── simulation.py       # forward simulation engine
│   │   ├── tax.py              # tax shadow engine
│   │   ├── sanitizer.py        # regex sanitizer
│   │   └── extractor.py        # pdfplumber + pymupdf4llm + pytesseract pipeline
│   ├── memory/
│   │   ├── mem0_client.py      # in-process mem0 wrapper
│   │   └── chat_search.py      # pgvector semantic search over chat_messages
│   ├── channels/
│   │   ├── web_sse.py          # SSE adapter — wraps event stream
│   │   └── signal.py           # signalbot adapter — collapses event stream
│   ├── jobs/
│   │   ├── scheduler.py        # APScheduler setup
│   │   ├── reflection.py       # 6h batch + on-demand reflection
│   │   ├── briefings.py        # morning + Sunday review
│   │   ├── backfill_check.py   # detect missed-logging windows
│   │   └── upload_worker.py    # processes upload_jobs
│   ├── db/
│   │   ├── models.py           # SQLAlchemy models
│   │   ├── session.py          # async session factory
│   │   └── migrations/         # alembic
│   └── static/
│       └── chat.html           # single-file vanilla JS frontend
└── tests/
```

---

## 5. Chat Turn Lifecycle

Walking through a single chat message end-to-end:

1. **Ingress** — POST `/api/v1/chat` (web) or signalbot message handler (Signal)
2. **Auth** — env token (web) or Signal phone-number allowlist check (see §10)
3. **Enqueue** — push onto per-session message queue; if agent is mid-response, wait (see §11)
4. **Persist user message** — insert into `chat_messages` with computed embedding
5. **Sanitize** — `Sanitizer.should_block()`; if blocked, emit `ErrorEvent(tier="validation")` and return
6. **Build context** (§7) — assemble profile + active goals + last N messages + mem0 facts + semantic-search hits
7. **Emit memory hits** — for each mem0 fact or retrieved chat, emit `MemoryHitEvent` early in the stream (so the user sees "remembered: X" before tokens start)
8. **Invoke agent** — `agent.run_stream(message, deps=context)`
9. **For each event from agent:**
   - Tool calls → emit `ToolCallEvent` twice (start with `result=None`, end with filled `result`)
   - Status updates (from a small helper exposed to the agent) → emit `StatusEvent`
   - Tokens → emit `TokenEvent`
10. **Persist assistant message** with populated `tool_calls`, `memory_refs`, `tokens_used`, and embedding
11. **Emit `DoneEvent`** with full response + token usage
12. **Trigger reflection if agent called `remember_this()` tool** — fire-and-forget background task to write to mem0

---

## 6. Upload Job Lifecycle

Walking through a bank-statement PDF upload:

1. **POST `/api/v1/upload`** with multipart file
2. **Validate** — size ≤ `FINAGENT_MAX_UPLOAD_MB`, type in `{pdf, jpg, png, webp}`, optional password
3. **Persist file** to temp storage (encrypted at rest if `FINAGENT_ENCRYPT_TEMP_FILES=true`)
4. **Insert `upload_jobs` row** with `status='queued'`
5. **Return** `{"job_id": ..., "status": "queued"}` immediately — client is not blocked
6. **Background worker (APScheduler-triggered or queue-driven) picks up the job:**
   - `status='processing'`, update `progress`
   - Extract: pdfplumber (tables) → pymupdf4llm fallback (narrative) → pytesseract (if scanned)
   - Sanitize extracted text (mask account numbers, IFSC, mobile, drop OTP lines)
   - Invoke agent's `extract_transactions(text) -> list[Transaction]` PydanticAI tool (structured output)
   - Dedupe candidate transactions against existing rows (date + amount + merchant)
   - Insert non-duplicates into `transactions` table
   - `status='done'`, `result={"transactions_logged": 47, "duplicates_skipped": 3}`
7. **Send Signal message** to user: *"Parsed your May statement — 47 transactions logged (3 duplicates skipped). Want to review?"*
8. **Client can poll** `/api/v1/jobs/{job_id}` for status if they want in-UI feedback

Web UI behavior: upload form returns instantly with "queued"; chat shows a small "Job 12abc... queued" inline message. The user-visible result arrives via Signal (or via polling if they're on the web tab).

---

## 7. Context Building Strategy

Always-on rich context per turn. Chosen because LLM round-trips dominate latency on a 5-15 tok/sec local model — paying input-token cost once is cheaper than making the agent call tools for context discovery.

Each turn's prompt assembly:

| Block | Source | Tokens (typical) |
|---|---|---|
| System prompt (persona, tools, rules) | `prompts.py` template | ~1500 |
| Profile | `profiles` table | ~150 |
| Active goals | `goals` table (where `status='active'`) | ~300 |
| Tax shadow snapshot | computed | ~100 |
| Recent messages | last 10 from `chat_messages` for this session | ~1500 |
| mem0 facts | `mem0.search(query=user_message, user_id="joy", limit=5)` | ~400 |
| Cross-session relevant chats | pgvector cosine search top 3, exclude current session | ~400 |
| **User message** | this turn | ~50 |
| **Total input** | | **~4400** |
| **Reserved for response** | | ~3500 |

Fits comfortably in 8K context. Tuned via `FINAGENT_CONTEXT_BUDGET_TOKENS`.

---

## 8. Memory Integration

### Reads (per turn, via context builder)

- `mem0.search(query=user_message, user_id=FINAGENT_MEM0_USER_ID, limit=5)`
- `ChatSearch.semantic(query=user_message, limit=3, exclude_session=current_session_id)`

Both happen in parallel via `asyncio.gather()`. Each fact/hit becomes a `MemoryHitEvent` emitted before tokens start.

### Writes

- **Verbatim:** every message goes to `chat_messages` with embedding computed at insert time (via Ollama embedding model — `nomic-embed-text` default)
- **On-demand distillation:** agent has a `remember_this(fact: str)` tool. When called, fires a background task that does `mem0.add(fact, user_id=...)`
- **Batch distillation (every 6 hours):** APScheduler job reads `chat_messages` not yet processed (tracked via a watermark), runs an LLM "what's worth remembering from this exchange?" pass, writes results to mem0 in bulk

### mem0 deployment

- Python library `mem0ai`, imported in-process
- Backed by the same Postgres + pgvector (no separate Qdrant/Chroma)
- No sidecar service to manage
- `user_id="joy"` for v1; namespacing structure is in place for future multi-user

---

## 9. Security Sanitizer (Ported from v1)

Same patterns, Python implementation. Runs on every input — both chat messages (block-only) and extracted file text (mask-only — preserves useful transaction context).

```python
import re

class Sanitizer:
    BLOCK_PATTERNS = [
        re.compile(r"\botp\b", re.IGNORECASE),
        re.compile(r"\bpassword\b", re.IGNORECASE),
        re.compile(r"\bpin\b", re.IGNORECASE),
        re.compile(r"\bcvv\b", re.IGNORECASE),
        re.compile(r"\b\d{6}\s*(is your|for your)\b"),
        re.compile(r"verify.*code", re.IGNORECASE),
    ]

    MASK_PATTERNS = [
        # Full account number → keep only last 4
        (re.compile(r"\b(\d{4})\s*(\d{4})\s*(\d{4})\s*(\d{4})\b"), r"XXXX-XXXX-XXXX-\4"),
        # IFSC code
        (re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"), "[IFSC-REDACTED]"),
        # Indian mobile number
        (re.compile(r"\b[6-9]\d{9}\b"), "[MOBILE-REDACTED]"),
        # Lines containing OTP — drop entirely
        (re.compile(r"(?i).*otp.*\n?"), ""),
    ]

    def should_block(self, text: str) -> bool:
        return any(p.search(text) for p in self.BLOCK_PATTERNS)

    def mask_extracted(self, text: str) -> str:
        for pattern, replacement in self.MASK_PATTERNS:
            text = pattern.sub(replacement, text)
        return text
```

---

## 10. Auth & Session Model

### Auth

- **Web:** every request to `/api/v1/*` requires `X-Session-Token` header matching env `FINAGENT_WEB_TOKEN`. Bound to `127.0.0.1` so requests cannot reach the app from outside the host. Single static value (single-user reality).
- **Signal:** `signal-cli` (running inside the `signal-cli-rest-api` sidecar) is registered to your phone number as a linked device. The signalbot middleware validates incoming sender phone numbers against `FINAGENT_SIGNAL_ALLOWED_NUMBERS` (comma-separated E.164 env). All other senders silently dropped.
- **Admin endpoints** (`/admin/halt`, `/admin/undo`): separate `FINAGENT_ADMIN_TOKEN` required. Distinct from web token.

### Session model

- **Web:** `session_id` = stable browser cookie set on first visit (`finagent_session=<uuid>`). One rolling thread per browser.
- **Signal:** `session_id = f"signal-{phone_number}"` (E.164 format). One rolling thread per allowed sender.
- **Briefings/system messages:** `session_id = f"system-{kind}-{date}"` (e.g., `system-morning-brief-2026-05-18`).

No auto-detected topic threads in v1 — cross-session recall via mem0 + chat history semantic search makes this unnecessary for the agent. Topic threads remain a v1.1 UI-only feature.

---

## 11. Concurrency Model

Serial processing per session. When a new message arrives while the agent is mid-response on the same session:

- The new message enters the session's `asyncio.Queue`
- Agent finishes current turn, persists, emits `DoneEvent`
- Channel adapter pulls next message from queue automatically
- Frontend may show a "queued" indicator on the pending message

Implementation: a `dict[str, asyncio.Lock]` keyed on `session_id`. Lock held for the duration of `process_message()`. Different sessions process in parallel naturally.

---

## 12. Background Tasks (APScheduler)

Set up in FastAPI's `lifespan` context manager. All schedules use `FINAGENT_TIMEZONE` (default `Asia/Kolkata`).

| Task | Schedule | What it does |
|---|---|---|
| Morning brief | Daily 08:00 | Compose brief: yesterday's spend, today's known outflows, anomalies; send via Signal |
| Sunday review | Sundays 09:00 | Compose weekly recap + 30-day forward look; send via Signal |
| Reflection batch | Every 6 hours | Read chat_messages with `id > last_reflection_watermark`; distill facts; write to mem0 |
| Backfill check | Daily 21:00 | If user hasn't logged in N days, send Signal nudge: "Walk me through this week?" |
| Anomaly scan | Every 4 hours | Pattern checks over recent transactions; if anomaly, send Signal alert |
| Subscription detection | Weekly (Mon 10:00) | Detect recurring patterns; flag new ones for confirmation |
| Upload worker tick | Every 30 seconds | Pick up queued upload_jobs, process them |

Briefings and anomalies use the same `process_message()`-style flow but with `session_id="system-<kind>-<date>"` namespaces. The agent generates the briefing text; the Signal channel adapter sends it.

---

## 13. Error Model (Tiered)

| Tier | Examples | How it surfaces |
|---|---|---|
| **Validation** | upload too large, missing session_id, empty message, sanitizer block | `ErrorEvent(tier="validation")` emitted before agent invoked; frontend renders inline near input |
| **Agent** | tool timeout, PydanticAI `ValidationError`, OCR returned garbage, simulation failed | Friendly text from agent persona — emitted as `TokenEvent`s so it feels like the agent speaking. *"I had trouble running the simulation — want me to try again?"* |
| **Infra** | Ollama unreachable, DB connection lost, mem0 init failed | `ErrorEvent(tier="infra")` with detail; frontend renders as prominent banner; Signal sends a "⚠️ Backend issue, see web" message |

**Mitigation for PydanticAI's small-model brittleness** (see [Agent Framework PydanticAI](agent-framework-pydanticai.md)):

- All structured-output tool calls wrapped in PydanticAI's retry mechanism (default 2 retries, configurable)
- Ollama v0.5+ structured outputs enabled at the model level
- On second failure, falls through to agent-tier friendly error

---

## 14. Environment Configuration

```bash
# .env (never committed)

# Auth
FINAGENT_WEB_TOKEN=<strong-random-string>
FINAGENT_ADMIN_TOKEN=<separate-strong-random-string>

# Signal channel
FINAGENT_SIGNAL_PHONE_NUMBER=+919999999999          # phone registered with signal-cli
FINAGENT_SIGNAL_ALLOWED_NUMBERS=+919999999999       # comma-separated E.164 allowlist
FINAGENT_SIGNAL_CLI_REST_URL=http://signal-cli:8080 # sidecar URL

# Data
FINAGENT_DB_URL=postgresql+asyncpg://localhost:5432/finagent

# LLM
FINAGENT_OLLAMA_URL=http://localhost:11434
FINAGENT_OLLAMA_MODEL=qwen3:8b
FINAGENT_OLLAMA_EMBED_MODEL=nomic-embed-text

# Memory
FINAGENT_MEM0_USER_ID=joy

# Uploads
FINAGENT_MAX_UPLOAD_MB=32
FINAGENT_ENCRYPT_TEMP_FILES=true

# Backups (see Infrastructure.md)
FINAGENT_BACKUP_BUCKET=s3://your-encrypted-bucket

# Misc
FINAGENT_TIMEZONE=Asia/Kolkata
FINAGENT_CONTEXT_BUDGET_TOKENS=4500
```

---

## 15. Python Dependencies Summary

| Package | Purpose | External network? |
|---|---|---|
| `fastapi`, `uvicorn` | HTTP server | Never |
| `pydantic`, `pydantic-ai` | Models + agent framework | Never |
| `sse-starlette` | SSE in FastAPI | Never |
| `sqlalchemy[asyncio]`, `asyncpg` | ORM + Postgres driver | Local only |
| `pgvector` | Python binding for pgvector | Local only |
| `ollama` | Ollama HTTP client | localhost:11434 only |
| `mem0ai` | Memory layer | Local Postgres |
| `pdfplumber`, `pymupdf4llm`, `pikepdf` | PDF parsing (digital, narrative, password) | Never |
| `pytesseract` | OCR (calls system tesseract) | Never |
| `signalbot` | Signal bot framework | localhost only (talks to signal-cli-rest-api sidecar) |
| `apscheduler` | Scheduler | Never |
| `python-multipart` | File uploads | Never |
| `cryptography` | At-rest temp-file encryption | Never |
| `pydantic-settings` | Env config | Never |
| `alembic` | DB migrations | Never |

System packages: `tesseract-ocr`, `tesseract-ocr-eng`, `postgresql-16`, `postgresql-16-pgvector`, `postgresql-16-pgcrypto`.

External Docker container: `bbernhard/signal-cli-rest-api` — Signal bridge; registered to your phone number as a linked device. Outbound long-poll to Signal servers; content is E2E encrypted.

All dependencies are local-compute except `signal-cli-rest-api` (Signal outbound, E2E encrypted at protocol level) and the backup bucket (encrypted payload only). No analytics. No telemetry.

---

## 16. V1 Scope vs Deferred

### V1 (build now)

- Text chat with SSE streaming over hybrid protocol (tokens + status + memory_hit + tool_call + done + error)
- PDF/CSV upload with background processing + Signal completion notify
- Image OCR upload
- `chat_messages` schema with `tool_calls` / `memory_refs` / `tokens_used` / `embedding`
- Postgres + pgvector + mem0 in-process
- Sanitizer (block on chat, mask on extracted)
- Auth: env token (web) + Signal phone-number allowlist
- APScheduler-driven briefings, reflection, backfill, anomaly scans
- Per-session message queue (serial concurrency)
- Tiered error model
- Web frontend: single `chat.html`, vanilla JS, drag-drop file upload
- Signal adapter: shared core processor; collapses token stream for Signal message UX

### V1.1 (after v1 proves useful)

- Voice notes via `faster-whisper` (sidecar or in-process)
- Auto-detected topic threads (UI-side grouping)
- Password-protected PDF prompt in UI
- Live balance/goals sidebar in web frontend
- Cloudflare Tunnel exposure for outside-home access
- `status_events` JSONB column on `chat_messages` for full UX replay

### V2 (long term)

- OpenWebUI frontend via OpenAI-compatible shim
- Mobile PWA over the same `/api/v1/*` endpoints
- Multi-user (family) with per-user mem0 namespaces
- Account Aggregator integration

---

## 17. Open Questions for Implementation Phase

- **Signal phone number strategy** — register `signal-cli` to your personal number, or get a dedicated number for the bot? (Trade-off documented in [Security v1.0.0](../security/v1.0.0.md) §16.)
- **Sanitizer rules** — do we need additional regex for any specific Indian bank format quirks discovered during real testing?
- **Embedding model choice** — `nomic-embed-text` (Ollama-served) vs `sentence-transformers/all-MiniLM-L6-v2` (in-process) — measure latency on GTX 1650
- **Reflection prompt template** — what's the exact LLM prompt that asks "what's worth remembering from this exchange?"
- **Chat history pruning** — when to summarize/forget old `chat_messages`; embedding regeneration cost
- **Context budget tuning** — 4500-token default may need adjustment after measuring real prompt sizes
- **Mem0 search vs entity strategy** — start with semantic search; revisit if entity graph adds value
- **Frontend reconnection behavior** — if SSE drops mid-stream, do we resume or restart? (v1: restart; revisit if painful)

---

## 18. Related Documents

- [Security v1.0.0](../security/v1.0.0.md) — threat model, prompt injection defenses, audit log, channel decision (Signal over Telegram)
- [Language Python over Go](language-python-over-go.md) — language-level decision
- [Agent Framework PydanticAI](agent-framework-pydanticai.md) — framework-level decision
- [Product Picture v1.0.0](../product-picture/v1.0.0.md) — the requirements this interface serves
- [Infrastructure](../infrastructure.md) — hardware, deploy, backup
- [FinAgent Chat Interface](finagent-chat-interface.md) — v1 (superseded, kept for historical reasoning)
- [Capabilities v1.0.0](../capabilities/v1.0.0.md) — feature list the UI must expose
- [Tools and MCPs v1.0.0](../tools-and-mcps/v1.0.0.md) — tool servers the agent will call
- [Initial Conversation](../initial-conversation.md) — origin decisions
