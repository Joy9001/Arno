# Model cost accounting and gateways: Native stack, Phoenix, and alternatives

Research note, November 12, 2026. Read-only: no paid model calls, no evals, no gateway deployment,
no invoice reconciliation. Vendor claims are labeled; source behavior is what the pinned code does.

## Scope and method

The question is what a turn cost, whether the daily ceiling is meaningful, and whether the number
can survive an invoice. This note reads Arno's current files and pinned dependencies, Phoenix at
image tag `21.0` (commit `6359d6388815d56f7795a3a8cd11e89a71793e01`), and primary sources for each
alternative.

## What Arno has today

Arno currently implements the following boundaries.

| Area | Current behavior | Evidence |
|---|---|---|
| Budget gate | `SpendLimits`, `$5` per run, `$20` shared per day | [`capabilities.py`](../../../src/arno/agent/capabilities.py) |
| Persistence | `PostgresSpendStore`, one counter for every agent path | [`spend.py`](../../../src/arno/agent/spend.py) |
| Pricing | genai-prices `0.2.1`, bundled snapshot, no update | [`uv.lock`](../../../uv.lock) |
| Unpriced models | Harness default `on_unpriced='zero'` | `SpendLimits` default |
| Trace | Phoenix `21.0`, native `gen_ai.*` spans | [`observability.md`](../../arno/observability.md) |
| STT | Raw `httpx` POST; no `SpendLimits`, no usage parsed | [`transcribe.py`](../../../src/arno/capture/transcribe.py) |

