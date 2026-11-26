# Harness layer

This specification owns Arno's PydanticAI agent, its capabilities, prompt discipline, history
storage, and test and eval harness; [`plan.md`](../plan.md) §3 owns stack decisions,
[`tools.md`](tools.md) owns tool contracts, [`cost-tracking.md`](cost-tracking.md) owns the usage
log and daily spend cap, and [`observability.md`](../operations/observability.md) owns tracing
operations.

## Why plain PydanticAI

Arno runs one `Agent` from PydanticAI with plain-Python tools and a few small capabilities. The
core library supplies each control the product needs: prompt caching, model fallback, history
processors, native tool approval, usage limits, and the Vercel AI stream adapter. Provider
portability is built in too: `OllamaProvider`, `OpenAIProvider`, `AnthropicProvider`, and
`OpenRouterProvider` share one agent loop, keeping local and cloud endpoints on a single code path
(`plan.md` §3).

`VercelAIAdapter` streams text, tool calls, custom card data parts, source citations, and deferred
tool-approval requests in the Vercel AI UI protocol that the web app consumes. The chat endpoint
wires `from_request`, `run_stream`, and `encode_stream` without a custom streaming protocol, and
decoupled execution lets a turn outlive its HTTP connection ([`architecture.md`](../architecture.md)).

**Why not LangChain or an external agent host:** LangChain's middleware caps call counts rather
than USD spend and lacks a Python Vercel AI stream adapter. Hosting tools inside Hermes or OpenClaw
would replace this harness and place shell and browser toolsets beside the ledger. Because
architecture rule 3 (`plan.md` §4) keeps ledger and tool functions free of PydanticAI types,
exposing Arno over MCP later remains a thin adapter ([`parked.md`](../planning/parked.md)).

## Capabilities

`build_agent` in `src/arno/agent/builder.py` composes these capabilities, and nothing else:

| Capability | Role | Source |
|---|---|---|
| `InferenceContext` | Propagates `session.id`, `gen_ai.conversation.id`, `arno.cost.workload`, and background consent through jobs | `src/arno/agent/capabilities.py` |
| `RootSpanIO` | Records `user.id`, `input.value`, `output.value`, and `arno.approvals.offered` / `arno.approvals.approved` on the `invoke_agent` span | `src/arno/agent/capabilities.py` |
| `Caching` | Enables prompt caching | PydanticAI |
| `ToolErrorsToModel` | Converts unexpected tool failures into structured error payloads while propagating `COST_STOP_ERRORS` unchanged | `src/arno/agent/capabilities.py` |
| `ProcessHistory(trim_old_tool_payloads)` | When the last request nears 70% of the context window (`LLM_CONTEXT_WINDOW`, default `128_000`), blanks older read-tool returns outside the newest 10 messages. Write-tool returns are never trimmed | `src/arno/agent/capabilities.py` |
| `ImageAttachments` | Hydrates raw `BinaryContent` from `attachments` onto outgoing requests without storing base64 in `model_history` | `src/arno/agent/images.py` |
| `web_search` | `WebSearch` capability: the model's native search when it has one, else a local Brave tool when `BRAVE_API_KEY` is set ([`tools.md`](tools.md#web_search)) | `src/arno/agent/builder.py` |
| `ModelFallback` | Falls back to the configured backup model (`sticky=False`) when the primary fails after the SDK's own retries (`max_retries=2`); omitted when no backup is set | `src/arno/agent/models.py` |
| `usage_hooks(role)` | Core `Hooks` that refuse a request once the shared daily cap is reached and write one `llm_usage` row per model response; also attached to the categorizer, vision, and briefing agents | `src/arno/agent/usage.py` |

Lasting or wide writes ask for confirmation through core `ApprovalRequired` and
`DeferredToolRequests` (`plan.md` §4 rule 2; [`tools.md`](tools.md)). A scheduled question builds
the same agent with the read tools only (`read_only=True`).

Run limits: `TURN_LIMITS = UsageLimits(request_limit=25)` caps every chat run, and
`ModelSettings(timeout=600)` is a 10-minute stream-stall guard with no total wall-clock cap.
Scheduled questions keep their own stricter limits (`src/arno/jobs/questions.py`).

## Tool surface

The agent has seven tools plus web search, all thin adapters in `src/arno/agent/toolset.py` over
plain functions in `src/arno/tools/`; full contracts live in [`tools.md`](tools.md):

- **Reads:** `run_query` (read-only SQL as `agent_ro`), `project` (the projection engine), and
  `run_python` (Python in the isolated `sandbox` service).
- **Writes:** `record_movements`, `correct_movement`, `undo`, and `update_setup`.

