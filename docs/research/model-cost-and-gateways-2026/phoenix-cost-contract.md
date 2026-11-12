# Phoenix cost contract

The later [complexity audit](complexity-audit.md) narrows the implementation to immutable supplied
observations, native export, and a focused Phoenix ingestion/UI patch. Proposals below for receipt
version history and correction APIs belong to the superseded larger draft, not the current plan.

Research note, November 12, 2026. This note owns one question: what Phoenix can be made to show as
Arno's only cost dashboard, and what must change for it to be exact. It proposes no Arno dashboard,
no second stack, and no gateway. No paid request, eval, credential, or live-owner data was used.

Versions inspected: Phoenix `21.0.0`, commit
[`6359d63`](https://github.com/Arize-ai/phoenix/tree/6359d6388815d56f7795a3a8cd11e89a71793e01)
(2026-10-30), the newest `arize-phoenix` release; Phoenix `main` at
[`26f422f`](https://github.com/Arize-ai/phoenix/tree/26f422f20ead8a59df366497373e3e0e4813810b)
(2026-11-12); Pydantic AI `2.64.0`; `openinference-semantic-conventions` `0.1.43`;
`arize-phoenix-otel` `0.17.2`; OpenTelemetry SDK `1.45.2`. Links to `main` are unreleased behavior;
the tag is what Arno runs.

## The acceptance contract, item by item

| Requirement | Stock 21.0 | Cause |
|---|---|---|
| Every known-provider LLM call appears | Yes, if the call produced an LLM-kind span with a model name | `should_calculate_span_cost` gate |
| Every STT call appears | Only if Arno emits a span for it; Phoenix creates none | Arno has no STT span |
| Tokens per call | Yes | `llm.token_count.*` |
| Cost source per call | No | No field, no attribute read |
| Exact chosen estimate or reported charge | No | Ingestion prices from its own manifest |
| Explicit local zero | No, indistinguishable from unknown | Both are "no cost" |
| Unknown or partial, visible | No; counted as `$0` in aggregates | `coalesce(sum(...), 0)` |
| Trace, session, project aggregates | Yes | Cost dataloaders |
| No double counting | Only if one LLM span per attempt | Nested LLM spans double count |

## What the pinned server actually does

Costing happens once, at ingestion, only for spans that pass a narrow gate. `bulk_inserter.py`
calls `_calculate_span_cost` before writing, and `should_calculate_span_cost` requires
`openinference.span.kind == LLM` plus a non-empty `llm.model_name`
([`helpers.py`](https://github.com/Arize-ai/phoenix/blob/6359d6388815d56f7795a3a8cd11e89a71793e01/src/phoenix/db/insertion/helpers.py)).
An agent-run span is `AGENT`, so Pydantic AI's `invoke_agent` span is never priced and cannot double
count its children. Batch categorization runs through `build_labeling_agent`, so those calls are
covered like any other agent run; STT is a raw HTTP call and produces no span at all.

The calculator never reads a supplied cost. `SpanCostCalculator.calculate_cost` looks up a
`GenerativeModel`, builds a `SpanCostDetailsCalculator` from that model's `TokenPrice` rows, and
sums `tokens × base_rate`
([`span_cost_calculator.py`](https://github.com/Arize-ai/phoenix/blob/6359d6388815d56f7795a3a8cd11e89a71793e01/src/phoenix/server/cost_tracking/span_cost_calculator.py)).
No `llm.cost.*` key is read on that path, and `main` is byte-identical for this file, the details
calculator, the gate, and `bulk_inserter.py`. Upstream tracks the gap as an open issue,
[#15240](https://github.com/Arize-ai/phoenix/issues/15240) (opened 2026-08-09, updated 2026-10-08),
which proposes precedence, partial input, and a recorded source; no PR is linked and no milestone is
set, so upstream timing is not committed.

Model selection is a regex and an exact provider string. `CostModelLookup.find_model` matches
`llm.model_name` against each entry's `name_pattern` with `search()`, filters by
`start_time <= span start`, prefers user-defined entries over built-in ones, then prefers an entry
whose `provider` equals `llm.provider` exactly, then ranks by regex specificity, start time, and id
([`cost_model_lookup.py`](https://github.com/Arize-ai/phoenix/blob/6359d6388815d56f7795a3a8cd11e89a71793e01/src/phoenix/server/cost_tracking/cost_model_lookup.py)).
A provider-agnostic entry is the fallback, not a wildcard.

Two defects follow. First, Phoenix prices `gen_ai.request.model`, because the converter
writes that value to `llm.model_name` and puts the response model inside `output.value`
([`conversion.py`](https://github.com/Arize-ai/phoenix/blob/6359d6388815d56f7795a3a8cd11e89a71793e01/src/phoenix/trace/gen_ai/conversion.py)).
A router that silently answers with a different model is priced as the model Arno asked for. Second,
Arno builds `OpenAIProvider` for every non-Ollama endpoint, so `gen_ai.provider.name` is `openai`
for OpenCode, OpenRouter, Groq, and a local proxy alike; an entry keyed to `openrouter` cannot
match.

Unknown cost is stored as `NULL` and displayed as zero. A span with tokens but no matching price
still gets a `SpanCost` row whose detail costs are `None`, so `total_cost` stays `NULL`; the project
dataloader sums with `coalesce(func.sum(...), 0)`
([`span_cost_summary_by_project.py`](https://github.com/Arize-ai/phoenix/blob/6359d6388815d56f7795a3a8cd11e89a71793e01/src/phoenix/server/api/dataloaders/span_cost_summary_by_project.py)).
The cost chart distinguishes "no data" from "tokens recorded, no price" in its empty-state text
([`tokenDetails.ts`](https://github.com/Arize-ai/phoenix/blob/26f422f20ead8a59df366497373e3e0e4813810b/js/app/src/pages/project/metrics/tokenDetails.ts)),
but the number itself is `$0`. An unpriced count is therefore cheap: `count(*) where total_cost is
null`.

Aggregation is inconsistent for nested LLM spans. Token totals count only LLM spans that have no LLM
child, to avoid a wrapper repeating its child's tokens
([`token_counts.py`](https://github.com/Arize-ai/phoenix/blob/6359d6388815d56f7795a3a8cd11e89a71793e01/src/phoenix/server/api/dataloaders/token_counts.py)),
while the cost loaders sum every `SpanCost` row. Issue
[#17856](https://github.com/Arize-ai/phoenix/issues/17856) (2026-11-11, still open) reproduces a
project that shows 1,200 tokens and twice the cost on 21.0.0. The rule for Arno is therefore
mechanical: exactly one LLM span per billable attempt, and no LLM span wrapping another.

Custom entries are an API, not only a form. `createModel`, `updateModel`, and `deleteModel`
accept a name, an optional provider, a regex, per-million rates by token type, and a `start_time`
([`model_mutations.py`](https://github.com/Arize-ai/phoenix/blob/6359d6388815d56f7795a3a8cd11e89a71793e01/src/phoenix/server/api/mutations/model_mutations.py)).
Updating deletes and re-inserts the price rows, so the prior rate is not retained anywhere. Nothing
on `SpanCost` records which revision applied: only `model_id` and `span_start_time`. A price edit
never recalculates old rows, and there is no way to reconstruct the rate a historical row used.

The UI surface is wider than the docs and narrower than the contract. The spans table
has tokens, total cost, and metadata columns; the trace header, session view, project summary, top
models by cost, and a project cost time series all exist in 21.0. Filters can read any span
attribute, for example `attributes['arno.cost.source'] == 'reported'`, and `metadata[...]` is
shorthand ([filter docs](https://github.com/Arize-ai/phoenix/blob/26f422f20ead8a59df366497373e3e0e4813810b/docs/phoenix/tracing/how-to-tracing/filter-expressions.mdx)).
Extra columns come only from annotations, not from arbitrary attributes
([`SpanColumnSelector.tsx`](https://github.com/Arize-ai/phoenix/blob/26f422f20ead8a59df366497373e3e0e4813810b/js/app/src/pages/project/SpanColumnSelector.tsx)).
So provenance can be filtered and read in the span aside, but it cannot become a column or enter a
rollup without a server change.

## What native instrumentation supplies

`Agent.instrument_all()` (or the `Instrumentation` capability) emits `invoke_agent`, `chat`, and
`execute_tool` spans
([official docs](https://pydantic.dev/docs/ai/capabilities/instrumentation/)). On `chat` it sets
`gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`,
`gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, and cache-read and cache-write counts. It
also sets `operation.cost`, a float in USD, from `response_cost()`, which prefers
`response.usage.cost` when set and otherwise uses genai-prices
([`_instrumentation.py:803`](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/pydantic_ai/_instrumentation.py)).
The same value is recorded on an `operation.cost` histogram. `operation.cost` is not an
OpenTelemetry or OpenInference convention, the Phoenix converter does not map it, and the server has
no OTLP metrics receiver on the traces route, so both the attribute and the metric are inert for
cost.

The converter does pass unknown provider names through, so `llm.provider` can be corrected at the
source. It also synthesizes `llm.token_count.*` and preserves attributes the span already carries;
`_merge_synthesized_attributes` never overwrites an existing key
([`otel.py`](https://github.com/Arize-ai/phoenix/blob/6359d6388815d56f7795a3a8cd11e89a71793e01/src/phoenix/trace/otel.py)).
Reasoning tokens are not mapped to `completion_details.reasoning`; they remain inside output tokens
and are priced at the output rate. One-hour and five-minute cache writes are not distinguished.

## Three options

**A. Custom model entries only (stock).** Register one entry per billing profile, set `llm.provider`
to that profile, and let Phoenix compute `tokens × rate`. This fixes provider ambiguity and gives a
defensible per-call estimate. It cannot express peak and off-peak rates, a reported charge, an
allowance debit, a fixed fee, or a partial result, and it cannot mark a miss as unknown rather than
zero. It is an interim, not the acceptance path.

**B. Upstream supplied cost (recommended).** Consume OpenInference cost attributes with precedence.
It is the only stock-shaped change that satisfies the contract without a second dashboard, fake
tokens, or a synthetic model.

**C. Rejected.** Direct writes into the Phoenix database break its schema contract and replay
semantics. A duplicate "spend" span double counts. A synthetic unit-price model or fabricated token
counts corrupt every rollup. Misnaming the model to encode a price tier corrupts model attribution
and the trace's record of the request. A separate Arno dashboard or collector violates the
single-dashboard and single-stack constraints.

## Candidate upstream change and its limits

The source suggests the following starting changes. They are not a demonstrated complete fix:

1. `span_cost_calculator.py`: read `llm.cost.total`, then `llm.cost.prompt` and
   `llm.cost.completion`. Validate finite nonnegative values, preserve a supplied total, and sum
   components only when the breakdown is known complete. Keep incomplete components partial.
   Otherwise use the manifest path unless the span explicitly declares unknown pricing. Preserve
   provenance independently of the model-table foreign key. Precedence, not merge.
2. `SpanCostSummary` and the project/session/trace dataloaders: add `unpricedCount` (rows with
   `total_cost IS NULL`) and an explicit provenance/completeness split. A null `model_id` cannot
   mean reported: an application can supply an estimate. Partial breakdowns also need a distinct
   status rather than being promoted to complete totals.
3. UI: show the split and the unpriced count beside the existing project and session cost summaries,
   and label a span's cost in `SpanTokenCostsDetails`.
4. Tests: precedence, partial input, zero, and absence; a dataloader test for the unpriced count; a
   GraphQL schema snapshot.

Stock ingestion accepts cost only on `LLM` spans. The implementation plan changes that gate for
validated inference receipts so STT can retain its real modality without polluting language-model
rollups. Tokens remain absent when duration is the billable unit.

Release gate, runnable without a model: POST one synthetic span with `llm.cost.total = 0.0000924`
and assert the GraphQL `span.costSummary.total.cost` and `project.costSummary` both return that
value with `model_id` null; POST a second span with tokens and no cost and assert `unpricedCount`
increases while the total is unchanged. If a release does not pass, the acceptance item stays open.

The final plan uses concrete-provider wrappers and child attempt spans: one native request span
can enclose several fallback attempts. Native logical parents are excluded from cost/token
rollups. Core attempt attributes include `llm.cost.total` and:
`arno.cost.source` (`reported`, `estimated`, `local_zero`),
`arno.cost.price_revision`, `arno.cost.billing_source` (`balance`, `go`, `free`, `byok`), and
`arno.cost.partial`. STT and externally submitted batch items also use real inference spans, with
no fabricated tokens. The implementation plan specifies the complete attribute, correction,
delivery, and UI contract, including a service-owned observation migration and receipt API.

The reported value exists on the wire. OpenCode documents `usage.cost` (dollars charged to balance,
`0` inside a Go plan), `opencode.billing_source`, `opencode.list_cost` (list price including peak
and long-context), `opencode.estimated`, `opencode.allowance` (dollars of the plan's monthly limit),
and `opencode.tokens`, with `input` excluding cache and `output` including reasoning
([official docs](https://opencode.ai/docs/console/api/inference/)). DeepSeek V4.1 Flash on Go is
`$0.15/$0.60` per million off-peak and `$0.30/$1.20` peak, with peak defined as 01:00-04:00 and
06:00-10:00 UTC Monday to Friday, on a `$100` monthly allowance
([Go docs](https://opencode.ai/docs/console/go/)). The `$10` monthly fee, the `$100` allowance, and
`list_cost` are three different quantities: a fixed charge, a quota debit, and a shadow price.
Phoenix can show the last two per call; the fee must never enter a per-call total.

## Worked arithmetic

One call: 1,000 input tokens including 800 cache reads, 100 output tokens including 30 reasoning.

| Basis | Calculation | Result |
|---|---|---|
| Off-peak reported or list | `(200×0.15 + 800×0.003 + 100×0.60)/10^6` | `$0.0000924` |
| Peak reported or list | `(200×0.30 + 800×0.006 + 100×1.20)/10^6` | `$0.0001848` |
| Phoenix, custom entry at off-peak rates with a cache rate | same as off-peak | `$0.0000924` |
| Phoenix, custom entry at off-peak rates with no cache rate | `(1000×0.15 + 100×0.60)/10^6` | `$0.00021` |

The last row is the silent error: cache reads fall back to the input rate, so a cached
turn is overstated by 2.3× and a peak turn by 1.1×. Reasoning needs no separate line; it is inside
output tokens, and charging it again would double count.

## Delivery, retention, privacy

Export is per-span and synchronous. Arno calls `register(endpoint=...)` without `batch=True`, and
the installed SDK `0.17.2` defaults to `batch=False`, which installs a `SimpleSpanProcessor`
([`otel.py:69`](https://github.com/Arize-ai/phoenix/blob/26f422f20ead8a59df366497373e3e0e4813810b/packages/phoenix-otel/src/phoenix/otel/otel.py)).
Each ended span goes to the OTLP/HTTP exporter on the request path. The installed OTel 1.45.2 HTTP
client retries transient failures within its timeout/deadline, using backoff and retry headers;
final failure still drops the span. There is no durable queue. A hung Phoenix can therefore stall
a turn, and a crash loses in-flight exports. `batch=True` moves export to a background thread and
a bounded memory queue, but does not make it crashproof. The implementation plan instead requires
a Postgres cost outbox, drained through standard OTLP, with idempotent recovery. Budget authority
remains independent of Phoenix delivery. These delivery changes have not been implemented.

Replay is safe per span. `insert_span` uses `ON CONFLICT DO NOTHING` keyed on `span_id` and returns
`None` for a duplicate, and the cost row is written only for a newly inserted span, in the same
transaction, so a replayed span does not double count. A retried HTTP attempt is a new span and
should count, because it may have been billed. The inverse is not safe: a dropped export loses the
span and its cost with no signal in the totals.

One retry gap remains. The OpenAI client under `OpenAIProvider` retries by default and Arno does not
set `max_retries`, so a retried-then-succeeded request is one `chat` span reporting the final
attempt's usage. Either set `max_retries=0` and let Arno's own retry create a span per attempt, or
record the gap. Streaming has a similar edge: OpenCode reports cost on the event that carries final
usage, so a stream cancelled before that event has no reported charge and must read as unknown or
partial, never as zero. Both are implementation decisions, not defects.

Privacy is unchanged by this design: the new attributes are numbers and short enum labels, never a
provider response body or statement text. Content capture is the existing
`include_content=True` setting and stays a separate decision. Retention defaults to 0 days
(infinite); any policy deletes traces and cascades `span_costs`, so a retention window silently
truncates the only long-horizon cost history. Phoenix's database and volume are outside the ledger
backup: the `backup` service dumps only `arno`, so Phoenix cost history is not recoverable state.

## Findings

Phoenix 21.0 cannot meet the acceptance contract. It ignores supplied cost, prices from a manifest
it updates on its own schedule, matches providers by an exact string that Arno's generic OpenAI
provider defeats, reports unknown as `$0`, keeps no price revision, and double counts a nested LLM
span. The reported charge and allowance exist per response, so the gap is delivery and display, not
measurement.

Supplied-cost precedence is necessary but not sufficient. Explicit provenance, partial totals,
unknown counts across every supported rollup, and versioned corrections also need backend and UI
contracts. Duplicate span ingestion currently does nothing, so replay alone cannot revise an
existing cost. A migration or explicit correction API may be needed; four edits and one badge are
not proven sufficient. A custom model entry does not meet acceptance. The authoritative proposed
scope is the [implementation plan](../../arno/cost-tracking.md#focused-phoenix-change), which requires a
qualified pinned service build rather than waiting for an unspecified upstream release.

## Evidence gaps

No Phoenix instance ran and no span was ingested, so the gate is a design, not a result.
`opencode.allowance` versus `list_cost` equality, and whether the generic OpenAI chat path preserves
the `opencode` usage object at all, are unresolved. Resolution for the first is one owner-run
request's usage object or a Console reading after a known call count; for the second, a unit test
that parses a saved response body through `OpenAIChatModel` and, if the object is dropped, a thin
model subclass in `agent/` that copies it into `provider_details`. Whether upstream accepts #15240,
and in which release, is unknown; the gate is the only reliable answer.
