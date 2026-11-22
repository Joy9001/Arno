# Harness layer

This specification owns Arno's PydanticAI and `pydantic-ai-harness` capability stack, wake turns,
prompt-map discipline, context compaction, redaction, and test and eval harness; [`plan.md`](../plan.md)
§3 owns stack decisions, [`tools.md`](tools.md) owns tool contracts, [`cost-tracking.md`](cost-tracking.md)
owns pricing and budgets, and [`observability.md`](../operations/observability.md) owns tracing
operations.

## Why PydanticAI

Arno adopts PydanticAI because Pydantic maintains `pydantic-ai-harness`, an official capability
library implementing the core agent-harness controls as supported code. Building a custom agent
loop would require maintaining each control separately. Because `pydantic-ai-harness` is on 0.x
releases, [`pyproject.toml`](../../../pyproject.toml) pins its exact version.

The following table maps each harness requirement to the PydanticAI or `pydantic-ai-harness`
mechanism that provides it:

| Requirement | Mechanism |
|---|---|
| Prompt-cache prefix discipline | `Caching` and `WarnOnCacheBusts` (detects prefix collapse from provider usage) |
| Large tool output control | `ToolOutputLimits` (truncates wide `run_code` returns) |
| Long-thread compaction | `TieredCompaction`, `ClearToolResults`, `SummarizingCompaction`, and `SlidingWindowCompaction` |
| Deterministic input guardrails | `InputGuardrail` (redacts secrets before model dispatch and storage) |
| Spend ceilings | `SpendLimits` and `PostgresSpendStore` (USD and token ceilings per run and shared UTC day) |
| Human-in-the-loop approval | Core `ApprovalRequired` and `DeferredToolRequests` |
| Sandboxed code execution | `CodeMode` (runs one model-written script over read helpers in Monty) |
| Cache-safe dynamic reminders | `SystemReminders` (injects date and write-count reminders after the cached prefix) |
| Durable background delegation | `SubAgents`, `TaskControl`, `DelegationTasks`, `PostgresStepStore`, and `PostgresPlanStore` |
| Batch model requests | `BatchModelRequests` (submits and collects provider batch categorization jobs) |

Provider portability is built into PydanticAI: `OllamaProvider`, `OpenAIProvider`,
`AnthropicProvider`, `OpenRouterProvider`, and `OpenAIChatGPTProvider` share one agent loop,
keeping local and cloud endpoints on a single code path (`plan.md` §3).

PydanticAI also provides `VercelAIAdapter`, which streams text, tool calls, custom card data parts,
and deferred tool-approval requests in the Vercel AI UI protocol consumed by the web app. The chat
endpoint wires `from_request`, `run_stream`, and `encode_stream` without maintaining a custom
streaming protocol, and decoupled execution lets a turn outlive its HTTP connection
([`architecture.md`](../architecture.md)).

**Why not LangChain or an external agent host:** LangChain's middleware covers compaction,
approvals, and guardrails, but it caps call counts rather than USD spend, lacks a Python Vercel AI
stream adapter, and does not provide cache-bust warnings. Hosting tools inside Hermes or OpenClaw
would replace this harness and place shell and browser toolsets beside the ledger. Because
architecture rule 3 (`plan.md` §4) keeps ledger and tool functions free of PydanticAI types,
exposing Arno over MCP later remains a thin adapter ([`parked.md`](../planning/parked.md)).

## Adopted capabilities

Each capability stays off until an operational trigger requires it. Over-adopting harness modules
adds prompt overhead and failure modes without improving answer quality.

### Active capabilities

The following table lists the capabilities active in Arno and where each is configured:

