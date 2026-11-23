# Model cost tracking and budgets

This specification owns Arno's inference pricing, provider billing modes, native spend budgets,
batch accounting, call metering, and the Phoenix supplied-cost contract; [`plan.md`](../plan.md) §3
owns stack decisions, [`observability.md`](../operations/observability.md) owns Phoenix operations
and cost replay procedures, [`deploy/phoenix-cost/README.md`](../../../deploy/phoenix-cost/README.md)
owns the pinned Phoenix image build, and [`completion-checklist.md`](../completion-checklist.md)
tracks open live-provider and deployment acceptance gates.

## Architecture and limits

Arno prices every supported inference call once through a single pricing module, enforces shared
Postgres spend ceilings through `pydantic-ai-harness`, records metadata-only cost rows in a small
application journal (`llm_calls`), and displays usage and cost totals in Phoenix. Every observation
explicitly distinguishes a measured zero, a catalog estimate, a partial subtotal, and an unknown.

Design boundaries are deliberate:

- **Runaway brakes, not hard invoice caps:** `SpendLimits` checks remaining spend before a request
  and records actual cost after the response. An in-flight call can cross a ceiling, and concurrent
  calls can each pass the pre-check. Arno preserves that contract and bounds output sizes where
  supported instead of building a pre-allocation engine.
- **Phoenix is the sole cost UI:** `llm_calls` preserves cost metadata for export recovery; it is
  not a second budget authority or reporting interface.
- **Best-effort trace content:** Cost metadata can be replayed after a Phoenix outage or crash, but
  lost prompt and tool content cannot be reconstructed, and missing provider usage cannot be turned
  into an exact charge.

## Supported providers and billing modes

Arno supports the provider adapters wired in `src/arno/agent/models.py` and
`src/arno/agent/categorizer.py`. Each role reads `*_PROVIDER` and `*_BILLING_MODE` alongside
`*_BASE_URL`, `*_API_KEY`, and `*_MODEL`.

The following table lists the supported provider identities and how each is wired and priced:

| Provider value | Adapter and transport | Pricing behavior |
|---|---|---|
| `openai` (default) | `OpenAIChatModel` via `OpenAIProvider` | Provider-reported cost when captured, otherwise `genai-prices` for verified endpoints |
| `openai-responses` (`openai_responses`, `responses`) | `OpenAIResponsesModel` via `OpenAIProvider` | Same as `openai`, using Responses API transport |
| `anthropic` | Native `AnthropicModel` with Console API key (including eligible plan-funded Console credits) | `genai-prices` catalog estimate |
| `openrouter` | Native `OpenRouterModel` and `OpenRouterProvider` | Provider-reported cost in `provider_details` (preserving explicit zero by field presence) |
| `opencode` (inferred from `opencode.ai`) | `OpenAIChatModel` with session header and billing capture hooks | Narrow reported fields preserving wallet charge, list value, allowance, billing source, and estimate flag |
| `ollama` (inferred from `ollama` in URL) | `OpenAIChatModel` via `OllamaProvider` | Zero fee only when `*_BILLING_MODE` is `local` or `local_zero`; tokens and call count stay visible |
| `openai-chatgpt` (`chatgpt`) | Native `OpenAIChatGPTProvider` over `OpenAIResponsesModel` | API-equivalent `genai-prices` estimate (not a remaining plan-quota counter) |
| `google` (`gemini`, for `BATCH_PROVIDER`) | Provider batch API (`gemini` normalizes to `google` in `_batch_identity`) | `genai-prices` catalog estimate for batch pre-accounting and settlement |

### Billing modes and configuration checks

Three billing modes are accepted (`src/arno/agent/pricing.py`): `metered` (default), `local` (with
`local_zero` as an alias), and `reported`. There is no subscription billing mode.

Note the distinction between role availability (`RoleConfig.configured` in `models.py`) and zero
pricing (`observe_cost` in `pricing.py`):

- A role counts as **configured** when it has a non-placeholder model name and either a real API
  key, a provider in `{"ollama", "chatgpt", "openai-chatgpt"}`, or a billing mode in
  `{"local", "local_zero"}`.