The `$0.02` figure has no token breakdown
([`system-design.md`](../../arno/system-design.md#latency-and-cost)). Local genai-prices `0.2.1`
calculations, not billed traffic, put a 20,000-prompt/18,000-cached/500-output turn at `$0.0170` on
`gpt-5.4`; 80,000/70,000/800 is `$0.0545`, and 20,000 uncached is `$0.0575`. It fits a short cached
turn, not an average.

`SharedSpendStore` is fail-open: when Postgres is unavailable it reads zero, drops writes, and runs
uncapped. The gate is admission, not reservation: a request crossing the ceiling completes and
concurrent runs can each pass before any records, which the harness calls "a brake on a runaway
loop, not an accounting ledger." SDK and HTTP retries are uncounted; a cancelled stream is charged
only for reported usage.

## Phoenix at the pinned version

### Catalog and updates

The image bundles `cost_tracking/manifests/v1.json`: 399 models, generated `2026-10-30T01:19:43Z`,
394 with `"source": "litellm"` and 5 manual. The `gpt-5.4` entry is `$2.50/M` input, `$15/M` output,
`$0.25/M` cache read.

At startup Phoenix applies that manifest only if newer than its `system_state` record, then
[fetches](https://github.com/Arize-ai/phoenix/blob/6359d6388815d56f7795a3a8cd11e89a71793e01/src/phoenix/server/daemons/model_cost_manifest_fetcher.py)
the `main`-branch copy from GitHub about daily. A fetched manifest applies only if its
`generated_at` is newer; custom models are untouched; a failed fetch keeps stored prices.
`PHOENIX_DISABLE_MODEL_COST_AUTO_UPDATE=true` or `PHOENIX_ALLOW_EXTERNAL_RESOURCES=false` disables
the fetch. Arno sets neither, so its pinned image is a pinned program with an unpinned price feed.
The trace stores a dollar amount, not the revision or rate; updates affect new spans only.

### Matching, custom models, and identity

Costing runs only for an `LLM` span with a non-empty `llm.model_name`. It matches by regex, prefers
user-defined entries, ranks by regex specificity, later `start_time`, and a tie-breaker, and filters
by `llm.provider` when a provider-specific entry exists.

Arno builds `OpenAIProvider` for every non-Ollama endpoint. Its provider name is literally `openai`,
and Pydantic AI writes `gen_ai.provider.name=openai` plus the base URL hostname. Phoenix maps that
to `llm.provider=openai`, so OpenRouter, Groq, and a self-hosted LiteLLM proxy all look like OpenAI;
an entry keyed to `openrouter` will not match. Phoenix prices `gen_ai.request.model`; the response
model goes into `output.value`, so a router's actual model is not priced.

### Supplied costs, cache, and reasoning

At 21.0 Phoenix ignores client-supplied cost. Ingestion calls `should_calculate_span_cost` then
`SpanCostCalculator`; no `llm.cost.*` attribute is read. Those attributes may be stored with the
span but do not enter `span_costs` and have no precedence. A supplied cost has no effect.

Cache accounting is better. `gen_ai.usage.input_tokens` becomes `llm.token_count.prompt`; cache
read/write tokens become prompt details. The calculator prices each detail at its own rate and the
remaining prompt tokens at the input rate. Pydantic AI reports input inclusively, the convention
Phoenix expects. Two details are lost: reasoning tokens are not mapped to
`completion_details.reasoning`, and one-hour cache writes are not distinguished from five-minute
writes. Reasoning is still part of output tokens, so it is priced at the output rate; the one-hour
split matters for Anthropic.

### Unknown, zero, and free

An unknown model produces `model_id NULL`, detail costs `NULL`, and `total_cost NULL`. Trace and
session aggregates use `coalesce(sum(total_cost), 0)`, so the UI shows `$0`. A free model is an
entry with zero rates: `model_id` set and `total_cost` `0`. They differ in the database by
`model_id`, but not in the trace list or project total. "No price" reads as "free" on screen. That
is a serious fit problem for Arno, which supports local Ollama and arbitrary compatible endpoints.

### Backfill, duplicates, retention

Cost is computed at insertion from the span's start time. Built-in entries have no `start_time` (0
of 399), so an old span arriving today uses today's rates. Custom entries may be dated. Phoenix
never recalculates after an update, so a backfill does not reproduce historical prices.

Duplicate ingestion is safe: spans use `unique_by=("span_id",)` with `ON CONFLICT DO NOTHING`; a
retried export does not double-count. The inverse is not safe: a dropped export loses the span and
its cost silently. Retention defaults to 0 days (infinite); a policy deletes traces and `span_costs`
cascade. Phoenix's database and volume are outside the ledger backup, so cost history is deletable
and not recoverable state.

### Why the trace UI is not the budget authority

It runs after the request and cannot stop it. It prices with a mutable catalog and records no
revision, so a number is not reproducible. It reports unknown as zero in aggregate. It loses spans
on export failure and does not see SDK retries. A cancelled stream is charged only for reported
usage. Its data is not backed up. `llm.provider` is the API family, not the billing entity. Phoenix
is good for "which model is expensive" and "did this loop"; it is wrong for "has the ceiling been
reached" or "does this match the invoice."

## The native stack

### genai-prices

`genai-prices` `0.2.1` bundles a price table and calculator. `UpdatePrices` is opt-in, downloads
`v2/data.json` from `main` hourly, keeps the last good copy on failure, and never reverts to the
bundled data. Pydantic AI's `prices.update_in_background()` wraps it; Arno does not call it.

There is no documented per-model registration API. Version 0.2.1 publicly exports `DataSnapshot` and
`set_custom_snapshot`, an undocumented hook that replaces the process-wide snapshot
([source](https://github.com/pydantic/genai-prices/blob/v0.2.1/packages/python/genai_prices/data_snapshot.py)).
That option deserves a compatibility test; `SpendLimits.price` is the practical supported hook for
supplying an amount to the budget without replacing the catalog. The LiteLLM relationship is
build-time: maintainers read LiteLLM, Helicone, OpenRouter, and llm-prices data to detect
discrepancies and populate their own YAML. "Use the LiteLLM catalog" means adding LiteLLM or writing
a converter.

Provider resolution is better than a model id. `calc_price` accepts `provider_id` or
`provider_api_url`; Pydantic AI tries the base URL first, then the provider name, which is why an
OpenRouter endpoint can price correctly even when the trace says `openai`. `PriceContext` selects
variants such as `service_tier`, `processing: batch`, `speed: fast`, or `search_type`; the timestamp
selects dated changes. Unknown models raise `LookupError`, which Pydantic AI degrades to `None`. The
catalog is a good estimate, not billing truth; it warns prices "will not be 100% accurate."

### Pydantic AI and the harness

`ModelResponse` carries `usage.cost` (`None` when unknown), `provider_name`, `provider_url`,
`model_name`, `model_id`, and `provider_details`. `ModelResponse.cost()` always uses genai-prices. A
supported gateway's reported cost is placed on `usage.cost` and can win for core `UsageLimits`;
`cost()` still returns the registry estimate. The installed harness does not use that amount for an
ordinary successful response without a `price=` callback; the [offline
reproduction](verification.md#installed-boundary-reproduced) demonstrates this. For Google and
Bedrock the documented header-cost path is non-streamed only.

Pydantic AI's `OpenRouterModel` reads `usage.cost` into `provider_details`; it does not set
`usage.cost`. Arno's generic `OpenAIProvider` path does not use that model, so it neither collects
nor uses the field.

The installed harness tries `price` first, uses `usage.cost` for incomplete usage or a rejected
fallback attempt when available, and normally calls `ModelResponse.cost()`. Assigning an amount to
`usage.cost` alone is insufficient for ordinary successful responses. If nothing prices, it records
zero and increments `unpriced_requests`. `on_unpriced='raise'` would break local Ollama use; the
right shape is a resolver that knows which endpoints are free.

`PostgresSpendStore` applies every window for a response in one transaction with dedup markers, a
replay-stable token, server-clock TTLs, and a sweep. Admission reserves nothing, concurrency can
overshoot, provider retries are outside the count, and a cancelled stream is charged only for
reported usage. Arno's wrapper adds fail-open behavior when Postgres is down.

## Alternatives

**Native catalog plus hooks.** No new process or additional prompt recipient; Postgres stays
authoritative. A resolver records the amount and its meaning, then supplies a `Decimal` through the
existing `price` callback: provider-reported cost, a configured local endpoint as free, catalog
estimates for known cloud models, and `None` when truly unknown. Unknown must stay distinguishable
from free; strict admission must not treat it as a free request.

**Pydantic AI Gateway.** Gets actual cost and a server limit in one place, with BYOK, failover, and
native OTel. Hosted or commercial self-hosted Logfire. Routing every prompt through Pydantic Cloud
adds a prompt recipient. Its limits need an explicit authority relationship with Arno's local
budgets rather than being treated as automatically identical.

**Bifrost and Helicone.** Bifrost is Apache-2.0 and uses Maxim's datasheet, not LiteLLM. It has real
virtual-key budgets, but its docs say OSS keeps critical state in memory and does not support
multiple OSS nodes with Postgres; restart durability is unstated and must be tested. Unknown pricing
returns `0.0`, the same unknown-as-free failure. Helicone is Apache-2.0 and estimates from its own
table; its self-host is a multi-component stack whose all-in-one image wipes data without volumes
and has no gateway routes. Neither beats Phoenix plus a price function.

**LiteLLM.** A broad catalog: the proxy fetches `model_prices_and_context_window.json` from GitHub
`main` at startup and reports the loaded git blob as `source_revision`. It supports a local-only
map, custom pricing, cache and tier variants, and spend logs; a `$0` request on a priced model is
logged and counted. The core is MIT; `enterprise/` is separately licensed. As a gateway it adds a
proxy, database, keys, and a second ledger. Its subscription feature connects personal Claude,
ChatGPT/Codex, or Copilot plans to coding clients; that is not a per-request cost model.

**OpenRouter reported cost.** Returns `usage.cost` on every response and `total_cost`,
`upstream_inference_cost`, `provider_name`, and `provider_responses` from `GET /generation`. That is
actual charged cost and the best reconciliation source if traffic already routes through it; it can
feed a `price` callback. It is not a reason to switch providers, and BYOK upstream cost is not
always populated.

## Requirements

1. **Cost status is explicit:** _actual_, _estimated_, _free_, or _unknown_. Unknown may be zero
   for the brake, never for the ledger.
2. **Billing identity is a tuple:** API family, endpoint or account, requested model, response
   model, and generation id where available. A model id cannot distinguish providers.
3. **Usage completeness is a flag:** cache read/write, reasoning, audio duration, omitted usage. An
   incomplete record is a lower bound.
4. **Price revision is auditable:** catalog name and revision, price context, computed amount. A
   later update must not change a past row. Reconcile a period and record the variance.
5. **Limits are durable and their guarantees stated:** one transaction per response, dedup on
   replay, explicit retries, cancellation, and concurrency. Admission is not reservation; document
   the overshoot bound.
6. **Observation and authority stay separate:** Phoenix for inspection, Postgres for decisions,
   the invoice for truth.

## What exists already versus required work

The following work remains for reliable cost accounting.

| Capability | Status | Work |
|---|---|---|
| Per-response estimate | Exists | Add source label |
| Unknown distinct from zero | Partial | Harness records zero |
| Free local models | Implicit | Make explicit |
| Provider identity | Missing | Add endpoint, provider, response model |
| Price revision | Missing | Record revision and context |
| Actual provider cost | Partial | OpenRouter field unused |
| Durable shared counter | Exists | Document fail-open |
| STT metering | Missing | Parse `usage` or mark unknown |
| Cancellation and retries | Limitation | Measure and flag |
| Invoice reconciliation | Missing | Add variance record |

## Validation gates

1. **Pricing fixture.** Synthetic responses with known usage; exact decimal USD for a known model,
   an
   unknown model, a free endpoint, and an OpenRouter-shaped cost. Unknown stays unknown; actual
   wins over estimate.
2. **Phoenix golden trace.** One synthetic span with known tokens and a supplied
   `llm.cost.total`; assert Phoenix uses its catalog and ignores the supplied value.
3. **Catalog revision.** Change the table between two fixtures; the earlier row does not change.
4. **Cancellation.** A stub reports usage only at the end; cancel mid-stream; the shortfall is
   flagged.
5. **STT usage.** A stub returns OpenAI's transcription `usage`; the duration is recorded or
   explicitly unknown.
6. **Invoice reconciliation.** One period by provider and model. Define tolerances from provider
   rounding and reporting scope before testing; explain variance rather than choose an arbitrary
   percentage as proof of correctness.

## Sources

These primary sources support the findings.

| Source | Supports | Kind |
|---|---|---|
| [Phoenix cost calculator](https://github.com/Arize-ai/phoenix/blob/6359d6388815d56f7795a3a8cd11e89a71793e01/src/phoenix/server/cost_tracking/span_cost_calculator.py) | Cost from attributes | Source |
| [Phoenix model lookup](https://github.com/Arize-ai/phoenix/blob/6359d6388815d56f7795a3a8cd11e89a71793e01/src/phoenix/server/cost_tracking/cost_model_lookup.py) | Matching order | Source |
| [Phoenix manifest](https://github.com/Arize-ai/phoenix/blob/6359d6388815d56f7795a3a8cd11e89a71793e01/src/phoenix/server/cost_tracking/manifests/v1.json) | 399 undated entries | Data |
| [Phoenix conversion](https://github.com/Arize-ai/phoenix/blob/6359d6388815d56f7795a3a8cd11e89a71793e01/src/phoenix/trace/gen_ai/conversion.py) | Provider mapping | Source |
| [Phoenix insertion](https://github.com/Arize-ai/phoenix/blob/6359d6388815d56f7795a3a8cd11e89a71793e01/src/phoenix/db/insertion/span.py) | Duplicate handling | Source |
| [genai-prices](https://github.com/pydantic/genai-prices) | Catalog and update | Vendor |
| [genai-prices data](https://github.com/pydantic/genai-prices/blob/main/prices/README.md) | LiteLLM discrepancy check | Repo docs |
| [Gateway cost](https://pydantic.dev/docs/ai/gateway/#request-cost) | Cost precedence | Vendor |
| [Logfire Gateway](https://pydantic.dev/docs/logfire/manage/ai-gateway/) | Hosted vs self-hosted | Vendor |
| [Bifrost catalog](https://docs.getbifrost.ai/architecture/framework/model-catalog) | Datasheet, unknown 0.0 | Vendor |
| [Bifrost budgets](https://docs.getbifrost.ai/features/governance/budget-and-limits) | In-memory OSS | Vendor |
| [Helicone cost](https://docs.helicone.ai/references/how-we-calculate-cost) | Own table, stream flag | Vendor |
| [LiteLLM cost map](https://docs.litellm.ai/docs/proxy/model_cost_map) | Map, zero-cost | Vendor |
| [LiteLLM sync](https://docs.litellm.ai/docs/proxy/sync_models_github) | `source_revision` | Vendor |
| [OpenRouter usage](https://openrouter.ai/docs/use-cases/usage-accounting) | `usage.cost` | API |
| [OpenRouter generation](https://openrouter.ai/docs/api/api-reference/generations/get-request-&-usage-metadata-for-a-generation) | `total_cost` | API |

## Strongest objections, unknowns, and what was not tested

The strongest objection is that this is a one-user app with a `$20` brake, so full accounting is
overkill. Partly true: the first change is a `price` function and three recorded facts, not a
gateway. But `$0` for an unknown cloud model makes the brake and the screen disagree, and the user
cannot tell a cheap month from an unpriced one.

The second is that genai-prices is good enough and calling it unreliable is wrong. True: the defect
is Arno's handling of a miss. The third is that Phoenix's display is fine for "what did this run
cost" and only misused as accounting. Also true: keep it for inspection, not as a ledger.

Unknowns: Bifrost restart durability, whether Arno's cloud STT returns usage, whether batch pricing
context is applied, and the real cancellation undercount. One inference: because `OpenAIProvider`
reports `openai`, an aggregator-only model absent from the OpenAI catalog falls through both lookups
and records `$0`. A local check confirmed both forms raise `LookupError`; the live configuration was
not tested.

**Not tested:** any live model request or invoice; a running Phoenix ingesting a real Pydantic AI
trace; a deployed Bifrost, Helicone, or LiteLLM; OpenRouter's cost through Arno's generic provider;
STT usage capture; a cancelled stream's real charge; and a catalog update under load. Phoenix
findings come from the pinned tag, not from operating the image.
