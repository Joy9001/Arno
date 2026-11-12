# LiteLLM: Price catalog, SDK, and gateway

Research note for Arno, November 12, 2026. Scope: LiteLLM as a standalone price catalog, embedded
Python SDK, and self-hosted gateway. It does not decide whether the OpenCode Go subscription may be
used; a sibling note owns that. No live model call, proxy, database, or Redis instance was started.
Claims separate observed source behavior from vendor documentation and inference.

## Artifacts inspected

The following snapshots were inspected.

| Artifact | Version / commit | Size | SHA-256 | Models |
|---|---|---|---|---|
| Catalog at `main` | `89149b4544e056d52b46774bf5723b2ac57c385a` | 4,037,663 B | `4D9F67AB0593C98C989B222D5290508E58299C01FAA746517037DFA5B8794088` | 5,917 models |
| Catalog at latest release | `v1.109.0`, `55c9c6af17a9acc4b46e707e8761c4592d0e0954` | 3,176,369 B | `8F8060D8CE7A40C91B995C74D6A27657543D49E3C1C53430DABD556F244392C6` | 4,551 models |
| Bundled backup at `main` | `litellm/model_prices_and_context_window_backup.json` | 4,037,663 B | Same as `main` catalog | Same |

`main` reports 1.110.0; the latest release is v1.109.0 (2026-11-11). There is no `generated_at`;
provenance is per row plus a runtime `source_revision`, a git blob id ([cost
map](https://docs.litellm.ai/docs/proxy/model_cost_map), [source
sync](https://docs.litellm.ai/docs/proxy/sync_models_github)).

## Catalog provenance and updates

Observed in `main`: 4,493 of 5,917 models carry a `source` URL and 1,424 do not; 3,556 chat rows,
985 image, 443 video, 206 responses, 157 embedding, 113 transcription, 81 speech. Of chat rows, 140
lack `input_cost_per_token` and 78 have both token rates explicitly at zero. Price categories: 56
rows use `tiered_pricing`, 586 have a cache-write base rate, 258 a one-hour rate, 12 a twelve-hour
rate, 600 a batch input rate, 525 a search-context query rate, and 77 a separate reasoning-output
rate.

`main` adds 1,366 rows over v1.109.0, mostly image and video providers; both contain the 30
`opencode_go` rows. Refresh cadence is unpublished. Cost-map pull requests come from bot branches
(`litellm_cost_map_sync_*`, `litellm-providers/price-sync*`), and a guard requires the bundled
backup to match the root file and a sync branch to only add or update models
([`cost_map_guard.py`](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/ci_cd/cost_map_guard.py)).

At startup LiteLLM fetches raw GitHub `main`, validates it, and silently falls back to the bundled
backup on failure
([`get_model_cost_map.py`](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/litellm/litellm_core_utils/get_model_cost_map.py)).
It validates rows and retries a failed fetch; the proxy exposes manual and scheduled reloads. `GET
/model/cost_map/source` reports `source`, `fallback_reason`, `source_revision`, `etag`, and
`model_count` ([custom map](https://docs.litellm.ai/docs/proxy/custom_model_cost_map)). Monitor it:
a fallback reverts pricing without failing health checks.

Pin with the bundled backup (`LITELLM_LOCAL_MODEL_COST_MAP=True`) or a commit-pinned URL (`file://`
is unsupported). A partial `model_info` override detaches the deployment: unset long-context tiers
disappear and cache fields come from the default row, not the variant ([custom
map](https://docs.litellm.ai/docs/proxy/custom_model_cost_map#partial-overrides-do-not-fall-back-to-default-pricing)).
The map is per-process and refreshed only by reload.

## OpenCode Go coverage

LiteLLM has an `opencode_go/` provider. It selects the upstream protocol from the catalog's
`supported_endpoints` and forwards a supplied `x-opencode-session` ([provider
page](https://docs.litellm.ai/docs/providers/opencode_go)). The entry for our model declares
`input_cost_per_token: 3e-07`, `output_cost_per_token: 1.2e-06`, `cache_read_input_token_cost:
6e-09`, 1M input and 384k output, mode `chat`, only `/v1/chat/completions`, function calling,
reasoning, and vision. Those rates are $0.30 / $1.20 / $0.006 per 1M tokens and are the **peak**
rates. OpenCode publishes $0.15 / $0.60 / $0.003 off-peak, with peak 01:00–04:00 and 06:00–10:00
UTC, Monday–Friday ([Go pricing and limits](https://opencode.ai/v2/docs/console/go/)). LiteLLM's
provider page says the catalog uses the peak rate, so an off-peak request can show a higher estimate
than OpenCode deducts.

The entry omits `supports_response_schema`, `supports_tool_choice`, and `supports_prompt_caching`
despite a cache-read price; missing flags are not evidence of support. Function calling is
advertised; tool-call semantics, native JSON-schema output, and cache-write accounting still need a
real request.

The response carries `usage.opencode.list_cost`; the adapter reads it. LiteLLM's code says
`list_cost` is the list price including peak and long-context tiers, while `usage.cost` is what hit
the prepaid balance and is zero inside a plan
([`core_helpers.py`](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/litellm/litellm_core_utils/core_helpers.py),
[`opencode/common_utils.py`](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/litellm/llms/opencode/common_utils.py)).
OpenCode says each response reports `opencode.allowance`, "how much the request counted" toward the
monthly per-model limit. LiteLLM reads `list_cost`, not `allowance`. Whether they differ is
unresolved because no response was captured. The plan is an allowance, not per-request cash: Go is
$10/month with a $100 monthly allowance for DeepSeek V4.1 Flash; LiteLLM budgets do not mirror it.

A custom endpoint can use `openai/<model>` plus `api_base`; `model_name` is the client alias and
`litellm_params.model` the upstream ID. If the routing provider differs from the pricing provider,
set `model_info.pricing_provider` or `base_model`; otherwise cost lookup can land on $0 ([custom
pricing](https://docs.litellm.ai/docs/proxy/custom_pricing#set-pricing_provider-when-the-routing-provider-and-the-pricing-provider-differ)).

## How cost is computed, and what "actual" means

LiteLLM reads usage and multiplies token counts by row rates. Cache reads, cache writes, reasoning
output, audio, image, video, search queries, and long-context or service-tier variants each have a
key; absent keys fall back in defined ways ([cost
map](https://docs.litellm.ai/docs/proxy/model_cost_map)). Tiered pricing is all-or-nothing: one tier
is chosen from prompt tokens and every token is billed at that tier, not sliced into graduated
bands; long-context rows use `_above_200k` or `_above_272k` keys
([`tiered_pricing.py`](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/litellm/litellm_core_utils/llm_cost_calc/tiered_pricing.py)).
Cache TTLs are separate keys: the base cache-write rate covers 5- and 30-minute writes, while
`_above_1hr` and `_above_12hr` are distinct; a row without the key prices a write at zero. Batch and
service tiers use `_batches`, `_priority`, `_flex`, and `_ultrafast`; audio/STT use token or
per-second keys; search uses per-query keys.

The result is a USD float, a list-price estimate unless the provider reports a cost. LiteLLM uses
provider-reported cost first: OpenRouter's `usage.cost`, xAI's `cost_in_usd_ticks`, and OpenCode's
`list_cost` ([provider usage
fields](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/CONTRIBUTING.md)).
For OpenCode that is list price, not cash or allowance.

The number appears in `x-litellm-response-cost` and `x-litellm-key-spend` headers, in
`LiteLLM_SpendLogs.spend`, and in streaming chat/responses in the final `usage` object. The
`/v1/messages` and pass-through routes need `include_cost_in_streaming_usage` (since v1.110.0, so
absent from v1.109.0) ([response headers](https://docs.litellm.ai/docs/proxy/response_headers),
[streaming usage](https://docs.litellm.ai/docs/completion/usage)). Component cost headers are
non-streaming only.

The PydanticAI boundary is where this fails to propagate. `ModelResponse.cost()` uses
`genai-prices`, not LiteLLM. `RequestUsage.extract()` reads only `usage.logfire_ai_gateway.cost` or
`usage.pydantic_ai_gateway.cost_estimate`; the OpenAI model's `_map_usage()` keeps selected integer
details and does not map a nested `opencode` object
([`messages.py`](https://github.com/pydantic/pydantic-ai/blob/cde1d10f88ce6d0ab4743e226d51a9ac65810f50/pydantic_ai_slim/pydantic_ai/messages.py),
[`_gateway_cost.py`](https://github.com/pydantic/pydantic-ai/blob/cde1d10f88ce6d0ab4743e226d51a9ac65810f50/pydantic_ai_slim/pydantic_ai/_gateway_cost.py),
[`models/openai.py`](https://github.com/pydantic/pydantic-ai/blob/cde1d10f88ce6d0ab4743e226d51a9ac65810f50/pydantic_ai_slim/pydantic_ai/models/openai.py)).
It never reads `x-litellm-response-cost`. `genai-prices` 0.2.1 has no OpenCode provider entry, so
the model is unpriced there.

Arno's `SpendLimits` therefore does what its default says: `on_unpriced='zero'` counts an unpriced
response as free and warns once per model ([harness
source](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/src/pydantic_ai_harness/pydantic_ai_harness/spend/_capability.py)).
The $0.02-per-turn figure in [`system-design.md`](../../arno/system-design.md) is an assumption. At
peak rates, 2,000 uncached + 8,000 cached input + 500 output is about $0.00125; off-peak, $0.00062.
$0.02 is a much larger turn.

## Budgets, spend control, and durability

Plain key, user, and team budgets are open source; Enterprise lists "Budgets and rate limits" under
the open-source gateway. Tag budgets, model-specific budgets per key, temporary increases,
soft-budget alerts, and spend reports are Enterprise features
([Enterprise](https://docs.litellm.ai/docs/enterprise)). One key with an open-source `max_budget`
and `budget_duration` is enough.

The proxy has a pre-call budget reservation in MIT code, on by default when a budget is set. It
reserves input cost plus `min(requested max_tokens, model max_output_tokens)` output cost against
each counter, then reconciles to actual
([`budget_reservation.py`](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/litellm/proxy/spend_tracking/budget_reservation.py)).
Without an output cap it reserves 16,384 output tokens. That is a concurrency bound, not a hard cap:
an uncapped response can emit up to 384k output tokens, so one request can cross the remaining
budget by more than its reservation. Cancellation reconciles to input cost; failed requests release
their reservation. Fallback targets get a separate read-only budget check, without a reservation;
that check covers only selected budget scopes. Concurrent fallbacks can therefore cross a cap
([fallback
source](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/litellm/proxy/auth/fallback_budget.py)).
Neither path establishes a strict maximum charge.

Failure modes differ. A cost-map fetch failure silently uses stale bundled prices. A database outage
is not fail-open unless `allow_requests_on_db_unavailable: true` is set, and that is documented only
for a non-public VPC ([production](https://docs.litellm.ai/docs/proxy/prod)). A budget-counter
failure skips the counter by default; `fail_closed_budget_enforcement` returns 503 instead. That
flag is source-observed, not documented on the pages read. Spend updates use an in-memory queue
capped at 1,000 entries ([spend
queue](https://docs.litellm.ai/docs/troubleshoot/spend_queue_warnings)). A crash can lose recent
rows; the counters gate requests. The capture-rate page describes OpenAI-only reconciliation and
also labels it an upcoming-release feature. Do not assume it exists in the selected stable image
without checking that release ([capture
rate](https://docs.litellm.ai/docs/proxy/spend_capture_rate)).

Retries and fallbacks record the served attempt and budget-check fallback targets, but do not prove
the provider did not bill a failed first attempt. There is no idempotency key in chat; a timeout
after generation can be billed twice while LiteLLM logs one success.

## Deployment, semantics, privacy

Minimal shape: one process plus Postgres. Without a DB there is no key management, spend tracking,
or UI persistence; auth falls back to the master key. Redis is optional for one worker; without it,
budgets and rate limits are per process ([deploy](https://docs.litellm.ai/docs/proxy/deploy)). One
worker makes that global; more workers need Redis to share it.

On paper, the chat path is ordinary OpenAI Chat Completions, which is what PydanticAI's OpenAI model
expects. The provider supports function calling and reasoning but does not declare native
response-schema support; structured output is untested.

The gateway sees every prompt and response. Content is not written to spend logs by default;
`store_prompts_in_spend_logs` opts in, and `turn_off_message_logging` keeps content out of callbacks
while retaining spend ([logging](https://docs.litellm.ai/docs/proxy/logging)). Debug logs can
include payloads. Auth is a master key or database-backed virtual keys, another secret; bind to
`127.0.0.1`.

Arno already emits native PydanticAI spans to Phoenix. LiteLLM's `otel` callback creates a second
span tree unless `traceparent` is forwarded; its sample proxy span has a null parent. Phoenix
correlation, duplicate token counts, and the proxy's cost attribute were not tested.

## Licensing is the hard gate

The root license is MIT, but `enterprise/` is under a BerriAI Enterprise license. The supported
proxy extra declares `litellm-enterprise` (0.1.82 at v1.109.0, 0.1.83 at `main`). Its
enterprise-route imports are guarded by `try/except ImportError`, so enterprise-free operation is
plausible but was not established by this research ([proxy
source](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/litellm/proxy/proxy_server.py#L1217)).
PyPI says `LicenseRef-Proprietary`; the enterprise license restricts production use to a paid
subscription ([PyPI](https://pypi.org/project/litellm-enterprise/),
[license](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/enterprise/LICENSE.md)).
The base SDK, catalog, and substantial proxy code are MIT. The standard `litellm[proxy]` dependency
set includes the separately licensed package. This is a deployment and license-audit gate for both
production use and distribution, not proof that every community proxy feature needs an enterprise
license. A supported enterprise-free assembly was not tested.

## Catalog only versus embedded SDK versus gateway

The three integration levels have different responsibilities.

| Criterion | Catalog only | Embedded SDK | Gateway |
|---|---|---|---|
| What you get | JSON rows and rates | `completion`, `completion_cost`, router/fallbacks | OpenAI-compatible server, keys, budgets, spend logs |
| Cost source | Your code over rows | Usage or LiteLLM calculator | Same plus provider-reported values |
| Admission control | None | None from the price calculator | Reservations and spend counters; not a strict charge cap |
| Extra state | None | In-process | Postgres; Redis optional |
| Licensing | MIT file | MIT base | Proxy extra pulls proprietary `litellm-enterprise` |
| PydanticAI fit | Custom `price` function | Custom model adapter | Works as base URL; cost does not propagate |

The SDK's `response_cost` and `completion_cost()` are real, but the SDK does not enforce a budget.
The gateway refuses from a shared spend counter; `SpendLimits` gates from its own local price and
counter, and the gateway's refusal is bounded by its reservation estimate.

## Go/no-go criteria

**Catalog only: go, with conditions.** Vendor the v1.109.0 file or a pinned commit, record the
SHA-256, map `opencode_go/deepseek-v4.1-flash` explicitly, and feed a `SpendLimits.price` function.
Use `on_unpriced='raise'` in tests. It enforces no hard budget and reconciles nothing with OpenCode.

**Embedded SDK: conditional.** Only if Arno needs provider translation, router, or fallback
in-process. It does not solve budgeting and adds boto3, huggingface-hub, tokenizers, and tiktoken to
the API process; a custom PydanticAI adapter would be required.

**Gateway: not ready to adopt on current evidence.** The standard proxy extra includes a
nonpermissive dependency, and the pricing path does not establish subscription-allowance parity.
Gateway state could use a separate database in the existing Postgres cluster; it does not inherently
require another Postgres server. Its database, secrets, configuration, and any authoritative
counters would need explicit backup and restore coverage. Arno would also need to choose which layer
owns admission control rather than maintain two dollar ledgers with different pricing silently.

Before reversing that, prove:

1. **License:** an SPDX/image audit shows no proprietary component, or the enterprise package can be
   removed and the proxy still runs.
2. **Cost parity:** shadow 100–200 requests and compare `x-litellm-response-cost` and spend logs
   with
   `opencode.allowance` and the Console balance, peak and off-peak; state an error bound.
3. **Streaming:** prove PydanticAI receives a cost without custom code (v1.110.0 body cost), or test
   a wrapper that reads the header.
4. **Budget:** run concurrent requests with a small remaining budget, with and without `max_tokens`;
   measure overshoot; test cancellation, retry, and fallback.
5. **Durability, privacy, semantics:** kill the proxy mid-stream; quantify lost spend and counter
   recovery; restore its DB from Arno's backup; confirm no prompt content in Postgres, logs, or
   callbacks; test tools, structured output, long context, and loopback auth.
6. **Version pin:** a released version and image digest, not `main`; a catalog hash; monitoring of
   `/model/cost_map/source`.

## Strongest objections

- The standard proxy extra includes a nonpermissive enterprise dependency. Guarded imports leave
  an enterprise-free assembly plausible, but its operation and complete license inventory remain
  unverified.
- A subscription's real unit is allowance, not dollars; LiteLLM reads a peak `list_cost`, OpenCode
  exposes `allowance`, and equality is unproven.
- PydanticAI does not consume LiteLLM's cost header or body by default, so the gateway alone does
  not
  make Arno's cost accurate.
- Gateway configuration and durable spend state expand the backup boundary; the in-memory queue
  requires crash testing. A database in the existing cluster is feasible, but is not automatically
  covered by Arno's current dump. Reservations remain estimates, and uncapped output can exceed
  the 16,384-token hold.

## Unknowns

- Whether `opencode.allowance` equals `list_cost`, and whether either includes the plan discount.
- Whether usage and allowance appear on every streaming chunk and match the Console.
- Whether structured output / `response_schema` works through the native provider.
- Whether the proxy runs without `litellm-enterprise`, whether manual reload reaches every worker,
  and whether failed-attempt billing is captured; provider billing is the only authority.

## What was not tested

No live proxy, database, Redis, or OpenCode call; no streaming, tool-call, structured-output,
vision, STT, cancellation, retry, fallback, concurrency, crash, backup/restore, or Phoenix test; no
invoice reconciliation; no license audit beyond PyPI and repository files; no custom map, S3,
offline, or Windows proxy run; no attempt to install or remove `litellm-enterprise`.

## Sources

Primary, accessed November 12, 2026:

- [Model cost map](https://docs.litellm.ai/docs/proxy/model_cost_map)
- [Custom cost map](https://docs.litellm.ai/docs/proxy/custom_model_cost_map)
- [Custom pricing](https://docs.litellm.ai/docs/proxy/custom_pricing)
- [Auto sync and provenance](https://docs.litellm.ai/docs/proxy/sync_models_github)
- [OpenCode Go provider](https://docs.litellm.ai/docs/providers/opencode_go)
- [OpenCode Go plans and rates](https://opencode.ai/v2/docs/console/go/)
- [Response headers](https://docs.litellm.ai/docs/proxy/response_headers)
- [Streaming usage cost](https://docs.litellm.ai/docs/completion/usage)
- [Cost tracking](https://docs.litellm.ai/docs/proxy/cost_tracking)
- [Spend capture rate](https://docs.litellm.ai/docs/proxy/spend_capture_rate)
- [Spend queue warning](https://docs.litellm.ai/docs/troubleshoot/spend_queue_warnings)
- [Production and DB/Redis](https://docs.litellm.ai/docs/proxy/prod)
- [Deployment](https://docs.litellm.ai/docs/proxy/deploy)
- [Reliability and fallback budgets](https://docs.litellm.ai/docs/proxy/reliability)
- [Virtual keys](https://docs.litellm.ai/docs/proxy/virtual_keys)
- [Enterprise feature split](https://docs.litellm.ai/docs/enterprise)
- [Logging and redaction](https://docs.litellm.ai/docs/proxy/logging)
- [Catalog at main, commit 89149b4](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/model_prices_and_context_window.json)
- [Catalog at v1.109.0, commit 55c9c6a](https://github.com/BerriAI/litellm/blob/55c9c6af17a9acc4b46e707e8761c4592d0e0954/model_prices_and_context_window.json)
- [Cost map loader](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/litellm/litellm_core_utils/get_model_cost_map.py)
- [Cost map guard](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/ci_cd/cost_map_guard.py)
- [Budget reservation](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/litellm/proxy/spend_tracking/budget_reservation.py)
- [OpenCode adapter](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/litellm/llms/opencode/common_utils.py)
- [Provider-reported cost helper](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/litellm/litellm_core_utils/core_helpers.py)
- [Tiered pricing](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/litellm/litellm_core_utils/llm_cost_calc/tiered_pricing.py)
- [Root license](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/LICENSE)
- [Enterprise license](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/enterprise/LICENSE.md)
- [`litellm-enterprise` on PyPI](https://pypi.org/project/litellm-enterprise/)
- [PydanticAI v2.64.0](https://github.com/pydantic/pydantic-ai/tree/v2.64.0)
- [Harness SpendLimits](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/src/pydantic_ai_harness/pydantic_ai_harness/spend/_capability.py)
- Arno: [`system-design.md`](../../arno/system-design.md), [`harness.md`](../../arno/harness.md),
  [`observability.md`](../../arno/observability.md)