- A call is priced as **`local_zero`** only when its explicit billing mode is `local` or
  `local_zero`. Neither `provider="ollama"` nor a loopback URL classifies an inference call as free
  without the explicit local billing mode, and unknown pricing is never silently treated as zero.

`reported` is an explicit contract that the endpoint returns its own charge. For other
OpenAI-compatible endpoints, Arno keeps wire compatibility and requires `reported` billing or a
catalog-recognized endpoint URL before enabling paid inference.

### Cohere published-rate exception

Cohere's optional `command-a-plus-05-2026` fallback is absent from the pinned `genai-prices`
snapshot while Cohere publishes a zero API tariff for that exact model on `api.cohere.ai` and
`api.cohere.com`. `pricing.py` records that narrow endpoint-and-model pair as `published_rate`
(`price_revision="cohere-command-a-plus/2026-11-12"`, zero input/output rates). Other models and
resellers do not inherit this exception.

### Anthropic credits and ChatGPT sign-in

Eligible Anthropic plan credits can fund standard Console API calls through `AnthropicModel`. The
direct Claude Agent SDK subscription runtime runs a separate loop and remains
[parked](../planning/parked.md). Arno never extracts OAuth tokens from external coding clients.

For eligible ChatGPT plans (`openai-chatgpt`), `src/arno/agent/chatgpt_auth.py` stores credentials
in `model_credentials` (excluded from `agent_ro` and JSON/CSV exports):

- **Sign-in and sign-out:** `arno chatgpt-login` completes the loopback callback on
  `127.0.0.1:1455`; `arno chatgpt-login --manual --no-browser` accepts a hidden pasted callback URL
  and verifies its `state` parameter for containerized hosts. `arno chatgpt-logout` deletes Arno's
  stored credentials without revoking other apps.
- **Safe rotation and revocation:** Token refresh uses compare-and-swap on the stored refresh
  token so an in-flight rotation cannot restore a disconnected sign-in, and every request checks
  that the row still exists and matches the cached access token.
- **Background consent and no paid fallback:** Jobs, wake turns, and delegated work require
  `--allow-background` consent (`InferenceContext(background=True)`). Constructing an agent with a
  ChatGPT primary and a configured `CHAT_FALLBACK_*` raises `UserError` so quota exhaustion never
  spills onto a paid API key.

## Pricing

`src/arno/agent/pricing.py` owns all inference pricing. `observe_cost(response, provider=..., billing_mode=...)`
returns an immutable `CostObservation` containing normalized usage, an exact `Decimal` USD amount
(never rounded to cents; paise stay in the financial ledger), optional `provider_charge` and
`allowance`, `complete` flag, `source`, `price_revision`, `billing_source`, and effective rates.

Sources apply in strict priority order:

1. **Explicit local zero** (`local` or `local_zero` billing mode).
2. **Provider-reported cost** for the selected billing mode (OpenCode's allowlisted fields or
   captured `provider_usage.cost`, including OpenRouter).
3. **Narrow published-rate exception** (Cohere `command-a-plus-05-2026` on official Cohere hosts).
4. **Installed `genai-prices` snapshot** (`genai-prices==0.2.1` pinned in `pyproject.toml`) for
   verified provider identities and URLs, preserving cache read/write and reasoning token categories
   without double-counting.
5. **Unknown** (`usd=None`, `complete=False`) with no cross-vendor rate substitution.

The shared dollar budget tracks usage value (reported pre-credit or list value when present,
otherwise the catalog estimate) so included credits do not hide consumed usage. For OpenCode Go,
`provider_charge` and `allowance` stay separate from list value (`usd`).

`price_response()` adapts `observe_cost()` to the `SpendLimits(price=..., on_unpriced="raise")`
callback, returning `Decimal | None`. Returning `None` preserves PydanticAI's incomplete-usage path
for `unmeasured_requests` and partial subtotals while raising on unverified endpoints. Pricing
updates ship through lockfile upgrades, and every journal row records its `price_revision` so
upgrades never silently reprice historical calls.

### Provider billing metadata capture

