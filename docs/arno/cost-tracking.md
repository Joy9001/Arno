# Model cost tracking and budgets

This file holds the detail behind `plan.md` §3: native pricing, shared spending limits, and
Phoenix as the cost dashboard. The plan owns decisions; this file owns their implementation.

Implemented November 12, 2026, but not deployed to the owner instance. The code, migrations,
tests, and the pinned Phoenix build are in the working tree. Paid-provider calls, a real ChatGPT
sign-in, and owner deployment remain pending. Scratch restore, image, and invariant checks passed;
the [acceptance log](cost-tracking-acceptance.md) records the synthetic evidence and the gaps.

The scope is one owner, one API process, occasional CLI use, and overlapping chat/import/jobs.
Neither this implementation nor its tests authorize paid model calls or changes to the owner
deployment. The design was cut after the
[complexity audit](../research/model-cost-and-gateways-2026/complexity-audit.md); it supersedes the
larger reservation, receipt-service, and export-platform proposal in the earlier research review.

## Outcome and limits

Arno prices every supported inference call once, applies the existing shared budgets, and shows
its usage and cost in Phoenix. It distinguishes a measured zero, an estimate, a partial subtotal,
and an unknown. Pydantic AI, genai-prices, native Spend Limits, and the existing Postgres/Phoenix
deployment remain the foundation.

Budgets stop runaways. They are not an unconditional cap on the provider invoice. Native Spend
Limits checks before a request and records after its response; an in-flight call can cross the
limit, and concurrent calls can each pass the check. Preserve that contract, bound request/output
sizes where supported, and test the overshoot behavior. Do not build a reservation engine to
promise a stronger guarantee the product did not ask for.

Phoenix remains the only cost dashboard. A small call journal preserves cost observations for
export recovery; it is not another budget ledger or reporting UI. Standard tracing remains best
effort: after an outage or crash, cost metadata can be replayed, but lost prompt/tool content
cannot be reconstructed. Unknown provider usage cannot be turned into an exact charge by replay.

## Supported providers and subscriptions

Arno implements and tests the configured profiles, rather than making every provider family a
release prerequisite. Existing endpoint configuration still works. Another adapter needs a real
usage problem, not catalog completeness.

| Profile | Pricing and access |
|---|---|
| Known API provider | Native Pydantic AI adapter and installed genai-prices; preserve actual endpoint/provider and served model |
| Anthropic | Native `AnthropicModel` with a Console API key; eligible plan-funded API credits use this same route |
| OpenCode | Narrow adapter preserving reported charge, list value, billing source, allowance, and estimate flag |
| Explicit local Ollama/speech | Zero provider inference fee; tokens, duration when known, and call count remain visible |
| Eligible ChatGPT plan | Native `openai-chatgpt` app sign-in provider; API-equivalent estimate, not a remaining-quota counter |
| Other compatible endpoint | Keep wire compatibility; require `reported` billing or a verified identity before enabling paid inference |

Each role reads `*_PROVIDER` and `*_BILLING_MODE`, plus the existing `*_BASE_URL`, `*_API_KEY`, and
`*_MODEL`. The billing modes are `metered` (default), `local` (with `local_zero` as an alias), and
`reported`; there is no subscription mode. A provider defaults to `openai`, with `opencode` and
`ollama` or a catalog-recognized vendor inferred from that role's own URL when unset. A role is
configured only when it has a model and either
a real key, a native ChatGPT sign-in, or an explicit local mode; a loopback URL alone proves
nothing. `reported` is an explicit contract that the endpoint returns its own charge.