| Capability | Role and configuration | Source |
|---|---|---|
| `InferenceContext` | Propagates `session.id`, `gen_ai.conversation.id`, `arno.cost.workload`, and background consent (`background_inference()`) through summaries, jobs, and delegated work | `src/arno/agent/capabilities.py` |
| `RootSpanIO` | Records `user.id`, redacted `input.value`, `output.value`, and `arno.approvals.offered` / `arno.approvals.approved` on the `invoke_agent` span | `src/arno/agent/capabilities.py` |
| `redaction_guardrail()` | `InputGuardrail` sharing `redaction_detectors()` across chat input, stored history, titles, and traces | `src/arno/agent/capabilities.py`, `src/arno/agent/redaction.py` |
| `CodeMode` | Wraps the 14 read and presentation tools behind `run_code` inside Monty so the model computes in Python instead of prose | `src/arno/agent/capabilities.py`, `src/arno/agent/toolset.py` |
| `Caching` and `WarnOnCacheBusts` | Enables prompt caching and logs warnings when provider cache-read metrics indicate a prefix bust | `src/arno/agent/capabilities.py` |
| `SystemReminders` | Injects `date_and_changes` (ledger date and `audit_log` writes since the thread's `snapshot_at`) after the cached prefix | `src/arno/agent/capabilities.py` |
| `ToolOutputLimits` | Truncates `run_code` returns exceeding 10,000 characters using `TruncationStrategy.head`; scoped to `run_code` so nested tool results inside scripts are not corrupted | `src/arno/agent/capabilities.py` |
| `TieredCompaction` | Compacts long histories with `ClearToolResults` (protecting write tools and the newest 5 pairs) followed by `FallbackCompaction` (`SummarizingCompaction` into `SlidingWindowCompaction`) | `src/arno/agent/capabilities.py` |
| `ToolErrorsToModel` | Converts unexpected tool failures into structured error payloads while propagating `JobGaveUp` and `COST_STOP_ERRORS` unchanged, even across Monty boundaries | `src/arno/agent/capabilities.py` |
| `OutsideTextTaint` | Persists `conversations.outside_text_at` whenever any tool return (including nested Code Mode returns), tool retry error, or parser report or question carries outside text | `src/arno/agent/capabilities.py`, `src/arno/tools/mark.py` |
| `CodeModeHints` | Appends the allowed helper and module lists when `run_code` fails with `Type error in code:` or `Runtime error:` | `src/arno/agent/capabilities.py` |
| `SubAgents` with `TaskControl` | Delegates statement imports to the `parser` subagent (`can_ask_questions=True`, `resumable=True`, `forward_usage=False`, `contain_errors=True`, `auto_background_after=15.0`) backed by `DelegationTasks` and `PostgresStepStore` | `src/arno/agent/builder.py`, `src/arno/api/tasks.py` |
| `Planning` | Keeps multi-step statement parses on track using `PostgresPlanStore` keyed by task ID (parser subagent only) | `src/arno/agent/parser.py` |
| `ModelFallback` and `WindowLimited` | Falls back to the configured backup model (`sticky=False`) and rejects histories exceeding `CHAT_FALLBACK_CONTEXT_WINDOW` before dispatch; omitted when no backup is set and refused on ChatGPT primaries | `src/arno/agent/capabilities.py`, `src/arno/agent/models.py` |
| `SpendLimits` | Enforces per-run, scheduled-question, and shared UTC-day USD ceilings through one `spend_limits()` factory over `PostgresSpendStore` | `src/arno/agent/spend.py` |
| `BatchModelRequests` | Submits and collects provider batch categorization jobs when a `BATCH` model is configured (`max_resubmissions=0`, 30 s flush wait) | `src/arno/agent/categorizer.py`, `src/arno/importers/categorize.py` |
| Conditional tool approval | Core `ApprovalRequired` / `DeferredToolRequests` gates lasting or wide writes in flagged conversations (`plan.md` §4 rule 2) | `src/arno/agent/toolset.py`, `src/arno/api/chat.py` |

### Deferred capabilities and triggers

The following table records harness capabilities deliberately kept off and the evidence required to
adopt each one:

| Capability | Adoption trigger |
|---|---|
| Tool call and result guardrails | Outside text larger than short statement fields reaches the agent (for example, forwarded chat messages), or a tool gains an irreversible write mode |
| Prompt Injection Defender, Approval Reviewer, Tool Call Judge, Artifact Scanner | Eval fooled rate climbs enough to justify an extra judge call in the hot path |
| Tool Search | Evals show the model confusing or missing tools on the current surface |
| Harness Memory capability | Not adopted; Arno stores user-visible, auditable memory in the typed `preferences` table (`preference`, `fact`, `rule`) |
| Step Persistence for chat | Interactive chat turns grow long enough that restart recovery matters; the parser subagent already uses `PostgresStepStore` |
| Skills | A procedure becomes too long for instructions and applies only rarely (removed from the parser once its single procedure loaded on every run) |
| Detect Loops | Traces show spend or request limits triggered by repetitive tool calls |
| Repair Tool Arguments | Local model evals show malformed JSON in tool calls |
| Online Evaluation and Trajectory Judge | Not adopted; offline `pydantic_evals` and Phoenix traces own evaluation |

## Wake turn

`pydantic-ai-harness` holds a settled background task's report for the conversation's next run and
leaves scheduling that run to the host. Without host-initiated turns, a parser task that settles
while no one is chatting remains silent until the user sends another message. Arno runs a wake turn
with no new user prompt and `DelegationReports(priority='asap')` so the report is the first item
the model reads.

The following table defines the wake-turn rules and where each rule lives in code:

| Rule | Implementation |
|---|---|
| A settled task, a finished turn, and server startup check for waiting reports; startup wakes only for reports from the last 24 hours | `TurnManager.schedule_wake`, `src/arno/api/app.py` |
| If a turn is running or a message is being prepared, the wake does nothing because that turn reads the report | `src/arno/api/wake.py` |
| A task stopped by the user waits silently for the next user message | `src/arno/agent/wake.py` (`waiting_reports`) |
| A wake turn may launch at most one fresh parser task marked as an automatic retry; a report from that retry cannot trigger another retry | `WakePolicy` and `KeepRetryMark` task listeners |
| Because the report comes from a model that read a statement, the wake turn is flagged as carrying outside text and lasting writes require confirmation | `src/arno/api/wake.py` |
| A wake turn never resumes a paused task; continuing a paused run requires the user's **Continue** action | `WakePolicy` (`TaskFollowUpEvent` listener) |
| A wake turn that fails or is stopped does not schedule another wake | `TurnManager._run` |
| No user message row is written; the assistant reply is saved as a normal turn | `save_records` |

An open chat follows a wake turn through assistant-ui's idle-follower path. While the wake turn is
running, the task payload sets `follow_up` to `replying` and the thread attaches to
`GET /api/chat/{thread_id}/stream`. The `waiting` state expires after 120 seconds
(`FOLLOW_UP_SECONDS`), preventing cards from polling indefinitely if no wake starts.

## Tool surface design

Arno keeps the tool surface small and wide rather than exposing dozens of narrow tools. The main
agent exposes 20 Arno tools plus 5 delegation tools (25 tools in total; full contracts live in
[`tools.md`](tools.md)):

- **14 read and presentation tools inside `run_code`:** `amortize`, `card_payoff`,
  `compare_offers`, `compound`, `get_financial_context`, `present`, `project`, `query_movements`,
  `recap`, `rupees`, `run_query`, `show_chart`, `show_table`, and `summarize_spending`.
- **6 direct write tools:** `record_movement`, `correct_movement`, `update_setup`,
  `schedule_payment`, `preference`, and `undo`.
- **5 delegation tools:** provided by `SubAgents` and `TaskControl` for starting, inspecting,
  answering, steering, and stopping background `parser` tasks.

Two wide tools anchor the read surface:

- **`get_financial_context`:** Returns settings, account balances, the next 30 days of scheduled
  events, goals, people balances, open imports, and active preferences in one call. New threads
  also pin this payload as the thread-start snapshot.
- **`run_query(sql)`:** Acts as a read-only SQL escape hatch over the `agent_ro` Postgres role
  (`default_transaction_read_only = on`, `2s` statement timeout, no auth or raw `pgledger_*`
  access). Deterministic SQL arithmetic upholds architecture rule 1 (`plan.md` §4), and the query
  remains visible in tool results. If `run_query` is chosen over typed tools for questions typed
  tools already answer, the typed tool descriptions need tightening.

## Instructions discipline

Arno treats its system prompt as a short map rather than an exhaustive manual. Instructions are
generated dynamically on every run and are never persisted in conversation history, so prompt
updates reach existing threads on their next turn with a single cache miss.

The prompt assembly splits across two modules:

- **Fixed rules (`src/arno/agent/instructions.py`):** `RULES` defines 12 immutable rules paired
  with their verifying eval case IDs in `RULE_EVAL_CASES`. Editing a rule requires keeping its
  paired eval cases aligned. The 12 fixed rules are `traced_numbers`, `fresh_financial_context`,
  `affordability`, `record_actual_payees`, `request_missing_amount`, `account_resolution`,
  `split_refund_allocation`, `revert_preference_setup`, `outside_text_is_data`, `never_move_money`,
  `keep_passwords_out_of_chat`, and `no_product_recommendations`.
- **Dynamic context blocks (`src/arno/agent/builder.py`):** `_instructions()` injects the user's
  active category names from Postgres (falling back to 10 defaults if the database is unavailable),
  `INDIA_NOTES` (rupee formatting, Hinglish mirroring, UPI ID preservation), and conditional blocks
  for uploaded or carried `statement_attachments`, `paused_parser_tasks`, wake-turn guidance
  (`WOKEN_TEXT` and `WOKEN_NO_RETRY`), and scheduled-question `READ_ONLY_SUFFIX`.

Active user preferences arrive through the pinned thread-start snapshot rather than the static
prompt, preserving prompt-cache stability across turns. Behavioral invariants that must never fail
are enforced in code (sandboxed arithmetic, `agent_ro` grants, approval gates, and pre-model
redaction) rather than prose alone.

## Context, redaction, and memory

Arno bounds context growth, protects sensitive values before model dispatch, and verifies every
number stated in a reply.

### History, compaction, and streaming

New threads seed `conversations.model_history` with a pinned two-message exchange containing the
`get_financial_context` snapshot (`pin(snapshot)` followed by an assistant acknowledgment) so
input redaction can safely replace the user's first message without disturbing the snapshot.

The following table summarizes the runtime bounds and adapter settings for interactive turns:

| Setting | Value and behavior | Source |
|---|---|---|
| Compaction target | `target_tokens = min(100_000, window // 2)`, `keep_tokens = max(target // 5, 1)` where `window` reads `LLM_CONTEXT_WINDOW` (default `128_000`) | `src/arno/agent/capabilities.py` |
| Summarization | `SummarizingCompaction` runs on `FallbackModel(primary, backup)` (or `primary` alone) with `keep_messages=20`, `keep_user_messages=True`, `receipts=True`, and `summarization_capabilities=[InferenceContext(), summary_limits]` (non-run budgets only so summarization shares the daily or conversation ledger) | `src/arno/agent/capabilities.py` |
| Summary prompt | `SUMMARY_PROMPT` preserves decisions with exact figures (amounts, floors, dates, rates, rules), row IDs, and open questions while dropping balances, statement totals, and outside text | `src/arno/agent/capabilities.py` |
| Turn limits | `UsageLimits(request_limit=50, tool_calls_limit=100)` and `ModelSettings(timeout=600)` (a 10-minute stream stall guard with no total wall-clock cap) | `src/arno/agent/capabilities.py` |
| Chat adapter | `VercelAIAdapter` with `sdk_version=7` (required for approval cards), `manage_message_history="server"`, and `allowed_file_url_schemes=frozenset()` | `src/arno/api/chat.py` |
| Cancellation | `CancellationToken` stops runs cleanly on user **Stop** or server shutdown so `save_stopped` persists both `model_history` and UI `messages` | `src/arno/agent/turns.py` |

Inside `TieredCompaction`, inner tier triggers (`max_tokens=1`, `max_messages=1`) are ignored in
favor of the outer `target_tokens`. `keep_tokens` protects the newest exchanges while
`keep_messages` caps older verbatim turns.

### Redaction

One detector list in `src/arno/agent/redaction.py` (`redaction_detectors()`) serves
`InputGuardrail`, persisted chat history, thread titles, `RootSpanIO` trace previews, and statement
and photo text redaction:

- **`secret_data()`:** Built-in harness detector for API keys and tokens.
- **`personal_data(only=["credit_card"], extra=...)`:** Detects Luhn-valid credit cards, Indian
  PAN (`[A-Z]{5}[0-9]{4}[A-Z]`), spaced Aadhaar with lookaround guards so 16-digit card numbers
  cannot leak outer digits, and `card_number` (four 4-digit groups starting with 2–6) so Luhn-invalid
  card numbers are still redacted.
- **`keyword_secret_data`:** Redacts keyword-anchored passwords, passcodes, OTPs, PINs, and CVVs
  across English, Hinglish, and Devanagari (in both English and Hindi word orders), while allowing
  plain prose, currency amounts, dates, and postal PIN codes through unchanged.

### Post-reply number check

`src/arno/agent/number_check.py` compares numbers in every assistant reply against tool returns,
tool arguments, `run_code` outputs (including paise-to-rupee conversions), pinned snapshots, and
user messages. `classify()` and `classify_messages()` separate flagged numbers into two classes:

- **`derived`:** A sum or difference of two traced numbers of the same kind in the same reply
  (prose arithmetic, which still violates rule 1).
- **`untraced`:** A number with no matching source in the turn's evidence.

Production chat replies and scheduled answers run the check on `("money", "percent")` (Indian
digit-grouped numbers count as `money` even without `₹`) and attach `UNTRACED_NOTE` when any
figure is flagged. Eval runs check `("money", "percent", "bare")`, catching untraced bare counts as
well as amounts and percentages.

### Memory

Arno does not use a notebook or external memory library. Long-lived user facts live in the typed
`preferences` table (`preference`, `fact`, `rule`), saved with a **Remembered** card and undo,
and managed in **Settings → Agent memory**.

## Tests and evals

Deterministic behavior around the model is verified in `pytest`; answer quality, safety, and model
suitability are measured in `evals/`.

### Unit and integration tests

The Python dev dependency group in [`pyproject.toml`](../../../pyproject.toml) provides `pytest`,
`pytest-asyncio`, `pytest-xdist`, `httpx`, `cbor2`, `cryptography`, `ruff`, `pyrefly`, `pip-audit`,
and `types-python-dateutil`.

`tests/conftest.py` sets `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False`, blocks non-loopback
sockets and DNS, and points every model role at a closed loopback port so tests cannot make paid
requests. Tests drive agent paths with `FunctionModel` scripts and exercise streaming, approvals,
and cancellation through the live FastAPI adapter.

### Agent evals

The eval suite in [`evals/`](../../../evals/README.md) (`evals/cases.py` and `evals/runner.py`)
runs `pydantic_evals` against any configured chat model. It is the only code path that enables
`ALLOW_MODEL_REQUESTS`.

`build_cases()` defines **145 cases** across seven categories:

| Category | Cases | Grading focus |
|---|---:|---|
| Answers | 32 | Ledger accuracy, projection verdicts, date and weekday handling, and number-gate compliance |
| Writes | 23 | Resulting database rows, split and loan math, default naming, and ambiguity handling |
| Honesty | 15 | Refusing missing data, reporting coverage gaps, and distinguishing zero from unknown |
| Advice | 11 | Facts and tradeoffs without product or investment recommendations |
| Never moves money | 10 | Refusing transfer instructions and keeping passwords out of chat |
| Long threads | 4 | Retaining early decisions and exact figures across forced compaction |
| Prompt injection | 50 | Hard gate (zero lasting writes without confirmation) and model fooled rate |

Key harness properties in `evals/runner.py`:

- **Isolated databases:** Each trial runs inside a fresh Postgres database cloned with
  `CREATE DATABASE ... TEMPLATE` from the demo persona and dropped on teardown.
- **Reliability metric:** Default runs use `repeat=3` (`pass^3`—a case passes only when all three
  trials pass) and `max_concurrency=2`.
- **Budget and cost routing:** Each trial uses an `InMemorySpendStore` with a conversation-scoped
  `run_budget_usd` ceiling (default `$2`), while `llm_calls` journal rows route to the installation
  database before the disposable trial database is dropped.
- **Outcome grading:** Graders inspect database state after the last user message rather than
  grading trajectories; non-injection categories simulate user approval on confirm cards while
  injection and never-moves-money categories keep confirm cards pending.

### Tracing and spend accounting

Tracing is opt-in through `PHOENIX_COLLECTOR_ENDPOINT` (`src/arno/observability.py`); when set,
`bootstrap_observability()` registers Phoenix OTLP export and calls `Agent.instrument_all()`, while
vision agents (`importers/vision.py` and `capture/photo.py`) explicitly set
`agent.instrument = False` so images never enter traces. See
[`observability.md`](../operations/observability.md) for tracing setup and span attributes, and
[`cost-tracking.md`](cost-tracking.md) for pricing, spend ceilings, `MeteredModel`, and cost replay.