Because generic OpenAI-compatible SDK mappings drop unknown `usage` fields (such as `usage.cost`
and OpenCode's `opencode` block), `src/arno/agent/provider_usage.py` provides HTTP/SSE capture hooks
that `MeteredModel` (`src/arno/agent/metering.py`) activates around every model call:

- **`capture_response` and `CapturingSSEStream`:** Public `httpx2` response hooks inspect JSON
  responses (up to 1 MiB) and wrap `text/event-stream` byte streams to extract only allowlisted
  billing keys (`usage.cost` and OpenCode's `list_cost`, `allowance`, `billing_source`,
  `estimated`, and token splits) without retaining raw provider bodies.
- **`MeteredModel`:** Activates `capture_context()`, attaches captured billing fields via
  `attach_captured_usage()`, and stamps the configured `arno_cost` (`provider`, `billing_mode`)
  marker once onto `response.provider_details` (for both non-streaming and streamed responses)
  before pricing runs.

## Native spend budgets

Every agent and summarizer construction site routes through `spend_limits()` and
`shared_daily_budget()` in `src/arno/agent/spend.py` over `PostgresSpendStore`:

- **Ceilings:** `$5` per interactive chat turn (`window="run"`), `$20` per shared UTC day
  (resets at 05:30 IST), and a configurable per-run ceiling for scheduled questions (default `$1`).
  Compaction and child runs share the outer UTC-day (or eval conversation) ledger.
- **Retention:** The shared daily budget uses `retain="forever"` so delayed batch settlements
  cannot resurrect an expired counter, and replay deduplication tokens persist for seven days.
- **Fail-closed stores:** All uncapped or in-memory fallbacks are removed. A database read or write
  failure stops paid inference and never triggers an automatic model retry. `SpendStoreError`,
  `SpendLimitExceeded`, `UserError`, and `UsageLimitExceeded` (`COST_STOP_ERRORS`) map to
  non-retryable `JobGaveUp` in jobs and propagate unchanged across Monty Code Mode and parser
  sandbox boundaries.
- **Output cap:** Metered requests default to `DEFAULT_MAX_OUTPUT_TOKENS = 8192` unless a caller
  sets `max_tokens`; the native ChatGPT provider omits this cap because its endpoint rejects it.
- **Direct calls (STT):** Non-agent calls use `ensure_shared_day_available()` and
  `post_direct_call()` in `src/arno/agent/spend.py` over public `SpendLimits.status()` and
  `BatchSpendStore.add_many([SpendEntry(...)])` APIs. Transcription preflights both token and
  audio-duration pricing before dispatch and records unknown rather than inventing duration when
  provider usage is missing.

## Batch accounting

Background statement categorization can submit provider batch jobs (`submit_batch` in
`src/arno/agent/categorizer.py`), which incur spend before results arrive. `categorize_runs.accounting`
stores only the recovery state needed for pre-accounting and settlement:

1. **Pre-accounting:** Before enqueuing requests into `BatchModelRequests(max_resubmissions=0)`,
   `estimate_batch_usd` prices a conservative bound per chunk (UTF-8 prompt byte length plus
   `BATCH_PROMPT_OVERHEAD_TOKENS = 4096` and `BATCH_MAX_OUTPUT_TOKENS = 2048`). Unpriceable batch
   models or insufficient remaining daily budget refuse submission.
2. **Durable intent:** Under an import row lock, Arno checks `ensure_no_pending_batch`, records the
   posting intent (`phase="estimate_pending"`) and per-item journal intents in Postgres, then posts
   the estimate to the shared daily counter using a stable `estimate_token`.
3. **Ambiguous failures:** Any failure during submission or acknowledgement marks the batch
   `submit_ambiguous` and raises `JobGaveUp`, blocking duplicate submissions until inspected.
4. **Settlement on collection:** `handle_collect_batch` (`src/arno/importers/categorize.py`) saves
   collected answers first, then posts `actual - estimate` once (supporting negative deltas in
   `SpendEntry.usd`) using `settle_token` within the seven-day deduplication horizon. Unknown batch
   results keep the conservative estimate marked provisional rather than refunding unknown work.
   Collect and decode spans are tagged `logical_request` so they never double-charge.

## Call metering and trace contract

Every inference path—API and CLI chat, compaction summaries, fallbacks, parser and spike runs,
categorizer chunks and batches, statement and receipt vision, STT, weekly briefings, scheduled
questions, and eval trials and judges—routes through `MeteredModel` or `metered_call` in
`src/arno/agent/metering.py`. Hidden SDK retries are disabled (`max_retries=0`) so every dispatch
is accounted accurately.

- **Logical request versus attempt spans:** `MeteredModel` wraps each concrete model beneath
  PydanticAI's native instrumentation and before `ModelFallback` or `FallbackModel` assembly.
  PydanticAI's native model span acts as the logical request parent, while `MeteredModel` emits one
  child attempt span carrying the cost once. A failed primary attempt retains its own unknown row
  even when a fallback succeeds.
- **Vision tracing nuance:** Both `src/arno/importers/vision.py` and `src/arno/capture/photo.py`
  set `agent.instrument = False` so images and extracted content never reach traces. Those calls
  emit only `MeteredModel`'s metadata-only attempt span (and its `llm_calls` row) with no native
  logical parent span.
- **Attribution and cancellation:** `InferenceContext` (`src/arno/agent/capabilities.py`) attaches
  `session.id`, `gen_ai.conversation.id`, and `arno.cost.workload` across nested calls. On
  cancellation, `MeteredModel` bounds journal finalization to `CANCEL_FINALIZE_TIMEOUT = 5.0`
  seconds and records a `partial` or `unknown` observation rather than assuming zero.
- **Cost journal (`llm_calls`):** Migrations `0025`, `0029`, and `0030` define `llm_calls` with
  attempt, trace, and span IDs, role, provider, model, billing mode, workload, project name,
  session ID, normalized usage, USD amount, source, completeness, rate revision, and export status.
  It stores no prompts, replies, documents, or credentials, has no `agent_ro` grant, and is never
  exported in JSON/CSV data dumps. Eval trials route journal rows to the installation database
  (`use_journal_database`) before dropping disposable financial clones.
- **Cost-only replay:** Every five minutes the scheduler checks for finalized rows older than
  `REPLAY_GRACE_SECONDS = 300` that have not exported, and creates a `cost_replay` job only when one
  is waiting (it can be run manually via `arno cost-replay`, with `--finalize-interrupted` and
  `--attempt <id>`) using the original trace and span IDs over the standard OTLP exporter. See
  [`observability.md#cost-journal-and-replay`](../operations/observability.md#cost-journal-and-replay)
  for operational replay procedures.

## Phoenix cost customization

Stock Phoenix 21.0.0 ignores supplied cost attributes and can sum unknown calls as `$0`. Arno pins
and builds `arno-phoenix-cost:21.0.0` from
[`deploy/phoenix-cost/`](../../../deploy/phoenix-cost/README.md) to enforce four contract rules
while keeping Phoenix's Elastic License 2.0 source completely outside Arno's permissively licensed
application image:

- Prefer valid supplied `llm.cost.*` attributes (including explicit zero) over catalog
  recalculation, validating finite non-negative amounts.
- Preserve cost source, completeness, billing mode, and rate revision on spans without inventing
  missing input/output breakdowns.
- Honor `unknown` and `partial` completeness and expose unresolved call counts beside totals across
  span, trace, session, project, and time-range views so unpriced calls never render as `$0`.
- Exclude `logical_request` parent spans and batch collect/decode spans from cost and token
  rollups, and permit audio inference costs without fabricated text tokens.

Phoenix's own per-span token columns sum every span's `llm.token_count.*`, which the patch does
not change, so Arno's exporter drops token and cost attributes from `logical_request` spans before
export (`UsageOnceExporter` in `src/arno/observability.py`). The metered attempt span alone carries
a model call's counts, so trace and session token totals count each call once.

Existing Phoenix tables, GraphQL types, and OTLP ingestion are reused without custom receipt
endpoints or correction schemas.

## Deferred scope

The following items are deliberately excluded unless real usage demonstrates a need:

- Dedicated LLM gateway or proxy service.
- Hard spend pre-allocation or lease engine.
- Self-hosted pricing catalog mirror or background rate downloader.
- Automated provider-invoice reconciliation or provider quota enforcement.
- Direct Claude Agent SDK subscription runtime.
- Browser-side audio transcoding or custom media decoders.
- Custom Phoenix receipt schemas, durable full-trace delivery, or a second cost dashboard.

Open live-provider parity, real ChatGPT sign-in, and owner-deployment acceptance items are tracked
in [`completion-checklist.md`](../completion-checklist.md).