The existing optional `command-a-plus-05-2026` fallback is missing from the installed catalog.
Cohere documents a zero API tariff for that exact model for trial and production keys until rate
limits. Arno records that narrow endpoint/model exception as `published_rate`, revision
`cohere-command-a-plus/2026-11-12`, with zero input/output rates. It is an estimate from a published
rate, not a catalog miss treated as free; another model or reseller does not inherit it. See the
[official model pricing](https://docs.cohere.com/docs/command-a-plus). Provider rate limits still
apply, and this is not a quota counter.

Local zero requires the explicit billing setting, not a loopback URL or a catalog miss. It
excludes hardware, electricity, and hosting. Unknown pricing is never silently classified as free.

Eligible Anthropic plan credits can fund API calls through a linked Console organization. Ordinary
paid API keys work regardless of credit eligibility. The official Agent SDK subscription route is
real but runs another agent loop; it stays
[parked](parked.md#direct-claude-agent-sdk-subscription-runtime). Do not extract subscription OAuth
tokens or impersonate a coding client. The
[Anthropic research](../research/model-cost-and-gateways-2026/anthropic-follow-up.md) owns the
source and eligibility detail.

Use ChatGPT sign-in only for supported local or owner-controlled-host arrangements, following the
official VM guide where applicable. The native credential source stores the tokens in
`model_credentials`, which has no `agent_ro` grant; refresh is a compare-and-swap on the stored
refresh token, so a disconnect cannot be undone by an in-flight rotation. Each request checks that
the row still exists and matches the cached access token, and background jobs, wake turns, and
delegated work additionally need the owner's `--allow-background` consent. Quota failure never
selects a paid API fallback; a ChatGPT primary refuses an automatic fallback at construction.
`arno chatgpt-login --manual --no-browser` accepts the full callback URL privately and validates
its state, so a container needs no port 1455 publish; the normal host flow listens on
`127.0.0.1:1455`. Credit balance, remaining plan quota, and final invoice stay unknown unless a
documented source provides them.

## One pricing function

`agent/pricing.py` is the one pricing function. `observe_cost` returns a `CostObservation` with
normalized usage, an exact Decimal USD value, nullable provider charge and allowance,
completeness, source, price revision, and effective rates. It also records provider/model identity.
Paise stay in the ledger; amounts are USD and are never rounded to cents. Native spend counters
compare at their own precision.

The shared dollar budget uses usage value: reported pre-credit/list value when available,
otherwise a clearly labeled catalog estimate. Included credits do not erase consumed usage. For
OpenCode Go, display wallet charge and allowance separately from list value; do not add them or
assume they are equal. Subscription API-equivalent value is not the account's real quota debit.

Use these sources in order:

1. Documented provider-reported usage value for the selected billing mode, including OpenCode's
   narrow reported fields.
2. Native genai-prices for the correct provider, served model, timestamp, and supported tier.
3. Explicit local zero.
4. Unknown with a useful reason. Never substitute a different vendor's rate.

Preserve cache read/write categories and output that already includes reasoning without double
counting. Validate finite nonnegative amounts and supported billable units. A known subtotal can
be partial; an authoritative total does not require an invented input/output split.

Keep the installed genai-prices snapshot and pinned lockfile. Record its package version and the
selected effective rates with each call. Refresh by dependency update; do not add a catalog mirror,
background downloader, rate-activation protocol, or new stale-price policy. Prices may lag until
upgrade; estimates are not invoice guarantees. Record the revision so an upgrade cannot silently
reprice earlier observations.

Pass the pricing result into native `SpendLimits(price=..., on_unpriced="raise")`; the callback
returns `Decimal | None`, not the structured result. Preflight configured paid profiles for
pricing identity and supported units. `on_unpriced="raise"` is a last defense after a call, not
proof that an unknown-price request was prevented. Unexpected missing usage stops that run and
is recorded as unknown/partial, never as a successful zero-cost call.

The price callback must not mark a partial subtotal complete or fall through to a misleading
catalog value. For genuinely missing usage, preserve native `unmeasured_requests` and the known
`usage.cost` subtotal so the native incomplete-usage path records it and raises. Test this
separately from a missing price; both must remain visible in the journal and Phoenix.

OpenCode fields must survive native SDK mapping. First test the installed model/SDK with synthetic
nonstreaming and terminal-stream usage objects. Only if fields are dropped, preserve allowlisted
metadata through the public HTTP response/stream extension and attach it to `provider_details`.
Keep parsing narrow to the API families actually configured; do not retain raw provider bodies.
See the [wire contract](../research/model-cost-and-gateways-2026/opencode-accounting-contract.md).

OpenRouter amounts, if configured, come from `provider_details`; preserve zero by field presence.
Native ChatGPT URL-first lookup can already resolve OpenAI catalog prices. Keep transport,
billing, and pricing identities distinct without replacing working native resolution.

## Native budgets and direct calls

`SpendLimits`, `PostgresSpendStore`, and the existing counter keys remain. Every construction
site, including compaction, goes through `spend_limits` and `shared_daily_budget`: $5 per
interactive run, $20 per shared UTC day, and the saved scheduled-question limit, default $1.
Midnight UTC remains 05:30 IST. The shared day uses `retain="forever"`, so a delayed batch
correction cannot resurrect an expired counter; replay tokens are remembered for seven days.
Compaction and child-agent budget scopes keep their meaning.
The $5 cap applies to its native agent run; delegated runs and compaction still consume the shared
day under their existing scopes. It is not a new end-to-end task cap.

The old `_UnavailableStore`, uncapped `SharedSpendStore` exception fallbacks, bootstrap returning
an uncapped store, and `spend_store_scope` continuing without one are gone. Database failures stop
paid inference and report the failure; read-only pages remain independent. A failed counter write
is never swallowed or followed by an automatic model retry, because a
committed-but-unacknowledged write has an uncertain outcome. `SpendStoreError`,
`SpendLimitExceeded`, the native `UserError`, and `UsageLimitExceeded` map to the existing
nonretryable job outcome
(`JobGaveUp`), rather than rerun an already-spent model call through job retries.
These stops also cross the Monty parser and Code Mode boundaries unchanged, even if a script
catches an external-call error; another tool or model request cannot turn them into a paid retry.

Native request/tool/token limits remain, and metered requests get an 8192-token output cap unless
the caller sets its own `max_tokens`. The native ChatGPT provider does not support that cap and is
left unchanged. An endpoint rejecting output caps does not acquire an invented mathematical
bound; its normal post-response budget behavior applies. There are no configurable reservation
profiles, leases, provider-wide indefinite locks, or owner reconciliation workflow for ordinary
calls.

Raw STT and other non-agent calls use one helper in `agent/spend.py` over public APIs:
`SpendLimits.status`/`exhausted` for checks, `BudgetStatus.key` for resolved keys, and
`BatchSpendStore.add_many([SpendEntry(...)])` for posting the priced result. It uses the same
pricing function and counter store, not another balance. Run-scoped budgets need a real run
context; standalone capture uses the shared day. Private harness key formats are never
reconstructed.

Preserve the existing audio upload format and byte limits. Read documented provider token/duration
usage and any trustworthy duration already available. If neither reported units nor a defensible
estimate is available, show unknown and stop the run; do not fabricate duration. No mandatory
browser WAV conversion, media decoder, or audio-format rewrite belongs in this cost feature.
Explicit local STT still has zero provider fee, with unknown duration shown as unknown duration.

## Batch accounting

Batch work needs a small exception to response-time charging because submission can spend before
collection. `categorize_runs.accounting` holds only what recovery needs: the estimate and its USD
value, the original counter keys, stable estimate/settlement tokens, item count and model identity,
and whether each phase was applied.

Before the first batching `agent.run`/enqueue, `submit_batch` resolves the shared day and compares
a bounded batch estimate with remaining spend. It persists the posting intent, then posts that
estimate to the same native counters before allowing enqueue. A batch that cannot be priced with
the configured item/output limits is refused. Count/byte/timer thresholds can auto-submit before
`flush()`. `max_resubmissions=0` is set on creation and resume; explicit retries need another
budget check.

This is pre-accounted estimated spend, not an atomic reservation API or a guarantee against
concurrent overshoot. The real provider job ID is resolved after acknowledgement. Submission and
Postgres are separate systems: an acknowledgement gap pauses that batch for inspection, never
blindly resubmits it. Any saved posting intent blocks another submission in that run; an
uncollected or ambiguous estimate also blocks a new run for the same import. A short import-row
lock serializes intent creation, not provider execution. A failure through submission or the
acknowledgement transaction is terminal for the job, leaving the estimate provisional. No
distributed transaction protocol is involved.

On collection, post `actual - estimate` once to the saved keys and record actual per-item costs
in traces. A negative correction is supported by public `SpendEntry.usd`. It is not an invoice
reconciliation system. Do not also let a collect/decode capability charge the full actual amount.
Unknown results keep the conservative estimate marked provisional; never refund unknown work.

The native replay marker defaults to one hour, and daily counters normally expire after 48 hours.
Those defaults do not protect a delayed batch. The shared day is nonexpiring for every writer, and
a seven-day deduplication horizon covers the supported batch recovery period. Stable
estimate/settlement tokens and collection state live in the existing batch row. Within that
horizon, uncertain settlement writes can retry with the same token; outside it, settlement pauses
rather than risk another delta. Collected answers are saved before settlement, so a transient store
failure requeues only settlement from saved answers and never dispatches provider work. A posting
intent does not authorize submitting the provider work again. The tests exercise the crash gap
between posting and saving the applied
marker. Nothing claims an atomic transaction between the native store and the batch row. One
daily row per owner is small; no retention service is needed.

## Every call and its trace

Coverage is chat API/CLI, summary/fallback, parser and its spike command, categorizer, statement
vision, photo, transcription, briefing, scheduled questions, batch items/collection, eval trials,
and eval judges. Paid evals remain opt-in. Eval trial and judge counters stay isolated in memory;
their cost journal rows are routed to the installation database before the disposable financial
clone is dropped, and ChatGPT credentials always come from the installation database. The
[source inventory](../research/model-cost-and-gateways-2026/implementation-seams.md) is a
starting point; scan again for direct SDK/HTTP/model/batch calls before declaring coverage.

`MeteredModel` wraps a concrete provider model beneath the native instrumentation, before fallback
models are assembled. It records at dispatch in `request`/`request_stream`, not on wrapper entry
or suspended cancellation. Hidden SDK inference retries are disabled. A failed first attempt keeps
its own unknown row even when a later fallback succeeds. Cancellation and a missing terminal usage
event leave a visible partial/unknown result, not an assumed zero.

A native model span can enclose several attempts. It stays for context and is marked as a logical
request; child attempt spans carry cost once. STT uses `metered_call`; batch items retain their
attempt IDs from submission and export finalized cost spans after collection. Native batch
submission and collection spans are marked logical so they cannot duplicate those costs. Actual
model/provider, modality, and task/session/job links are preserved. Collecting a saved batch is not
another billable inference. Costs are never added to
parent agent/tool spans.

`llm_calls` (migrations 0025, 0029, and 0030) is the small application-owned journal:
attempt/trace/span
IDs, workload/profile/model, time, normalized usage, amount/source/completeness, effective rates,
and export status. An intent is written before dispatch and finalized before the attempt span
closes. The journal retains the original Phoenix project and opaque conversation ID so replay
keeps attribution. Interrupted intents remain pending/unknown until their run is known finished; a
charge is never inferred. The table has no `agent_ro` grant, no API export, and no budget windows,
allocations, leases, or invoice states. Native counters remain the only budget authority. Writes
are not claimed atomic with native capability accrual; a persistence error fails the run and
leaves a visible gap.

Export uses standard Phoenix `register(batch=True)` and one `BatchSpanProcessor`; there is no
custom span processor, HTTP/protobuf transport, receipt endpoint, or per-call acknowledgement
loop. The journal holds only cost/identity metadata, never prompts, answers, documents,
credentials, or raw responses. Normal native spans retain their content-capture policy.

The `cost_replay` job runs every five minutes, and `arno cost-replay` runs the same recovery by
hand. Rows finalized more than five minutes ago are re-exported through the standard OTLP exporter
with their original trace and span IDs. Phoenix keeps one cost for a duplicate span ID. Replay
delivers an immutable observation; it does not edit a cost or reconstruct full traces. Standard
exporter success is transport acceptance, not per-span proof; rows stay for inspection and manual
replay, and export failures remain visible. Crashproof OTel delivery is not promised, and a
partial response is never marked complete. An uncertain transmission is safe to replay.
`arno cost-replay --finalize-interrupted` marks unfinished ordinary calls as unknown before
replaying them; provider batch items remain pending for collection. Stop API and CLI inference
first: an intent has no completion signal, and finalizing it does not rebill anything. An
unfinished intent is never replayed over a still-running request. The five-minute grace gives
full native spans time to arrive first. Normal export has no per-span acknowledgement, so recovery
also sends observations that may already be present; Phoenix ignores their duplicate IDs and
retains the full span. A replay cannot replace an existing span or revise its cost.

A Phoenix outage does not bypass budgets or stop finance operations solely for telemetry. The
journal keeps recorded costs recoverable. Explicit `OTEL_SDK_DISABLED` remains respected: do not
export automatically, and make the missing-Phoenix-history limitation visible. Back up the journal
and native counters with Arno; document Phoenix database backup/retention separately.

## Focused Phoenix change

Stock Phoenix 21.0.0 ignores supplied cost and can aggregate unknown as zero. Arno pins and builds
`arno-phoenix-cost:21.0.0` from [`deploy/phoenix-cost`](../../deploy/phoenix-cost/README.md)
until an upstream release passes the same contract. The patch is limited to the gaps below; it is
not an inaccurate custom model-rate workaround. Phoenix's separate Elastic License 2.0 source and
notice stay in that image, never in the permissively licensed Arno image. See the
[Phoenix source investigation](../research/model-cost-and-gateways-2026/phoenix-cost-contract.md).

The patch changes only:

- Prefer valid supplied `llm.cost.*`, including explicit zero, over catalog recalculation.
- Preserve source, completeness, billing mode, and rate revision as span attributes. A supplied
  estimate is still an estimate; missing breakdowns do not require invented components.
- Honor explicit unknown/partial and expose unresolved counts with cost totals in the existing
  span, trace/session, project, and time-range views. No unqualified zero or complete total when
  some calls are unpriced. Validate finite nonnegative amounts.
- Exclude logical request parents and collect/decode spans from cost/token totals. Permit actual
  audio inference costs with their real modality, without fabricated language tokens.

Existing cost storage, GraphQL views, and trace attributes are reused. No observation-history
table, custom receipt schema, correction endpoint, hash protocol, invoice-history UI, or audio
dashboard was added. One immutable observation is supplied when the call finishes. Provider
invoices can differ; they are not used to rewrite past trace costs. The build passed synthetic
ingestion and rollup checks, so the patch is not assumed small on faith.

## Implementation order

The feature landed in this dependency order:

1. **Pricing and provider fixtures.** The shared pricing function and configured profiles, with
   fixtures for cache/reasoning units, OpenCode fields and streams, explicit local zero, provider
   identity, missing price/usage, and subscription funding labels.
2. **Native budgets and batch.** Every Spend Limits factory, fail-closed stores, the direct-call
   helper, and batch pre-accounting through the same counters. Tests cover direct calls,
   late/repeated collection, posting gaps, and the expired replay horizon.
3. **Call tracing and minimal recovery.** The journal, concrete-attempt instrumentation, standard
   batched export, and cost-only replay. Tests cover fallback, cancel, failed writes, crash
   intents, tracing off, and replay.
4. **Phoenix supplied costs.** The pinned service build passed synthetic OTLP and GraphQL contract
   tests, then a rendered-view check in a headless browser.
5. **Cutover and acceptance.** Scratch restore, counter preservation through migration, images,
   and financial invariants passed. The acceptance log records the final full-suite result.
   Owner-deployment adoption of pending batches and live-provider parity remain pending.
   Rollback pauses affected inference if an older build cannot understand new batch state;
   migrations are never deleted and inference never resumes against reset counters. No
   counter-store replacement is involved.

The synthetic SDK-to-Phoenix test checks pricing, journal, budget, and dashboard agreement,
including same-ID recovery. Prior research stays evidence, not a mandate to implement the larger
discarded architecture. Live provider parity remains an owner acceptance step.

## Completion checklist

This is the definition of done. Checked items have the evidence recorded in the
[acceptance log](cost-tracking-acceptance.md); unchecked items remain pending.

- [x] Configured providers have validated transport/pricing identities, explicit billing modes,
      and useful setup errors; unconfigured provider families do not become release blockers.
- [x] Native Anthropic and ChatGPT adapters are wired. Credential refresh is a compare-and-swap,
      revocation is checked before each request, background use needs consent, and no automatic
      paid fallback exists. A real ChatGPT sign-in and a credit-funded Anthropic call remain
      pending.
- [x] Pricing fixtures cover reported/estimated/partial/unknown/local-zero values, invalid numbers,
      cache/reasoning units, actual model, supported tiers, and the exact stored rate revision.
- [x] OpenCode metadata survives SDK parsing in nonstreaming and configured stream formats;
      charge, list value, allowance, and estimate flag remain separate.
- [x] All Spend Limits construction sites use the same price callback, unknown policy, and shared
      Postgres store. Request/tool/output safeguards and existing budget scopes are preserved.
- [x] DB-down reads/writes fail the affected inference; no `None`, dropped-write, or in-memory
      fallback silently disables the budget. Store failure cannot trigger a duplicate model call.
- [x] $5/run, $20/UTC-day, and scheduled limits have tests, and the existing daily-ceiling restart
      test still covers persistence. These are runaway brakes, not invoice hard caps; native
      pre-check/post-record overshoot stays documented rather than promised against.
- [x] STT uses the shared direct-call budget helper and real reported/defensible estimated units;
      current upload formats work, and missing usage is not invented.
- [x] Batch estimates are posted before count/byte/timer auto-submit, hidden resubmission is off,
      and collect/decode does not charge again through another capability.
- [x] Repeated/late collection, negative deltas, native counter retention, deduplication expiry,
      commit/marker gaps, and submit-acknowledgement loss cannot silently double-post or resubmit.
- [x] Chat, CLI, summary, parser/spike, category, vision/photo, STT, briefing, schedule, batch,
      eval trial, and judge paths are wired to the shared accounting/tracing policy. Synthetic
      tests cover the paths and cost-stop propagation through sandbox boundaries.
- [x] Fallback and explicit retry show each actual attempt; cancellation/missing usage leaves
      partial/unknown. Native logical parents, tools, and collect spans do not duplicate totals.
- [x] The journal migrations and grants work; the table carries no financial content or
      credentials, is unavailable to `agent_ro`, and is not a second budget authority. Its
      backup/restore preserves finalized and unfinished calls, credentials, counters, and dedup
      tokens.
- [x] Standard batched export keeps Phoenix network work off the turn; same-ID recovery preserves
      one cost, labels lost content, never replays a running intent, and respects disabled tracing.
- [x] The qualified Phoenix build preserves supplied zero/estimates/source/revision and exposes
      unresolved counts across supported totals; unknown is never displayed as free.
- [x] Synthetic OTLP and rendered Phoenix checks cover known/local-zero/partial/unknown,
      fallback, batch, audio, replay, unsplit totals, provider grouping, and unresolved time bins.
      The real SDK-to-Phoenix fixture checks journal, native budget, dashboard, and replay
      agreement. No paid model or screenshot is implied by the rendered DOM evidence.
- [x] Scratch backup/restore preserves journal rows, credentials, counters, and deduplication
      tokens. Migration preserves existing counter totals and an unfinished call. Phoenix
      retention and the exporter's delivery limits are documented.
- [x] The final current-source full suite passes: 1,408 passed, 4 skipped. Financial invariants,
      dependency/license checks, both image builds, and scratch app smoke checks also pass.
      The acceptance log records the evidence and limits.
- [ ] The owner deployment adopts its existing counters and pending batches, verifies credentials
      and provider responses, and accepts cutover/rollback. No owner service or paid provider has
      been exercised by this implementation pass.
- [x] Current plan, harness, observability, platform, self-hosting, system-design, `.env.example`,
      notices, and global checklist agree. Paid model and owner-deployment acceptance remain
      pending until separately authorized and executed; synthetic checks are labeled as such.

## Deferred deliberately

No dedicated gateway, hard reservation service, full catalog mirror, automated invoice
reconciliation, provider quota enforcement, direct Claude SDK runtime, new audio pipeline,
custom Phoenix receipt API/schema, durable full-trace delivery platform, or second cost dashboard.
Revisit each only for a demonstrated need. The
[complexity audit](../research/model-cost-and-gateways-2026/complexity-audit.md) records the cuts
and the safeguards kept. Accurate pricing, shared budgets, complete call coverage, visible
uncertainty, minimal cost recovery, and privacy are required. Paid-provider parity, real ChatGPT
sign-in, and owner-deployment acceptance remain pending; synthetic acceptance does not replace
them.