`run_query` is the one wide read: balances, spending, movements, schedules, goals, memories, and
imports all come from SQL over the `agent_ro` views, so the model computes with SQL or a
`run_python` script and reads the result instead of doing arithmetic in prose (architecture rule
1, `plan.md` §4).

## Instructions

The system prompt is a short positive brief in `src/arno/agent/instructions.py`: money and numbers,
language, answering, writing to the ledger, and boundaries. Tools describe themselves, so the prompt
holds only what they cannot. A dynamic instruction in `builder.py` adds today's date and the active
category names, read from Postgres on every request (falling back to 10 defaults if the database
is unavailable). A scheduled question appends `READ_ONLY_SUFFIX`.

Instructions are generated on every run and never stored in conversation history, so a prompt
change reaches existing threads on its next turn. Behavioral invariants that must never fail are
enforced in code (the sandbox, `agent_ro` grants, and approval gates) rather than prose alone.
Attached images ride on the user message through `ImageAttachments` rather than the system
instructions, keeping prompt caches stable.

## History and streaming

`conversations.model_history` is the model's whole memory: the `ModelMessage` list the agent reads
back on each turn. The `messages` table is the transcript the UI shows. It is written from
`VercelAIAdapter.dump_messages` at save time, in the same transaction as `model_history`, and never
changes afterward. Answered confirm cards are settled when the turn is saved. A run that crashes
between saves is caught by `conversations.run_started_at` and recovered at startup.

| Setting | Value | Source |
|---|---|---|
| Chat adapter | `VercelAIAdapter` with `sdk_version=7` (required for approval cards), `manage_message_history="server"`, and `allowed_file_url_schemes=frozenset()` | `src/arno/api/chat.py` |
| Cancellation | `CancellationToken` stops a run cleanly on user **Stop** or server shutdown so `model_history` and `messages` both save | `src/arno/agent/turns.py` |

### Memory

Arno does not use a notebook or external memory library. Long-lived user facts live in the typed
`preferences` table (`preference`, `fact`, `rule`), saved with a **Remembered** card and undo
through `update_setup(entity="preference")`, and managed in **Settings → Agent memory**.

## Tests and evals

Deterministic behavior around the model is verified in `pytest`; answer quality, safety, and model
suitability are measured in `evals/`.

### Unit and integration tests

`tests/conftest.py` sets `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False`, blocks non-loopback
sockets and DNS, and points every model role at a closed loopback port so tests cannot make paid
requests. Tests drive agent paths with `FunctionModel` scripts and exercise streaming, approvals,
and cancellation through the live FastAPI adapter.

### Agent evals

The eval suite in [`evals/`](../../../evals/README.md) (`evals/cases.py` and `evals/runner.py`)
runs `pydantic_evals` against any configured chat model. It is the only code path that enables
`ALLOW_MODEL_REQUESTS`.

`build_cases()` defines **85 cases** across five categories:

| Category | Cases | Grading focus |
|---|---:|---|
| Answers | 33 | Ledger accuracy, projection verdicts, date and weekday handling, and figures traced to tool output |
| Writes | 23 | Resulting database rows, split and loan math, default naming, and ambiguity handling |
| Honesty | 15 | Refusing missing data, reporting coverage gaps, and distinguishing zero from unknown |
| Never moves money | 10 | Refusing transfer instructions and keeping passwords out of chat |
| Long threads | 4 | Retaining early decisions and exact figures across history trimming |

Key harness properties in `evals/runner.py`:

- **Isolated databases:** Each trial runs inside a fresh Postgres database cloned with
  `CREATE DATABASE ... TEMPLATE` from the demo persona and dropped on teardown.
- **Reliability metric:** Default runs use `repeat=3` (`pass^3`: a case passes only when all three
  trials pass) and `max_concurrency=2`.
- **Usage and cost:** Each trial writes its `llm_usage` rows into its own cloned database, so evals
  never debit the owner's daily cap.
- **Outcome grading:** Graders inspect database state after the last user message rather than
  grading trajectories, and simulate user approval on confirm cards.

### Tracing and spend accounting

Tracing is opt-in through `OTEL_EXPORTER_OTLP_ENDPOINT` (`src/arno/observability.py`); when set,
`bootstrap_observability(app)` installs a stock OpenTelemetry tracer provider, instruments FastAPI
and asyncpg, and calls
`Agent.instrument_all(InstrumentationSettings(include_binary_content=False))` so image bytes stay
out of spans while prompts, tool calls, and replies remain traced. Isolated vision extraction in
`importers/vision.py` still sets `agent.instrument = False`. See
[`observability.md`](../operations/observability.md) for tracing setup and span attributes, and
[`cost-tracking.md`](cost-tracking.md) for the usage log and the daily spend cap.
